"""Tests for InterruptOp — first-class HITL suspend/resume node.

Verifies the suspend/resume protocol at the state-bus level:
    - InterruptOp emits an InterruptEvent to state's _notify_interrupt bus
    - Suspends on state._interrupt_responses[interrupt_id]
    - Caller resolves via state.resume_interrupt(interrupt_id, value)
    - Returns {"response": value, "timed_out": bool, "interrupt_id": str}
    - Timeout path yields {"timed_out": True, "response": None}
    - Outside active run → returns immediately with timed_out=True

Full scheduler integration (frame emission, engine.stream mode=updates
picking up InterruptEvent, run.resume() wiring) lives in the engine
test suite.
"""

import asyncio

import pytest

from operonx.core.ops.flow.interrupt_op import InterruptOp
from operonx.core.ops.graph.graph_op import END, START, GraphOp
from operonx.core.ops.transform.func_op import FuncOp
from operonx.core.states._scratch_var import _reset_state, _set_state
from operonx.core.states.schema import StateSchema
from operonx.core.states.state import MemoryState


def _pass(**kw):
    return kw


def _minimal_state():
    with GraphOp(name="g") as g:
        n = FuncOp(name="n", code_fn=_pass, inputs={})
        START >> n >> END
    g.build()
    schema = StateSchema(g)
    return MemoryState(schema)


class TestInterruptOpConstruction:
    def test_default_timeout(self):
        op = InterruptOp(name="approve")
        assert op.timeout == 0

    def test_custom_timeout(self):
        op = InterruptOp(name="approve", timeout=30)
        assert op.timeout == 30

    def test_declared_outputs(self):
        op = InterruptOp(name="approve")
        assert set(op.outputs.keys()) >= {"response", "timed_out", "interrupt_id"}

    def test_payload_input_declared(self):
        op = InterruptOp(name="approve")
        assert "payload" in op.inputs


class TestSuspendResumeProtocol:
    async def test_resume_delivers_value(self):
        state = _minimal_state()
        events = []
        state.subscribe_interrupt(lambda op, ctx, payload, iid: events.append((op, payload, iid)))

        op = InterruptOp(name="approve")

        async def _resume_soon():
            # Give the InterruptOp a moment to register its future.
            for _ in range(10):
                if state._interrupt_responses:
                    break
                await asyncio.sleep(0)
            (iid,) = list(state._interrupt_responses)
            assert state.resume_interrupt(iid, True)

        token = _set_state(state)
        try:
            resumer = asyncio.create_task(_resume_soon())
            result = await op._interrupt_impl(payload={"plan": "send email"})
            await resumer
        finally:
            _reset_state(token)

        assert result["response"] is True
        assert result["timed_out"] is False
        assert isinstance(result["interrupt_id"], str)
        assert len(events) == 1
        assert events[0][0] == "approve"
        assert events[0][1] == {"plan": "send email"}

    async def test_timeout_path(self):
        state = _minimal_state()
        op = InterruptOp(name="approve", timeout=0.05)

        token = _set_state(state)
        try:
            result = await op._interrupt_impl(payload="anything")
        finally:
            _reset_state(token)

        assert result["timed_out"] is True
        assert result["response"] is None

    async def test_outside_active_run(self):
        op = InterruptOp(name="approve")
        # No _set_state — should return immediately with timed_out=True.
        result = await op._interrupt_impl(payload="x")
        assert result["timed_out"] is True

    async def test_multiple_interrupts_share_bus(self):
        """Two InterruptOps firing in parallel should each get their own future."""
        state = _minimal_state()
        op1 = InterruptOp(name="op1")
        op2 = InterruptOp(name="op2")

        token = _set_state(state)
        try:
            t1 = asyncio.create_task(op1._interrupt_impl(payload="p1"))
            t2 = asyncio.create_task(op2._interrupt_impl(payload="p2"))

            # Wait until both interrupts have registered their futures.
            for _ in range(50):
                if state._interrupt_responses and len(state._interrupt_responses) == 2:
                    break
                await asyncio.sleep(0)

            ids = list(state._interrupt_responses)
            assert len(ids) == 2
            state.resume_interrupt(ids[0], "answer0")
            state.resume_interrupt(ids[1], "answer1")

            r1 = await t1
            r2 = await t2
        finally:
            _reset_state(token)

        assert {r1["response"], r2["response"]} == {"answer0", "answer1"}


class TestResumeAPI:
    def test_resume_unknown_id_returns_false(self):
        state = _minimal_state()
        state._register_interrupt_bus()
        assert state.resume_interrupt("no-such-id", "value") is False

    def test_resume_already_resolved_returns_false(self):
        state = _minimal_state()
        state._register_interrupt_bus()

        loop = asyncio.new_event_loop()
        try:
            fut = loop.create_future()
            fut.set_result("already")
            state._interrupt_responses["x"] = fut
            assert state.resume_interrupt("x", "again") is False
        finally:
            loop.close()

    async def test_bus_lazy_creation(self):
        state = _minimal_state()
        assert state._interrupt_responses is None
        state._register_interrupt_bus()
        assert state._interrupt_responses == {}


class TestObservabilityFilter:
    def test_exclude_stored_on_interrupt_op(self):
        op = InterruptOp(name="approve", exclude=["response"])
        assert op._obs_exclude == {"*": ("response",)}
