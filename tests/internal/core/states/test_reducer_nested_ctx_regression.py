"""Regression test for the Phase 1 reducer bug found during Phase 3 development.

Bug: ``_write_cell`` was reading ``old`` via ``ctx_key in cell`` before
applying the reducer. Shared cells always store at DEFAULT_CONTEXT, so when
a write came from a nested ctx like ``("main", "loop_1")`` (Phase 3 loop
iterations), the containment check failed, ``old = cell.default_value`` was
used, and the reducer degenerated to LWW — silently discarding all previously
accumulated shared state.

The fix routes the read through ``Cell.__getitem__`` which handles the
shared→DEFAULT_CONTEXT mapping and parent-context fallback correctly.

The Phase 1 test suite never exercised nested-ctx writes to a shared cell
so this bug was invisible until Phase 3's loop iterations exercised it.
"""

import operator

from operonx.core.ops.graph.graph_op import END, PARENT, START, GraphOp
from operonx.core.ops.transform.func_op import FuncOp
from operonx.core.states.cell import DEFAULT_CONTEXT
from operonx.core.states.schema import StateSchema
from operonx.core.states.state import MemoryState


def _passthrough(**kwargs):
    return kwargs


def _minimal_state_with_reducer():
    """Graph with one shared+reducer cell and a passthrough op."""
    with GraphOp(name="g") as g:
        PARENT.declare(total=0, reducers={"total": operator.add})
        n = FuncOp(name="n", code_fn=_passthrough, inputs={})
        START >> n >> END
    g.build()
    state = MemoryState(StateSchema(g))
    return g, state


class TestReducerFromNestedCtx:
    def test_first_write_from_nested_ctx_starts_from_initial(self):
        """First write at a nested ctx must reduce with the shared cell's
        initial value, not its default_value."""
        g, state = _minimal_state_with_reducer()
        # No prior write. Write at ("main", "loop_1") with value 5.
        # Reducer: add(initial=0, new=5) = 5.
        state["g", "total", ("main", "loop_1")] = 5
        total_idx = state.schema.get_index("g", "total")
        assert state._cells[total_idx][("main",)] == 5

    def test_repeated_writes_from_different_nested_ctxs_accumulate(self):
        """Simulates a loop: writes come from ("main",), ("main","loop_1"),
        ("main","loop_2"), etc. Reducer must accumulate across them."""
        g, state = _minimal_state_with_reducer()
        state["g", "total", ("main",)] = 1
        state["g", "total", ("main", "loop_1")] = 2
        state["g", "total", ("main", "loop_2")] = 3
        state["g", "total", ("main", "loop_3")] = 4
        total_idx = state.schema.get_index("g", "total")
        # Reducer sum: 0 + 1 + 2 + 3 + 4 = 10.
        assert state._cells[total_idx][("main",)] == 10

    def test_list_append_reducer_accumulates_across_nested_ctxs(self):
        """The failure mode that surfaced the bug — list concat via
        operator.add across loop iterations should build the full list."""
        with GraphOp(name="g") as g:
            PARENT.declare(log=[], reducers={"log": operator.add})
            n = FuncOp(name="n", code_fn=_passthrough, inputs={})
            START >> n >> END
        g.build()
        state = MemoryState(StateSchema(g))

        state["g", "log", ("main",)] = ["a"]
        state["g", "log", ("main", "loop_1")] = ["b"]
        state["g", "log", ("main", "loop_2")] = ["c"]

        log_idx = state.schema.get_index("g", "log")
        assert state._cells[log_idx][("main",)] == ["a", "b", "c"]

    def test_push_ref_through_nested_ctx_uses_reducer(self):
        """The full push-ref hop (the canonical local→shared case) must
        still honour the reducer when the source ctx is nested."""
        with GraphOp(name="g") as g:
            PARENT.declare(total=0, reducers={"total": operator.add})
            producer = FuncOp(name="producer", code_fn=_passthrough, inputs={})
            # producer["v"] pushes to PARENT["total"].
            producer["v"] >> PARENT["total"]
            START >> producer >> END
        g.build()
        state = MemoryState(StateSchema(g))

        # Direct write to producer's cell at a nested ctx triggers the
        # push-ref hop, which lands at PARENT["total"] shared cell.
        state["g.producer", "v", ("main", "loop_1")] = 7
        state["g.producer", "v", ("main", "loop_2")] = 8

        total_idx = state.schema.get_index("g", "total")
        # 0 + 7 + 8 = 15.
        assert state._cells[total_idx][("main",)] == 15
