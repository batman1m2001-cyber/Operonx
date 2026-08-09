"""Tests for EmitOp — first-class node emitting to state's custom-event bus.

Verifies the emit-side of Phase 2's mode="custom" streaming:
    - EmitOp.core() calls state._notify_custom(op, ctx, channel, payload)
    - No subscriber → payload dropped silently (fire-and-forget)
    - Payload is the resolved input value at runtime
    - Channel is per-op configuration
    - Ctx propagates via _current_op_ctx (V3 tracing plumbing)

Scheduler integration (state binding via ContextVar, engine.stream mode)
lives in the scheduler/engine test suites.
"""

from operonx.core.ops.flow.emit_op import EmitOp
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


class TestEmitOpConstruction:
    def test_defaults_channel(self):
        emitter = EmitOp(name="probe")
        assert emitter.channel == "default"

    def test_custom_channel(self):
        e = EmitOp(name="probe", channel="ui")
        assert e.channel == "ui"

    def test_payload_declared_as_input(self):
        e = EmitOp(name="probe")
        assert "payload" in e.inputs

    def test_no_outputs(self):
        e = EmitOp(name="probe")
        assert e.outputs == {}


class TestEmitOpRuntime:
    async def test_notifies_custom_bus(self):
        state = _minimal_state()
        events = []
        state.subscribe_custom(
            lambda op, ctx, channel, payload: events.append((op, channel, payload))
        )

        emitter = EmitOp(name="probe", channel="ui")

        token = _set_state(state)
        try:
            await emitter._emit_impl(payload={"progress": 0.5})
        finally:
            _reset_state(token)

        assert events == [("probe", "ui", {"progress": 0.5})]

    async def test_no_subscriber_no_error(self):
        state = _minimal_state()
        emitter = EmitOp(name="probe")

        token = _set_state(state)
        try:
            # Fire-and-forget — no subscriber, no exception.
            result = await emitter._emit_impl(payload="anything")
        finally:
            _reset_state(token)

        assert result == {}

    async def test_multiple_subscribers_all_receive(self):
        state = _minimal_state()
        got_a, got_b = [], []
        state.subscribe_custom(lambda *args: got_a.append(args))
        state.subscribe_custom(lambda *args: got_b.append(args))

        emitter = EmitOp(name="probe", channel="c1")
        token = _set_state(state)
        try:
            await emitter._emit_impl(payload="hi")
        finally:
            _reset_state(token)

        assert len(got_a) == 1
        assert len(got_b) == 1

    async def test_outside_active_run_swallows(self):
        """Without an active _current_state_var, EmitOp is a no-op."""
        emitter = EmitOp(name="probe")
        # No _set_state call → ContextVar unbound.
        result = await emitter._emit_impl(payload="x")
        assert result == {}


class TestSubscribeAPI:
    def test_subscribe_then_unsubscribe(self):
        state = _minimal_state()
        events = []
        cb = lambda op, ctx, ch, p: events.append(p)
        state.subscribe_custom(cb)
        state._notify_custom("op", ("main",), "c", "one")
        state.unsubscribe_custom(cb)
        state._notify_custom("op", ("main",), "c", "two")
        assert events == ["one"]

    def test_unsubscribe_unknown_is_noop(self):
        state = _minimal_state()
        state.unsubscribe_custom(lambda *a: None)  # never registered — no raise


class TestObservabilityFilter:
    """EmitOp inherits @op(exclude/include/observe_max) via BaseOp."""

    def test_exclude_stored_on_emit_op(self):
        e = EmitOp(name="probe", exclude=["payload"])
        assert e._obs_exclude == {"*": ("payload",)}

    def test_observe_max_stored(self):
        e = EmitOp(name="probe", observe_max=100)
        assert e.observe_max == 100
