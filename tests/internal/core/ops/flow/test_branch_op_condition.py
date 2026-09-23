"""``if_(predicate_op(...), target)`` — a branch that tests an op, not a Ref.

Before this, every boolean gate cost two declarations: an op returning
``{"is_short": n <= CAP}`` and a branch reading ``short["is_short"]``. The
op existed only to put a bool in a dict so the branch could take it out
again. Now the predicate is written as the boolean it is::

    @op
    def is_short(n_turns: int = 0) -> bool:
        return n_turns <= CAP

    fmt >> if_(is_short(n_turns=fmt["n_turns"]), kid).else_(scanner)

Three things had to change for that line to work, and each has a way of
failing quietly, so each is tested here:

1. A bare ``return`` value is wrapped into the op's single output. Before,
   it crashed in ``store_result``; worse, ``return False`` would have hit
   the falsy early-out and written nothing, leaving a cell that reads as
   "never ran" rather than "said no".
2. The inline predicate is registered but has no incoming edge, so
   ``__rshift__`` routes the source through it. Without that it never runs
   and the branch routes on ``None`` — every call taking the else arm,
   silently.
3. Auto-naming reads the *calling line* for an assignment target. An op
   built inline has none, so the parser latched onto a nearby line and the
   predicate overwrote whatever op owned that name.
"""

import pytest

from operonx.core import Operon
from operonx.core.ops.base import END, START
from operonx.core.ops.flow.branch_op import if_
from operonx.core.ops.graph.graph_op import GraphOp
from operonx.core.ops.transform.func_op import FuncOp, op

CAP = 6


@op
def fmt_fn(n: int = 0) -> dict:
    return {"n_turns": n}


@op
def is_short(n_turns: int = 0) -> bool:
    return n_turns <= CAP


@op
def kid_path() -> dict:
    return {"w": "kid"}


@op
def scanner() -> dict:
    return {"w": "scanner"}


def _edges(graph):
    return sorted((e.from_node, e.to_node) for e in graph._edges.values())


def _graph(n):
    with GraphOp(name="g") as g:
        fmt = fmt_fn(n=n)
        kid = kid_path()
        scan = scanner()
        START >> fmt >> if_(is_short(n_turns=fmt["n_turns"]), kid).else_(scan)
        kid >> END
        scan >> END
    return g


# ── the wiring the inline form has to invent ──────────────────────────


class TestAutoWiring:
    def test_the_source_is_routed_through_the_predicate(self):
        """source → predicate → branch, not source → branch."""
        g = _graph(3)
        assert ("fmt", "is_short") in _edges(g)
        assert ("is_short", "route_1") in _edges(g)
        assert ("fmt", "route_1") not in _edges(g), (
            "the branch must wait on the predicate, not race it"
        )

    def test_the_branch_still_wires_its_own_arms(self):
        g = _graph(3)
        assert ("route_1", "kid") in _edges(g)
        assert ("route_1", "scan") in _edges(g)

    def test_a_hand_wired_predicate_is_left_alone(self):
        """Only the inline form needs adopting; an explicit chain is intact."""
        with GraphOp(name="g") as g:
            fmt = fmt_fn(n=1)
            pred = is_short(n_turns=fmt["n_turns"])
            kid = kid_path()
            scan = scanner()
            START >> fmt >> pred
            pred >> if_(pred, kid).else_(scan)
            kid >> END
            scan >> END
        # assigned to `pred`, so auto_name has a real LHS to read
        assert ("fmt", "pred") in _edges(g)
        # already had a predecessor, so no second edge was invented for it
        assert ("pred", "route_1") in _edges(g)
        assert len([e for e in _edges(g) if e[1] == "pred"]) == 1


# ── routing, end to end ───────────────────────────────────────────────


class TestRouting:
    @pytest.mark.parametrize("n,want", [(3, "kid"), (6, "kid"), (7, "scanner"), (99, "scanner")])
    async def test_the_predicate_decides_the_arm(self, n, want):
        out = await Operon(_graph(n)).run(inputs={"n": n})
        assert out["w"] == want

    async def test_false_is_an_answer_not_a_missing_value(self):
        """The whole point: `return False` must reach state as False.

        It is a falsy dict value away from being dropped, and a dropped
        cell reads downstream as "the op did not run".
        """
        out = await Operon(_graph(99)).run(inputs={"n": 99})
        state = out["$state"]
        assert state["g.is_short", "value", None] is False


# ── a bare return value becomes a real output ─────────────────────────


class TestScalarOutput:
    def test_a_bool_annotation_declares_one_output(self):
        assert list(is_short(n_turns=1).outputs) == ["value"]

    def test_a_dict_op_keeps_its_ast_derived_keys(self):
        assert list(fmt_fn(n=1).outputs) == ["n_turns"]

    def test_an_explicit_return_key_wins_over_the_fallback(self):
        """`return_keys` names the cell a bare return lands in."""
        o = FuncOp(name="p", code_fn=lambda n=0: n <= CAP, return_keys=["is_short"], inputs={})
        assert list(o.outputs) == ["is_short"]
        assert o._scalar_output_name() == "is_short"

    def test_an_unannotated_function_is_left_alone(self):
        """We cannot tell a bare return from a dict built dynamically."""

        @op
        def untyped(n=0):
            return {"b": n}

        assert list(untyped(n=1).outputs) == ["b"]


# ── refusing what it cannot answer ────────────────────────────────────


class TestRejects:
    def test_an_op_with_two_outputs_is_refused(self):
        """`if_(route_check, ...)` on {is_heavy_kw, keyword} has no answer."""

        @op
        def two_outputs(n: int = 0) -> dict:
            return {"a": n, "b": n}

        with GraphOp(name="g"):
            with pytest.raises(ValueError, match="exactly one output"):
                if_(two_outputs(n=1), "x").else_("y")

    def test_an_op_with_no_outputs_is_refused(self):
        @op
        def silent(n=0):
            pass

        with GraphOp(name="g"):
            with pytest.raises(ValueError, match="declares none"):
                if_(silent(n=1), "x").else_("y")

    def test_the_error_names_both_ways_out(self):
        @op
        def two_outputs(n: int = 0) -> dict:
            return {"a": n, "b": n}

        with GraphOp(name="g"):
            with pytest.raises(ValueError) as exc:
                if_(two_outputs(n=1), "x").else_("y")
        msg = str(exc.value)
        assert "['a', 'b']" in msg, "say which outputs it found"
        assert "scalar return annotation" in msg

    def test_a_plain_function_is_not_a_condition(self):
        """An undecorated callable is a likely typo, not an op."""
        with GraphOp(name="g"):
            with pytest.raises(TypeError, match="must be a Ref or an op"):
                if_(lambda: True, "x").else_("y")


# ── the naming bug the inline form exposed ────────────────────────────


class TestInlinePredicateNaming:
    def test_an_inline_predicate_does_not_steal_a_nearby_name(self):
        """auto_name reads the calling line; inline there is no assignment.

        `scan = scanner()` sits one line above, and the predicate used to
        be named `scan` and overwrite it — losing a node with a warning
        nobody reads.
        """
        g = _graph(3)
        assert "is_short" in g._ops, "the predicate takes its function's name"
        assert g._ops["scan"].type == "code", "scanner must survive intact"
        assert g._ops["is_short"].type == "code"

    def test_two_inline_predicates_get_distinct_names(self):
        with GraphOp(name="g") as g:
            fmt = fmt_fn(n=1)
            a = kid_path()
            b = scanner()
            START >> fmt >> if_(is_short(n_turns=fmt["n_turns"]), a).else_(b)
            a >> if_(is_short(n_turns=fmt["n_turns"]), b).else_(b)
            b >> END
        names = [n for n in g._ops if n.startswith("is_short")]
        assert sorted(names) == ["is_short", "is_short_2"], names


# ── the classic form is untouched ─────────────────────────────────────


class TestRefFormStillWorks:
    def test_a_ref_condition_wires_source_to_branch_directly(self):
        with GraphOp(name="g") as g:
            fmt = fmt_fn(n=1)
            a = kid_path()
            b = scanner()
            START >> fmt >> if_(fmt["n_turns"] <= CAP, a).else_(b)
            a >> END
            b >> END
        assert ("fmt", "route_1") in _edges(g), "no predicate, no redirect"

    @pytest.mark.parametrize("n,want", [(3, "kid"), (99, "scanner")])
    async def test_a_ref_condition_still_routes(self, n, want):
        with GraphOp(name="g") as g:
            fmt = fmt_fn(n=n)
            a = kid_path()
            b = scanner()
            START >> fmt >> if_(fmt["n_turns"] <= CAP, a).else_(b)
            a >> END
            b >> END
        out = await Operon(g).run(inputs={"n": n})
        assert out["w"] == want
