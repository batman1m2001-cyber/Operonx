"""Tests for the SCRATCH observer bus (B1 fix).

Before Phase 2b3, ``SCRATCH[k] = v`` bypassed the observability funnel
entirely — any checkpointer or tracer bound to the run was blind to
SCRATCH mutations even though the design doc promised parity. This
suite locks in the fix.
"""

import pytest

from operonx import PARENT, SCRATCH, Operon, op
from operonx.checkpoint import InMemoryCheckpointer
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
    return MemoryState(StateSchema(g))


class TestScratchBusDirect:
    def test_notify_scratch_fires_observer(self):
        state = _minimal_state()
        events = []
        state.subscribe_scratch(lambda k, v: events.append((k, v)))
        state._notify_scratch("foo", 42)
        assert events == [("foo", 42)]

    def test_no_subscriber_fast_path(self):
        state = _minimal_state()
        # No observer → no error, no side effect.
        state._notify_scratch("k", "v")

    def test_unsubscribe(self):
        state = _minimal_state()
        events = []
        cb = lambda k, v: events.append((k, v))
        state.subscribe_scratch(cb)
        state._notify_scratch("a", 1)
        state.unsubscribe_scratch(cb)
        state._notify_scratch("b", 2)
        assert events == [("a", 1)]

    def test_scratch_setitem_routes_through_notify(self):
        """SCRATCH[k]=v inside an active run must fire the bus."""
        state = _minimal_state()
        events = []
        state.subscribe_scratch(lambda k, v: events.append((k, v)))

        token = _set_state(state)
        try:
            SCRATCH["docs_slots"] = {"name": "Alice"}
            SCRATCH["stuck_count"] = 3
        finally:
            _reset_state(token)

        assert events == [
            ("docs_slots", {"name": "Alice"}),
            ("stuck_count", 3),
        ]

    def test_observer_exception_does_not_break_peers(self):
        """B2 semantic applies to scratch bus too — one bad observer must
        not prevent peers from receiving the event."""
        state = _minimal_state()
        got_a, got_b = [], []
        state.subscribe_scratch(lambda k, v: (_ for _ in ()).throw(RuntimeError("boom")))
        state.subscribe_scratch(lambda k, v: got_a.append((k, v)))
        state.subscribe_scratch(lambda k, v: got_b.append((k, v)))

        with pytest.raises(RuntimeError, match="boom"):
            state._notify_scratch("k", 1)

        # First observer raised, but observers 2 and 3 still fired.
        assert got_a == [("k", 1)]
        assert got_b == [("k", 1)]


class TestScratchEndToEndCheckpointer:
    async def test_scratch_writes_land_in_checkpointer(self):
        """The primary regression: run a graph whose op writes SCRATCH
        and prove the bound checkpointer records the mutation."""

        @op
        def worker():
            SCRATCH["stuck_count"] = 5
            SCRATCH["is_final"] = True
            return {"n": 1}

        with GraphOp(name="pipe") as g:
            w = worker(name="worker")
            START >> w >> END

        cp = InMemoryCheckpointer()
        await Operon(g).run(inputs={}, checkpointer=cp)

        # Collate all cell keys the checkpointer saw across steps.
        seen: dict = {}
        for s in cp.list_steps():
            seen.update(cp.get_updates(s))

        # Scratch writes land under the synthetic op name "__scratch__".
        assert ("__scratch__", "stuck_count", ("main",)) in seen
        assert ("__scratch__", "is_final", ("main",)) in seen
        assert seen[("__scratch__", "stuck_count", ("main",))] == 5
        assert seen[("__scratch__", "is_final", ("main",))] is True

    async def test_scratch_and_regular_cell_writes_coexist(self):
        @op
        def worker():
            SCRATCH["k"] = "v"
            return {"result": 42}

        with GraphOp(name="pipe") as g:
            PARENT.declare(count=0)
            w = worker(name="worker")
            w["result"] >> PARENT["count"]
            START >> w >> END

        cp = InMemoryCheckpointer()
        await Operon(g).run(inputs={}, checkpointer=cp)

        seen: dict = {}
        for s in cp.list_steps():
            seen.update(cp.get_updates(s))

        # Both channels captured.
        assert ("__scratch__", "k", ("main",)) in seen
        assert ("pipe", "count", ("main",)) in seen
