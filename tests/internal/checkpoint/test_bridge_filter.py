"""Tests for bridge honouring @op(exclude/include/observe_max) filters.

Verifies the emission-source filter: writes from ops flagged with exclude/
include are skipped BEFORE reaching the checkpointer, and observe_max
raises a loud ObserveBudgetExceeded when an op exceeds its emit cap.
"""

import operator

import pytest

from operonx.checkpoint import InMemoryCheckpointer, ObserveBudgetExceeded, bind_checkpointer
from operonx.checkpoint.bridge import bind_observe_budget, should_emit_for_channel
from operonx.core.ops.graph.graph_op import END, PARENT, START, GraphOp
from operonx.core.ops.transform.func_op import FuncOp, op
from operonx.core.states.schema import StateSchema
from operonx.core.states.state import MemoryState


def _passthrough(**kwargs):
    return kwargs


# ---------------------------------------------------------------------------
# should_emit_for_channel — unit level
# ---------------------------------------------------------------------------


class TestShouldEmit:
    def test_no_op_always_emits(self):
        assert should_emit_for_channel(None, "any", "checkpoint")

    def test_bare_op_always_emits(self):
        @op
        def bare():
            return {}

        inst = bare()
        assert should_emit_for_channel(inst, "x", "checkpoint")
        assert should_emit_for_channel(inst, "x", "trace")

    def test_exclude_wildcard_blocks_both(self):
        @op(exclude=["tokens"])
        def transcribe():
            return {}

        inst = transcribe()
        assert not should_emit_for_channel(inst, "tokens", "checkpoint")
        assert not should_emit_for_channel(inst, "tokens", "trace")
        assert should_emit_for_channel(inst, "text", "checkpoint")

    def test_exclude_per_channel(self):
        @op(exclude={"trace": ["pii"], "checkpoint": ["blob"]})
        def sensitive():
            return {}

        inst = sensitive()
        assert not should_emit_for_channel(inst, "pii", "trace")
        assert should_emit_for_channel(inst, "pii", "checkpoint")  # not blocked here
        assert not should_emit_for_channel(inst, "blob", "checkpoint")
        assert should_emit_for_channel(inst, "blob", "trace")

    def test_include_wildcard_allowlist(self):
        @op(include=["summary"])
        def summarize():
            return {}

        inst = summarize()
        assert should_emit_for_channel(inst, "summary", "checkpoint")
        assert not should_emit_for_channel(inst, "other", "checkpoint")

    def test_include_empty_silences_op(self):
        @op(include=[])
        def internal():
            return {}

        inst = internal()
        assert not should_emit_for_channel(inst, "anything", "checkpoint")

    def test_include_per_channel_leaves_other_unrestricted(self):
        """include={'trace': [...]} means trace is allowlisted; checkpoint
        has no restriction (sees everything)."""

        @op(include={"trace": ["summary"]})
        def flex():
            return {}

        inst = flex()
        # Trace: only "summary"
        assert should_emit_for_channel(inst, "summary", "trace")
        assert not should_emit_for_channel(inst, "other", "trace")
        # Checkpoint: sees everything
        assert should_emit_for_channel(inst, "summary", "checkpoint")
        assert should_emit_for_channel(inst, "other", "checkpoint")


# ---------------------------------------------------------------------------
# End-to-end: bridge honours op filter
# ---------------------------------------------------------------------------


def _built_with_filtered_node(exclude=None, include=None, observe_max=None):
    with GraphOp(name="g") as g:
        PARENT.declare(count=0)
        node = FuncOp(
            name="node",
            code_fn=_passthrough,
            inputs={},
            exclude=exclude,
            include=include,
            observe_max=observe_max,
        )
        node["count"] >> PARENT["count"]
        START >> node >> END
    g.build()
    schema = StateSchema(g)
    state = MemoryState(schema)
    # Build op_registry as bridge expects.
    op_registry = {"g.node": node, "g": g}
    return g, schema, state, node, op_registry


class TestBridgeExcludeFilter:
    def test_excluded_var_not_captured(self):
        _, _, state, _, registry = _built_with_filtered_node(exclude=["count"])
        cp = InMemoryCheckpointer()
        bind_checkpointer(state, cp, lambda: 0, op_registry=registry)

        state["g.node", "count"] = 5

        # node.count filtered → checkpointer sees nothing from node.
        # Push-ref target (g.count) has no filter → captured.
        updates = cp.get_updates(0) if 0 in cp.list_steps() else {}
        assert ("g.node", "count", ("main",)) not in updates
        assert ("g", "count", ("main",)) in updates

    def test_non_excluded_var_still_captured(self):
        _, _, state, _, registry = _built_with_filtered_node(exclude=["other"])
        cp = InMemoryCheckpointer()
        bind_checkpointer(state, cp, lambda: 0, op_registry=registry)

        state["g.node", "count"] = 5

        assert ("g.node", "count", ("main",)) in cp.get_updates(0)


class TestBridgeIncludeFilter:
    def test_include_allowlist(self):
        _, _, state, _, registry = _built_with_filtered_node(include=["count"])
        cp = InMemoryCheckpointer()
        bind_checkpointer(state, cp, lambda: 0, op_registry=registry)

        state["g.node", "count"] = 5
        assert ("g.node", "count", ("main",)) in cp.get_updates(0)

    def test_include_empty_silences_op_writes(self):
        _, _, state, _, registry = _built_with_filtered_node(include=[])
        cp = InMemoryCheckpointer()
        bind_checkpointer(state, cp, lambda: 0, op_registry=registry)

        state["g.node", "count"] = 5

        # Op-local write filtered out; push-ref target (unfiltered graph cell)
        # still captured.
        updates = cp.get_updates(0) if 0 in cp.list_steps() else {}
        assert ("g.node", "count", ("main",)) not in updates


class TestObserveBudgetExceeded:
    """The budget lives in ``bind_observe_budget``, not ``bind_checkpointer``.

    It used to be counted inside the checkpointer's closure, which the
    engine only builds when a checkpointer was passed — so the circuit
    breaker did nothing under a plain ``engine.run()``. These tests bind
    it directly; ``TestObserveBudgetWithoutCheckpointer`` covers the wiring.
    """

    def test_within_budget_no_raise(self):
        _, _, state, _, registry = _built_with_filtered_node(observe_max=5)
        bind_observe_budget(state, registry)

        for _ in range(5):
            state["g.node", "count"] = 1
        # 5 emissions == cap; should not raise (limit is inclusive).

    def test_exceed_budget_raises(self):
        _, _, state, _, registry = _built_with_filtered_node(observe_max=3)
        bind_observe_budget(state, registry)

        state["g.node", "count"] = 1
        state["g.node", "count"] = 2
        state["g.node", "count"] = 3
        with pytest.raises(ObserveBudgetExceeded) as exc_info:
            state["g.node", "count"] = 4  # 4th event exceeds cap of 3

        err = exc_info.value
        assert err.op == "g.node"
        assert err.count == 4
        assert err.limit == 3

    def test_a_checkpointer_does_not_double_count(self):
        """Both were bound at once during the move; the budget halved."""
        _, _, state, _, registry = _built_with_filtered_node(observe_max=3)
        bind_checkpointer(state, InMemoryCheckpointer(), lambda: 0, op_registry=registry)
        bind_observe_budget(state, registry)

        for _ in range(3):
            state["g.node", "count"] = 1
        with pytest.raises(ObserveBudgetExceeded):
            state["g.node", "count"] = 1

    def test_no_budget_declared_binds_nothing(self):
        _, _, state, _, registry = _built_with_filtered_node()
        assert bind_observe_budget(state, registry) is None

    def test_filtered_writes_do_not_count(self):
        """Vars silenced by exclude should not consume the observe_max budget.

        ``ObserveBudgetExceeded``'s message offers ``@op(exclude=[...])``
        as a fix, so a var no observer ever sees has to be free.
        """
        with GraphOp(name="g") as g:
            PARENT.declare(count=0)
            node = FuncOp(
                name="node",
                code_fn=_passthrough,
                inputs={},
                exclude=["count"],  # count filtered out
                observe_max=1,
            )
            node["count"] >> PARENT["count"]
            START >> node >> END
        g.build()
        schema = StateSchema(g)
        state = MemoryState(schema)
        bind_observe_budget(state, {"g.node": node, "g": g})

        # Every write is filtered out, so the counter never increments — no raise.
        for _ in range(10):
            state["g.node", "count"] = 1

    def test_a_trace_only_exclusion_still_counts(self):
        """``exclude={"trace": ...}`` leaves the checkpoint bus reading the
        var, so it is still an emitted event."""
        with GraphOp(name="g") as g:
            PARENT.declare(count=0)
            node = FuncOp(
                name="node",
                code_fn=_passthrough,
                inputs={},
                exclude={"trace": ["count"]},
                observe_max=1,
            )
            node["count"] >> PARENT["count"]
            START >> node >> END
        g.build()
        state = MemoryState(StateSchema(g))
        bind_observe_budget(state, {"g.node": node, "g": g})

        state["g.node", "count"] = 1
        with pytest.raises(ObserveBudgetExceeded):
            state["g.node", "count"] = 2


class TestObserveBudgetWithoutCheckpointer:
    """F4 — the breaker has to fire on the cheap path too.

    Measured before the fix: 50 frames emitted against a budget of 5, no
    error, because the counter only existed inside ``bind_checkpointer``.
    """

    @pytest.mark.asyncio
    async def test_runaway_generator_is_capped_under_plain_run(self):
        from operonx.core import Operon, op

        @op(observe_max=5)
        def tokens(n: int):
            for i in range(50):
                yield {"tok": i}

        with GraphOp(name="cap") as g:
            t = tokens(n=PARENT["n"])
            START >> t >> END

        with pytest.raises(ObserveBudgetExceeded) as exc_info:
            await Operon(g).run(inputs={"n": 1})
        assert exc_info.value.limit == 5

    @pytest.mark.asyncio
    async def test_an_op_within_budget_still_completes(self):
        from operonx.core import Operon, op

        @op(observe_max=50)
        def tokens(n: int):
            for i in range(3):
                yield {"tok": i}

        with GraphOp(name="ok") as g:
            t = tokens(n=PARENT["n"])
            START >> t >> END

        result = await Operon(g).run(inputs={"n": 1})
        assert len(result["tok"]) == 3


class TestBackwardCompatWithoutRegistry:
    def test_no_op_registry_captures_everything(self):
        """Old bridge call (no op_registry) should still capture unconditionally."""
        _, _, state, _, _ = _built_with_filtered_node(exclude=["count"])
        cp = InMemoryCheckpointer()
        # NB: op_registry omitted — filter should be a no-op.
        bind_checkpointer(state, cp, lambda: 0)

        state["g.node", "count"] = 5
        assert ("g.node", "count", ("main",)) in cp.get_updates(0)
