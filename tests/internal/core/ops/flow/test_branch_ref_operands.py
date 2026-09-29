"""A Ref inside a branch condition is read from its own op, wherever it sits.

Two findings, one mechanism: a condition is a Ref whose transforms may hold
other Refs, and the branch has to declare each of them as an input and
hand the value back when the condition runs.

* **S7.** ``if_(p["a"] >= p["b"], f)`` compared ``p["a"]`` with the Ref
  object ``p["b"]``. That comparison built one more Ref, which is truthy,
  so the first case won on every call — silently. ``p["b"]`` was never
  declared as a branch input either, so there was no value to read.
* **S8.** The values were keyed by variable name alone, so in
  ``(a["n"] > 5) & (b["n"] < 3)`` both sides read ``a``'s ``n``.
"""

import operator

import pytest

from operonx.core import Operon
from operonx.core.ops.base import END, PARENT, START
from operonx.core.ops.flow.branch_op import if_
from operonx.core.ops.graph.graph_op import GraphOp, graph
from operonx.core.ops.transform.func_op import op


@op
def pair(a: int = 0, b: int = 0) -> dict:
    return {"a": a, "b": b}


@op
def left(n: int = 0) -> dict:
    return {"n": n}


@op
def right(n: int = 0) -> dict:
    return {"n": n}


@op
def first() -> dict:
    return {"w": "first"}


@op
def second() -> dict:
    return {"w": "second"}


def _declared(branch) -> set:
    """``(source op, var)`` of every value the branch reads."""
    return {
        (p.value.raw_source, p.value.var) for name, p in branch.inputs.items() if name != "anchor"
    }


# ── S7: a Ref on the right-hand side ─────────────────────────────────────


def _pair_graph(condition, a, b):
    with GraphOp(name="g") as g:
        p = pair(a=a, b=b)
        f, s = first(), second()
        START >> p >> if_(condition(p), f).else_(s)
        f >> END
        s >> END
    return g


COMPARISONS = [operator.lt, operator.le, operator.gt, operator.ge, operator.eq, operator.ne]


class TestRefComparedWithRef:
    @pytest.mark.parametrize("cmp", COMPARISONS, ids=lambda c: c.__name__)
    @pytest.mark.parametrize("a,b", [(1, 100), (100, 1), (5, 5)])
    async def test_every_comparison_routes_on_both_values(self, cmp, a, b):
        g = _pair_graph(lambda p: cmp(p["a"], p["b"]), a, b)
        out = await Operon(g).run(inputs={})
        assert out["w"] == ("first" if cmp(a, b) else "second")

    @pytest.mark.parametrize("a,b,want", [(1, 100, "first"), (1, 2, "second")])
    async def test_arithmetic_on_two_refs(self, a, b, want):
        g = _pair_graph(lambda p: p["a"] + p["b"] > 10, a, b)
        out = await Operon(g).run(inputs={})
        assert out["w"] == want

    @pytest.mark.parametrize("a,b,want", [(1, 100, "second"), (100, 1, "first")])
    async def test_graph_inputs_on_both_sides(self, a, b, want):
        with GraphOp(name="g") as g:
            p = pair(a=PARENT["a"], b=PARENT["b"])
            f, s = first(), second()
            START >> p >> if_(PARENT["a"] >= PARENT["b"], f).else_(s)
            f >> END
            s >> END
        out = await Operon(g).run(inputs={"a": a, "b": b})
        assert out["w"] == want

    @pytest.mark.parametrize("a,b,want", [(1, 100, "second"), (100, 1, "first")])
    async def test_inside_a_subgraph(self, a, b, want):
        """Names change when a graph is nested; the lookup must not care."""

        @graph
        def inner(a, b):
            p = pair(a=a, b=b)
            f, s = first(), second()
            START >> p >> if_(p["a"] >= p["b"], f).else_(s)
            f >> END
            s >> END

        @graph
        def outer(a, b):
            i = inner(a=a, b=b)
            START >> i >> END

        out = await Operon(outer, params={"a": None, "b": None}).run(inputs={"a": a, "b": b})
        assert out["w"] == want

    def test_the_right_hand_ref_is_a_declared_input(self):
        with GraphOp(name="g"):
            p = pair(a=1, b=2)
            branch = if_(p["a"] >= p["b"], "x").else_("y")
        assert (p, "a") in _declared(branch)
        assert (p, "b") in _declared(branch)

    def test_matched_names_both_sides(self):
        with GraphOp(name="g"):
            p = pair(a=1, b=2)
            branch = if_(p["a"] < p["b"], "x").else_("y")
        assert branch._case_descriptions == ["a < b"]


# ── S8: two ops, one variable name ───────────────────────────────────────


def _two_sources_graph(x, y, condition):
    with GraphOp(name="g") as g:
        a = left(n=x)
        b = right(n=y)
        f, s = first(), second()
        START >> [a, b]
        [a, b] >> if_(condition(a, b), f).else_(s)
        f >> END
        s >> END
    return g


class TestSameVarFromTwoOps:
    @pytest.mark.parametrize(
        "x,y,want", [(10, 1, "first"), (10, 10, "second"), (1, 1, "second"), (1, 10, "second")]
    )
    async def test_each_ref_reads_its_own_op(self, x, y, want):
        g = _two_sources_graph(x, y, lambda a, b: (a["n"] > 5) & (b["n"] < 3))
        out = await Operon(g).run(inputs={})
        assert out["w"] == want

    @pytest.mark.parametrize("x,y,want", [(10, 1, "first"), (1, 10, "second")])
    async def test_compared_with_each_other(self, x, y, want):
        g = _two_sources_graph(x, y, lambda a, b: a["n"] > b["n"])
        out = await Operon(g).run(inputs={})
        assert out["w"] == want

    @pytest.mark.parametrize("x,y,want", [(10, 1, "first"), (1, 1, "second")])
    async def test_a_graph_input_and_an_op_output(self, x, y, want):
        with GraphOp(name="g") as g:
            a = left(n=PARENT["x"])
            f, s = first(), second()
            START >> a >> if_((a["n"] > 5) & (PARENT["n"] < 3), f).else_(s)
            f >> END
            s >> END
        out = await Operon(g).run(inputs={"x": x, "n": y})
        assert out["w"] == want

    def test_one_input_per_source(self):
        with GraphOp(name="g"):
            a = left(n=1)
            b = right(n=2)
            branch = if_((a["n"] > 5) & (b["n"] < 3), "x").else_("y")
        assert _declared(branch) == {(a, "n"), (b, "n")}

    def test_the_first_keeps_the_plain_name(self):
        """``branch(n=...)`` keeps working for the common, unambiguous case."""
        with GraphOp(name="g"):
            a = left(n=1)
            b = right(n=2)
            branch = if_((a["n"] > 5) & (b["n"] < 3), "x").else_("y")
        assert branch.inputs["n"].value.raw_source is a
