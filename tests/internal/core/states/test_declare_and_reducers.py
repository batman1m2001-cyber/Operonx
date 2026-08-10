"""Tests for PARENT.declare() with reducers on shared cells.

Covers the Phase 1 semantic contract:
    - .declare() replaces .shared() with an optional `reducers=` kwarg.
    - Reducers are applied at cell-write via _write_cell — including on the
      push-ref hop (the critical B1 fix from the plan review).
    - Error cases E1-E6 from STATE_LOOP_REFACTOR_PLAN.md §Phase 1.
    - Backward compat: .shared() still works, just emits DeprecationWarning.
"""

import operator
import warnings

import pytest

from operonx.core.ops.graph.graph_op import END, PARENT, START, GraphOp
from operonx.core.ops.transform.func_op import FuncOp
from operonx.core.states.schema import StateSchema
from operonx.core.states.state import MemoryState, ReducerError
from operonx.reducers import add_messages, dict_merge

# ---------------------------------------------------------------------------
# Helpers — every test builds a minimal graph with one passthrough node so
# START >> node >> END satisfies the scheduler's target-must-exist check.
# ---------------------------------------------------------------------------


def _passthrough(**kwargs):
    return kwargs


def _minimal_graph(name, declare_call=None):
    """Build a minimal buildable graph. declare_call runs before wiring."""
    with GraphOp(name=name) as g:
        if declare_call is not None:
            declare_call()
        n = FuncOp(name="n", code_fn=_passthrough, inputs={})
        START >> n >> END
    return g


def _built_state(name, declare_call=None):
    g = _minimal_graph(name, declare_call)
    g.build()
    schema = StateSchema(g)
    return g, schema, MemoryState(schema)


# ---------------------------------------------------------------------------
# .declare() surface
# ---------------------------------------------------------------------------


class TestDeclareAPI:
    def test_declare_registers_shared_vars(self):
        g = _minimal_graph("g", lambda: PARENT.declare(count=0, log=[]))
        assert g._shared_vars == {"count": 0, "log": []}
        assert g._reducer_vars == {}

    def test_declare_registers_reducers(self):
        g = _minimal_graph(
            "g",
            lambda: PARENT.declare(
                count=0, log=[], reducers={"count": operator.add, "log": operator.add}
            ),
        )
        assert g._reducer_vars == {"count": operator.add, "log": operator.add}

    def test_declare_with_no_reducers_still_valid(self):
        g = _minimal_graph("g", lambda: PARENT.declare(x=42))
        assert g._shared_vars == {"x": 42}
        assert g._reducer_vars == {}

    def test_declare_outside_graph_raises(self):
        with pytest.raises(RuntimeError, match="inside a @graph"):
            PARENT.declare(x=0)

    def test_declare_on_non_parent_raises(self):
        with pytest.raises(TypeError, match=r"declare\(\) can only be called on PARENT"):
            START.declare(x=0)


# ---------------------------------------------------------------------------
# Error cases (E4, E6, reducer type check)
# ---------------------------------------------------------------------------


class TestDeclareErrorCases:
    def test_E4_reducer_key_not_in_declared_vars(self):
        """Reducer referencing an undeclared var must raise at decoration."""
        with pytest.raises(ValueError, match="undeclared vars.*typo"):
            _minimal_graph("g", lambda: PARENT.declare(count=0, reducers={"typo": operator.add}))

    def test_reducers_not_a_dict_raises(self):
        with pytest.raises(TypeError, match="reducers must be a dict"):
            _minimal_graph(
                "g",
                lambda: PARENT.declare(count=0, reducers=[("count", operator.add)]),
            )

    def test_E6_multiple_declare_calls_merge_not_error(self):
        """MVP: multiple .declare() calls in the same graph merge additively.

        Full 'must be at top level' enforcement would need AST inspection;
        keep the door open by asserting the merge behaviour we ship today.
        """

        def _both():
            PARENT.declare(a=1)
            PARENT.declare(b=2, reducers={"b": operator.add})

        g = _minimal_graph("g", _both)
        assert g._shared_vars == {"a": 1, "b": 2}
        assert g._reducer_vars == {"b": operator.add}


# ---------------------------------------------------------------------------
# Schema plumbing
# ---------------------------------------------------------------------------


class TestSchemaReducerBinding:
    def test_reducer_indexed_by_shared_idx(self):
        g, schema, _ = _built_state(
            "g", lambda: PARENT.declare(count=0, reducers={"count": operator.add})
        )
        idx = schema.get_index("g", "count")
        assert idx in schema._shared_indices
        assert schema._reducers[idx] is operator.add

    def test_reducerless_shared_var_absent_from_reducers(self):
        g, schema, _ = _built_state("g", lambda: PARENT.declare(count=0, log=[]))
        assert schema._reducers == {}
        idx = schema.get_index("g", "count")
        assert idx in schema._shared_indices


# ---------------------------------------------------------------------------
# Runtime write semantics (E1, E2, E3)
# ---------------------------------------------------------------------------


class TestRuntimeReducerSemantics:
    def test_E1_no_reducer_last_write_wins(self):
        _, _, state = _built_state("g", lambda: PARENT.declare(count=0))
        state["g", "count"] = 5
        state["g", "count"] = 10
        assert state["g", "count"] == 10

    def test_E2_reducer_merges_on_write(self):
        _, _, state = _built_state(
            "g", lambda: PARENT.declare(count=0, reducers={"count": operator.add})
        )
        state["g", "count"] = 1
        state["g", "count"] = 2
        state["g", "count"] = 3
        assert state["g", "count"] == 6

    def test_E2_reducer_merges_lists(self):
        _, _, state = _built_state(
            "g", lambda: PARENT.declare(log=[], reducers={"log": operator.add})
        )
        state["g", "log"] = ["a"]
        state["g", "log"] = ["b", "c"]
        assert state["g", "log"] == ["a", "b", "c"]

    def test_E2_add_messages_reducer_upserts(self):
        _, _, state = _built_state(
            "g",
            lambda: PARENT.declare(messages=[], reducers={"messages": add_messages}),
        )
        state["g", "messages"] = [{"id": "a", "text": "old"}]
        state["g", "messages"] = [{"id": "a", "text": "new"}, {"id": "b"}]
        assert state["g", "messages"] == [
            {"id": "a", "text": "new"},
            {"id": "b"},
        ]

    def test_E2_dict_merge_reducer(self):
        _, _, state = _built_state(
            "g",
            lambda: PARENT.declare(settings={}, reducers={"settings": dict_merge}),
        )
        state["g", "settings"] = {"a": 1, "nested": {"x": 1}}
        state["g", "settings"] = {"b": 2, "nested": {"y": 2}}
        assert state["g", "settings"] == {
            "a": 1,
            "b": 2,
            "nested": {"x": 1, "y": 2},
        }

    def test_E3_reducer_raises_wraps_in_ReducerError(self):
        def bad(old, new):
            raise ValueError("boom")

        _, schema, state = _built_state("g", lambda: PARENT.declare(x=0, reducers={"x": bad}))
        with pytest.raises(ReducerError) as exc_info:
            state["g", "x"] = 5

        err = exc_info.value
        assert err.idx == schema.get_index("g", "x")
        assert err.old == 0
        assert err.new == 5
        assert isinstance(err.cause, ValueError)
        assert "boom" in str(err.cause)

    def test_reducer_receives_default_on_first_write(self):
        received = []

        def track(old, new):
            received.append(old)
            return new

        _, _, state = _built_state("g", lambda: PARENT.declare(x=42, reducers={"x": track}))
        state["g", "x"] = 100
        assert received == [42]


# ---------------------------------------------------------------------------
# The critical B1 fix — push-ref hop must go through _write_cell
# ---------------------------------------------------------------------------


class TestPushRefReducerPath:
    """Regression tests for B1: push-refs must apply the target's reducer.

    Before the funnel fix, state.py wrote directly to the target cell,
    bypassing __setitem__ and therefore skipping the reducer. 100% of
    shared-cell writes come via push-ref (op["k"] >> PARENT["k"]), so a
    reducer branch in __setitem__ alone would have silently dropped every
    reducer merge.
    """

    def _build_pushref_graph(self, reducer=None):
        reducers = {"count": reducer} if reducer is not None else None
        with GraphOp(name="g") as g:
            PARENT.declare(count=0, reducers=reducers)
            node = FuncOp(name="node", code_fn=lambda: {"count": 5}, inputs={})
            node["count"] >> PARENT["count"]  # push_ref via >> operator
            START >> node >> END
        g.build()
        schema = StateSchema(g)
        return schema, MemoryState(schema)

    def test_push_ref_write_hits_reducer(self):
        schema, state = self._build_pushref_graph(reducer=operator.add)

        # Simulate three op invocations writing to node.count → push-ref g.count
        state["g.node", "count"] = 1
        state["g.node", "count"] = 2
        state["g.node", "count"] = 3

        # Reducer must have accumulated ALL three via the push-ref hop
        assert state["g", "count"] == 6  # 0 + 1 + 2 + 3

    def test_push_ref_write_hits_add_messages_reducer(self):
        with GraphOp(name="g") as g:
            PARENT.declare(messages=[], reducers={"messages": add_messages})
            node = FuncOp(name="node", code_fn=lambda: {"messages": []}, inputs={})
            node["messages"] >> PARENT["messages"]
            START >> node >> END

        g.build()
        schema = StateSchema(g)
        state = MemoryState(schema)

        state["g.node", "messages"] = [{"id": "a", "text": "hi"}]
        state["g.node", "messages"] = [{"id": "b", "text": "world"}]

        assert state["g", "messages"] == [
            {"id": "a", "text": "hi"},
            {"id": "b", "text": "world"},
        ]

    def test_push_ref_without_reducer_still_last_write_wins(self):
        schema, state = self._build_pushref_graph(reducer=None)

        state["g.node", "count"] = 1
        state["g.node", "count"] = 2
        assert state["g", "count"] == 2  # overwrite


# ---------------------------------------------------------------------------
# Backward compat — .shared() still works, emits deprecation warning
# ---------------------------------------------------------------------------


class TestBackwardCompatShared:
    def test_shared_still_registers_vars(self):
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            g = _minimal_graph("g", lambda: PARENT.shared(count=0, log=[]))
        assert g._shared_vars == {"count": 0, "log": []}
        assert g._reducer_vars == {}

    def test_shared_emits_deprecation_warning(self):
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            _minimal_graph("g", lambda: PARENT.shared(count=0))

        dep_warnings = [x for x in w if issubclass(x.category, DeprecationWarning)]
        assert len(dep_warnings) == 1
        assert "declare()" in str(dep_warnings[0].message)

    def test_declare_and_shared_can_coexist(self):
        """Migration path: existing .shared() code can add .declare() incrementally."""

        def _both():
            PARENT.shared(legacy=1)
            PARENT.declare(new=2, reducers={"new": operator.add})

        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            g = _minimal_graph("g", _both)
        assert g._shared_vars == {"legacy": 1, "new": 2}
        assert g._reducer_vars == {"new": operator.add}
