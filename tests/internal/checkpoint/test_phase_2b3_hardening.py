"""Phase 2b3 hardening — tests locking in the fix for each identified issue.

Covers:
    - H1: push-ref forwards source's post-reducer stored value
    - B2: per-observer exception guard — peer observers still fire, first
      BaseException re-raised
    - B3: engine.stream(mode=X) cancels the scheduler on caller break
    - H3: engine.run finally drains state._interrupt_responses
    - T1: mode="custom" E2E — EmitOp payloads reach engine.stream consumers
    - T2: sample_every via engine.run
    - T3: ObserveBudgetExceeded is BaseException and skips `except Exception:`
    - T4: handle.cancel() with pending InterruptOp
    - T5: on_cancel invoked when engine cancels
    - T6: test_checkpointer_detached_on_run_end (sound version)
    - T8: reducer + push-ref observer discriminating test
    - T7: per-observer filter honoured by bind_custom_bus / bind_interrupt_bus
"""

import asyncio
import operator

import pytest

from operonx import PARENT, SCRATCH, EmitOp, InterruptOp, Operon, op
from operonx.checkpoint import (
    CustomEvent,
    InMemoryCheckpointer,
    InterruptEvent,
    ObserveBudgetExceeded,
    bind_custom_bus,
    bind_interrupt_bus,
)
from operonx.core.ops.graph.graph_op import END, START, GraphOp
from operonx.core.ops.transform.func_op import FuncOp
from operonx.core.states.ref import Ref
from operonx.core.states.schema import StateSchema
from operonx.core.states.state import MemoryState


def _pass(**kw):
    return kw


# ---------------------------------------------------------------------------
# H1 — push-ref forwards source's POST-reducer stored value
# ---------------------------------------------------------------------------


class TestH1PushRefUsesPostReducerValue:
    """The DSL doesn't wire shared→shared push-refs, so we manually inject
    one into the schema — same technique as the reproducer that flagged
    the bug — to prove the semantic is correct after the fix."""

    def _prepared(self):
        with GraphOp(name="g") as g:
            PARENT.declare(a=0, b=0, reducers={"a": max, "b": max})
            n = FuncOp(name="n", code_fn=_pass, inputs={})
            START >> n >> END
        g.build()
        schema = StateSchema(g)

        a_idx = schema.get_index("g", "a")
        b_idx = schema.get_index("g", "b")

        # Wire push_ref A → B (identity fn).
        push_ref = Ref("g", "b", is_output=True)
        push_ref.idx = b_idx
        schema._push_refs[a_idx] = push_ref

        state = MemoryState(schema)
        return schema, state, a_idx, b_idx

    def test_source_reducer_reflected_in_push_target(self):
        _, state, _, _ = self._prepared()
        # Seed asymmetric so a delta-forward vs value-forward would differ.
        state._cells[state.schema.get_index("g", "a")][("main",)] = 100
        state._cells[state.schema.get_index("g", "b")][("main",)] = 5

        # Write 50 to a. Under max reducer:
        #   a stored:   max(100, 50) = 100 (unchanged)
        #   With FIX:   b receives push_ref._fn(100) = 100 → b: max(5, 100) = 100 ✓
        #   Pre-fix:    b receives push_ref._fn(50)  = 50  → b: max(5, 50)  = 50
        state["g", "a"] = 50

        assert state["g", "a"] == 100
        assert state["g", "b"] == 100  # would be 50 pre-H1-fix

    def test_no_reducer_at_source_behaves_identically(self):
        """Fix is a no-op for the canonical local→shared case where
        source has no reducer (stored == value)."""
        with GraphOp(name="g") as g:
            PARENT.declare(count=0, reducers={"count": operator.add})
            node = FuncOp(name="node", code_fn=lambda: {"count": 5}, inputs={})
            node["count"] >> PARENT["count"]
            START >> node >> END
        g.build()
        schema = StateSchema(g)
        state = MemoryState(schema)

        state["g.node", "count"] = 3
        state["g.node", "count"] = 4
        # Local writes overwrite (no reducer on node.count); push-ref
        # forwards each stored value; reducer at PARENT.count adds.
        assert state["g", "count"] == 7  # 0 + 3 + 4


# ---------------------------------------------------------------------------
# B2 — per-observer exception guard (cell-write bus)
# ---------------------------------------------------------------------------


class TestB2PerObserverGuard:
    def _state(self):
        with GraphOp(name="g") as g:
            PARENT.declare(x=0)
            n = FuncOp(name="n", code_fn=_pass, inputs={})
            START >> n >> END
        g.build()
        return MemoryState(StateSchema(g))

    def test_peer_observers_still_fire_when_one_raises(self):
        state = self._state()
        got_a, got_b = [], []
        state.subscribe_writes(lambda i, c, v: (_ for _ in ()).throw(RuntimeError("boom")))
        state.subscribe_writes(lambda i, c, v: got_a.append(v))
        state.subscribe_writes(lambda i, c, v: got_b.append(v))

        with pytest.raises(RuntimeError, match="boom"):
            state["g", "x"] = 42

        assert got_a == [42]
        assert got_b == [42]

    def test_first_exception_bubbles_last_ones_still_visible(self):
        """When multiple observers raise, the FIRST error is what
        bubbles up (deterministic behaviour)."""
        state = self._state()

        def raise_first(i, c, v):
            raise RuntimeError("first")

        def raise_second(i, c, v):
            raise RuntimeError("second")

        state.subscribe_writes(raise_first)
        state.subscribe_writes(raise_second)

        with pytest.raises(RuntimeError, match="first"):
            state["g", "x"] = 1


# ---------------------------------------------------------------------------
# T3 — ObserveBudgetExceeded is BaseException; op-body catch can't swallow
# ---------------------------------------------------------------------------


class TestT3ObserveBudgetExceededBaseException:
    def test_is_base_exception_not_exception(self):
        assert issubclass(ObserveBudgetExceeded, BaseException)
        assert not issubclass(ObserveBudgetExceeded, Exception)

    async def test_op_body_except_exception_cannot_swallow(self):
        """A user's op body wrapped in `try: ... except Exception:` must
        NOT swallow the circuit breaker — the run must halt."""

        @op(observe_max=1)
        def wrapped():
            try:
                # Anything that triggers a checkpointer write → 2nd event
                # → ObserveBudgetExceeded. Wrapped in Exception catch that
                # would swallow a plain RuntimeError.
                return {"a": 1, "b": 2}
            except Exception:
                return {"safe": True}

        with GraphOp(name="g") as g:
            PARENT.declare(count=0)
            w = wrapped(name="w")
            w["a"] >> PARENT["count"]
            START >> w >> END

        cp = InMemoryCheckpointer()
        with pytest.raises(ObserveBudgetExceeded):
            await Operon(g).run(inputs={}, checkpointer=cp)


# ---------------------------------------------------------------------------
# T1 — mode="custom" E2E
# ---------------------------------------------------------------------------


class TestT1CustomStreamE2E:
    async def test_emitop_payload_reaches_stream_consumer(self):
        with GraphOp(name="pipe") as g:
            src = FuncOp(name="src", code_fn=lambda: {"n": 42}, inputs={})
            tel = EmitOp(name="tel", payload=src["n"], channel="ui")
            START >> src >> tel >> END

        engine = Operon(g)
        got: list = []
        async for evt in engine.stream(inputs={}, mode="custom"):
            got.append(evt)

        assert got, "expected at least one CustomEvent"
        (evt,) = got
        assert isinstance(evt, CustomEvent)
        assert evt.channel == "ui"
        assert evt.payload == 42

    async def test_channel_filter(self):
        with GraphOp(name="pipe") as g:
            src = FuncOp(name="src", code_fn=lambda: {"n": 1, "m": 2}, inputs={})
            a = EmitOp(name="a", payload=src["n"], channel="ui")
            b = EmitOp(name="b", payload=src["m"], channel="metrics")
            START >> src >> a >> b >> END

        engine = Operon(g)
        got: list = []
        async for evt in engine.stream(inputs={}, mode="custom", channels=["ui"]):
            got.append(evt)

        # Only "ui" survives the filter.
        assert all(evt.channel == "ui" for evt in got)
        assert len(got) == 1


# ---------------------------------------------------------------------------
# T2 — sample_every via engine.run
# ---------------------------------------------------------------------------


class TestT2SampleEveryEndToEnd:
    async def test_sample_every_drops_intermediate_steps(self):
        @op
        def a():
            return {"v": 1}

        @op
        def b():
            return {"v": 2}

        @op
        def c():
            return {"v": 3}

        @op
        def d():
            return {"v": 4}

        with GraphOp(name="g") as g:
            n1 = a(name="a")
            n2 = b(name="b")
            n3 = c(name="c")
            n4 = d(name="d")
            START >> n1 >> n2 >> n3 >> n4 >> END

        cp = InMemoryCheckpointer(sample_every=2)
        await Operon(g).run(inputs={}, checkpointer=cp)

        # step_id starts at 0 for the first op and increments per op-complete.
        # With sample_every=2, only even-numbered steps land — expect a subset.
        assert cp.list_steps(), "at least the sampled steps should be recorded"
        for s in cp.list_steps():
            assert s % 2 == 0


# ---------------------------------------------------------------------------
# T4 — handle.cancel() with pending InterruptOp + T5 on_cancel called
# ---------------------------------------------------------------------------


class _CancelCountingCheckpointer(InMemoryCheckpointer):
    """Records on_cancel invocations so we can assert T5."""

    __slots__ = ("cancel_calls",)

    def __init__(self):
        super().__init__()
        self.cancel_calls = 0

    def on_cancel(self, ctx):
        self.cancel_calls += 1
        super().on_cancel(ctx)


class TestT4T5CancelWithPendingInterrupt:
    async def test_cancel_cleans_pending_interrupt_and_notifies_cp(self):
        with GraphOp(name="pipe") as g:
            gate = InterruptOp(name="gate")
            START >> gate >> END

        engine = Operon(g)
        cp = _CancelCountingCheckpointer()
        handle = engine.start(inputs={}, checkpointer=cp)

        # Wait for the interrupt to register its future.
        for _ in range(100):
            if handle.state._interrupt_responses:
                break
            await asyncio.sleep(0.01)

        assert handle.state._interrupt_responses, "interrupt should be pending"

        handle.cancel()
        # Give the cancellation propagation a tick to run.
        try:
            await asyncio.wait_for(handle._scheduler_task, timeout=1.0)
        except (asyncio.CancelledError, asyncio.TimeoutError):
            pass

        # T5: checkpointer.on_cancel was invoked at least once.
        assert cp.cancel_calls >= 1
        # H3: response bus drained by engine.finally.
        assert not handle.state._interrupt_responses


# ---------------------------------------------------------------------------
# T6 — sound checkpointer-detach test
# ---------------------------------------------------------------------------


class TestT6DetachOnRunEnd:
    async def test_state_observer_list_empty_after_run(self):
        """After engine.run finishes, the state's cell-write observer list
        must be empty — we bound the checkpointer, we must also unbind."""

        @op
        def worker():
            return {"n": 1}

        with GraphOp(name="g") as g:
            n = worker(name="n")
            START >> n >> END

        engine = Operon(g)
        cp = InMemoryCheckpointer()
        handle = engine.start(inputs={}, checkpointer=cp)
        await handle.collect(unwrap=True)

        # Post-run: state's observer list should be empty.
        # Writing directly to the state must NOT reach the checkpointer.
        before_steps = list(cp.list_steps())
        handle.state["g.n", "n"] = 99  # simulate a stray post-run write
        after_steps = list(cp.list_steps())
        # The extra write should not have added a new step to the checkpointer.
        assert before_steps == after_steps


# ---------------------------------------------------------------------------
# T8 — discriminating push-ref reducer observer test
# ---------------------------------------------------------------------------


class TestT8ObserverSeesReducerMergedValue:
    def test_observer_receives_post_reducer_value_on_pushref(self):
        """Before Phase 2b3 the test used 0+5=5 which couldn't distinguish
        reduce-then-observe from raw-forward. Now with a non-zero initial
        the two cases yield distinct values."""

        with GraphOp(name="g") as g:
            PARENT.declare(count=100, reducers={"count": operator.add})
            node = FuncOp(name="node", code_fn=lambda: {"count": 5}, inputs={})
            node["count"] >> PARENT["count"]
            START >> node >> END
        g.build()
        schema = StateSchema(g)
        state = MemoryState(schema)

        events = []
        state.subscribe_writes(lambda i, c, v: events.append((i, v)))

        node_idx = schema.get_index("g.node", "count")
        parent_idx = schema.get_index("g", "count")

        state["g.node", "count"] = 5
        # Two events expected:
        #   1. node.count = 5 (local, no reducer)
        #   2. parent.count = 100 + 5 = 105 (reducer add)
        assert (node_idx, 5) in events
        assert (parent_idx, 105) in events, (
            f"observer must receive post-reducer 105, not raw 5. Events: {events}"
        )


# ---------------------------------------------------------------------------
# T7 — per-observer filter on custom + interrupt buses
# ---------------------------------------------------------------------------


class TestT7PerObserverFilterOnCustomBus:
    async def test_emitop_include_empty_silences_custom_bus(self):
        """An EmitOp declared with include=[] must not surface events on
        the custom bus at all — proves bind_custom_bus consults the filter."""
        with GraphOp(name="pipe") as g:
            src = FuncOp(name="src", code_fn=lambda: {"n": 1}, inputs={})
            tel = EmitOp(name="tel", payload=src["n"], channel="ui", include=[])
            START >> src >> tel >> END

        engine = Operon(g)
        got = []
        async for evt in engine.stream(inputs={}, mode="custom"):
            got.append(evt)

        assert got == []

    async def test_interruptop_include_empty_silences_interrupt_bus(self):
        """InterruptOp with include=[] doesn't emit InterruptEvents even
        though it still suspends internally."""
        with GraphOp(name="pipe") as g:
            gate = InterruptOp(name="gate", include=[])
            START >> gate >> END

        engine = Operon(g)
        state_container = {}
        events: list = []

        def _sink(evt: InterruptEvent):
            events.append(evt)

        handle = engine.start(inputs={})
        state_container["s"] = handle.state
        _unbind = bind_interrupt_bus(
            handle.state,
            _sink,
            op_registry=engine._all_ops_registry(),
        )
        try:
            # Give the interrupt op a chance to register + emit (or not).
            for _ in range(50):
                if handle.state._interrupt_responses:
                    break
                await asyncio.sleep(0.005)
            # Resume immediately so the run can finish.
            for iid in list(handle.state._interrupt_responses or {}):
                handle.state.resume_interrupt(iid, True)
            await asyncio.wait_for(handle._scheduler_task, timeout=1.0)
        finally:
            _unbind()

        # Op was silenced → no InterruptEvent surfaced.
        assert events == []
