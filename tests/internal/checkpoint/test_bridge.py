"""End-to-end tests for bind_checkpointer: state writes → checkpointer captures.

This is the integration test that verifies Phase 1 (reducers via _write_cell)
composes with Phase 2 (observer funnel + checkpointer) as designed. When a
graph runs with a checkpointer bound, every cell write — including
push-ref hops and reducer-merged values — must land in the checkpointer's
delta log with the correct step_id, op, and var attribution.
"""

import operator

from operonx.checkpoint import InMemoryCheckpointer, bind_checkpointer
from operonx.core.ops.graph.graph_op import END, PARENT, START, GraphOp
from operonx.core.ops.transform.func_op import FuncOp
from operonx.core.states.schema import StateSchema
from operonx.core.states.state import MemoryState


def _passthrough(**kwargs):
    return kwargs


def _built_state(name, declare_call=None):
    with GraphOp(name=name) as g:
        if declare_call is not None:
            declare_call()
        n = FuncOp(name="n", code_fn=_passthrough, inputs={})
        START >> n >> END
    g.build()
    schema = StateSchema(g)
    return g, schema, MemoryState(schema)


class TestBridgeBasics:
    def test_write_captured_by_checkpointer(self):
        _, _, state = _built_state("g", lambda: PARENT.declare(x=0))
        cp = InMemoryCheckpointer()
        step = [0]
        bind_checkpointer(state, cp, lambda: step[0])

        state["g", "x"] = 42

        assert cp.list_steps() == [0]
        assert cp.get_updates(0) == {("g", "x", ("main",)): 42}

    def test_step_id_advances_per_write_batch(self):
        _, _, state = _built_state("g", lambda: PARENT.declare(x=0, y=""))
        cp = InMemoryCheckpointer()
        step = [0]
        bind_checkpointer(state, cp, lambda: step[0])

        state["g", "x"] = 1
        step[0] = 1
        state["g", "y"] = "hi"
        step[0] = 2
        state["g", "x"] = 2

        assert cp.list_steps() == [0, 1, 2]
        assert cp.get_state(2) == {
            ("g", "x", ("main",)): 2,
            ("g", "y", ("main",)): "hi",
        }

    def test_op_var_names_attributed_correctly(self):
        _, schema, state = _built_state("g", lambda: PARENT.declare(count=0))
        cp = InMemoryCheckpointer()
        bind_checkpointer(state, cp, lambda: 0)

        state["g", "count"] = 5

        (only_key,) = cp.get_updates(0).keys()
        assert only_key == ("g", "count", ("main",))

    def test_multiple_ctxs_kept_apart(self):
        """Per-context cells should land in the checkpointer with distinct ctx keys."""
        _, schema, state = _built_state("g", lambda: PARENT.declare(count=0))
        cp = InMemoryCheckpointer()
        bind_checkpointer(state, cp, lambda: 0)

        # Write to same op.var via different ctxs (shared cell collapses to
        # DEFAULT_CONTEXT so use a non-shared op-local var to demonstrate).
        state["g.n", "start_time", ("main", "[0]")] = "t0"
        state["g.n", "start_time", ("main", "[1]")] = "t1"

        updates = cp.get_updates(0)
        assert updates[("g.n", "start_time", ("main", "[0]"))] == "t0"
        assert updates[("g.n", "start_time", ("main", "[1]"))] == "t1"


class TestBridgeWithReducer:
    def test_push_ref_and_reducer_both_visible(self):
        """Both hops of a push-ref should register in the checkpointer,
        and the shared cell's recorded value should be POST-reducer."""
        with GraphOp(name="g") as g:
            PARENT.declare(count=0, reducers={"count": operator.add})
            node = FuncOp(name="node", code_fn=lambda: {"count": 5}, inputs={})
            node["count"] >> PARENT["count"]
            START >> node >> END
        g.build()
        schema = StateSchema(g)
        state = MemoryState(schema)
        cp = InMemoryCheckpointer()
        bind_checkpointer(state, cp, lambda: 0)

        state["g.node", "count"] = 3
        state["g.node", "count"] = 4

        updates = cp.get_updates(0)
        # Local cell writes: 3, then 4 (overwrite)
        assert updates[("g.node", "count", ("main",))] == 4
        # Shared cell reducer-merged: 0 + 3 + 4 = 7
        assert updates[("g", "count", ("main",))] == 7


class TestUnsubscribe:
    def test_unsubscribe_stops_capture(self):
        _, _, state = _built_state("g", lambda: PARENT.declare(x=0))
        cp = InMemoryCheckpointer()
        unsub = bind_checkpointer(state, cp, lambda: 0)

        state["g", "x"] = 1
        unsub()
        state["g", "x"] = 999  # not captured

        assert cp.get_updates(0) == {("g", "x", ("main",)): 1}


class TestFastPathUnaffected:
    def test_state_without_checkpointer_zero_overhead(self):
        """Ensure the observer slot stays empty when no bind is called."""
        _, _, state = _built_state("g", lambda: PARENT.declare(x=0))
        assert state._observers == []
        state["g", "x"] = 1
        # No error; no observer to notify — fast path.
