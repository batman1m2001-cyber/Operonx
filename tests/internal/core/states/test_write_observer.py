"""Tests for MemoryState observer funnel (Phase 2 write hook).

The ``_write_cell`` funnel from Phase 1 gets a second responsibility in
Phase 2: notify subscribed observers after each successful write. This is
the plumbing that lets the checkpointer, tracer, and any future observer
tap the write stream from one place.

Contract:
    - No observers registered → zero overhead (fast path)
    - Observer receives ``(idx, ctx_key, value)`` POST-reducer, POST-commit
    - Push-ref writes also fire observers (both hops)
    - Multiple observers fire in registration order
    - subscribe/unsubscribe are cheap and safe
"""

import operator

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


class TestObserverBasics:
    def test_no_observer_no_events(self):
        _, _, state = _built_state("g", lambda: PARENT.declare(x=0))
        state["g", "x"] = 5
        # No observers → no exceptions, no side effects.

    def test_single_observer_receives_write(self):
        events = []
        _, schema, state = _built_state("g", lambda: PARENT.declare(x=0))
        state.subscribe_writes(lambda idx, ctx, val: events.append((idx, ctx, val)))

        state["g", "x"] = 42
        idx = schema.get_index("g", "x")
        assert events == [(idx, ("main",), 42)]

    def test_multiple_writes_all_captured(self):
        events = []
        _, _, state = _built_state("g", lambda: PARENT.declare(x=0, y=""))
        state.subscribe_writes(lambda idx, ctx, val: events.append((idx, val)))

        state["g", "x"] = 1
        state["g", "y"] = "hello"
        state["g", "x"] = 2
        assert [e[1] for e in events] == [1, "hello", 2]


class TestObserverPostReducer:
    def test_observer_sees_post_reducer_value(self):
        """Value passed to observer must be POST-reducer (what's in the cell)."""
        events = []
        _, _, state = _built_state(
            "g", lambda: PARENT.declare(count=0, reducers={"count": operator.add})
        )
        state.subscribe_writes(lambda idx, ctx, val: events.append(val))

        state["g", "count"] = 1
        state["g", "count"] = 2
        state["g", "count"] = 3
        # Reducer accumulates: 0+1=1, 1+2=3, 3+3=6
        assert events == [1, 3, 6]


class TestPushRefObserver:
    def test_push_ref_writes_also_fire_observer(self):
        """Push-ref hop must emit an observer event on the target cell too."""
        events = []
        with GraphOp(name="g") as g:
            PARENT.declare(count=0, reducers={"count": operator.add})
            node = FuncOp(name="node", code_fn=lambda: {"count": 5}, inputs={})
            node["count"] >> PARENT["count"]
            START >> node >> END
        g.build()
        schema = StateSchema(g)
        state = MemoryState(schema)
        state.subscribe_writes(lambda idx, ctx, val: events.append((idx, val)))

        node_idx = schema.get_index("g.node", "count")
        shared_idx = schema.get_index("g", "count")

        state["g.node", "count"] = 5

        # Two events: op-local write + push-ref target write (reducer-merged)
        assert events == [(node_idx, 5), (shared_idx, 5)]


class TestMultipleObservers:
    def test_observers_fire_in_registration_order(self):
        order = []
        _, _, state = _built_state("g", lambda: PARENT.declare(x=0))

        state.subscribe_writes(lambda idx, ctx, val: order.append("first"))
        state.subscribe_writes(lambda idx, ctx, val: order.append("second"))
        state.subscribe_writes(lambda idx, ctx, val: order.append("third"))

        state["g", "x"] = 1
        assert order == ["first", "second", "third"]


class TestUnsubscribe:
    def test_unsubscribe_stops_notifications(self):
        events = []
        cb = lambda idx, ctx, val: events.append(val)
        _, _, state = _built_state("g", lambda: PARENT.declare(x=0))
        state.subscribe_writes(cb)

        state["g", "x"] = 1
        state.unsubscribe_writes(cb)
        state["g", "x"] = 2

        assert events == [1]

    def test_unsubscribe_unknown_is_noop(self):
        _, _, state = _built_state("g", lambda: PARENT.declare(x=0))
        # Doesn't raise even if callback never registered.
        state.unsubscribe_writes(lambda i, c, v: None)


class TestFastPath:
    def test_no_observers_slot_is_empty_list(self):
        _, _, state = _built_state("g", lambda: PARENT.declare(x=0))
        assert state._observers == []

    def test_after_unsubscribe_all_fast_path_restored(self):
        events = []
        cb = lambda idx, ctx, val: events.append(val)
        _, _, state = _built_state("g", lambda: PARENT.declare(x=0))
        state.subscribe_writes(cb)
        state.unsubscribe_writes(cb)
        # Writes still work; fast path (empty observers) is active.
        state["g", "x"] = 999
        assert events == []


class TestObserverExceptionsPropagate:
    def test_observer_exception_bubbles_up(self):
        """An observer that raises should not silently swallow — bubble up."""
        _, _, state = _built_state("g", lambda: PARENT.declare(x=0))

        def bad(idx, ctx, val):
            raise RuntimeError("observer boom")

        state.subscribe_writes(bad)

        import pytest as _pt

        with _pt.raises(RuntimeError, match="observer boom"):
            state["g", "x"] = 5
