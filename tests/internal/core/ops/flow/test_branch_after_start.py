"""A branch can be the first op in a graph, branching on its inputs (E8).

``START >> if_(PARENT["n"] > 5, big).else_(small)`` has always routed.
The predicate form did not: ``START >> if_(is_big(n=PARENT["n"]), big)``
wired START straight to the branch, so the predicate had no incoming
edge, never ran, and the branch read an unset cell — every call took the
else arm, with only an "is not reachable" warning to show for it.
"""

import pytest

from operonx.core import Operon
from operonx.core.ops.base import END, PARENT, START
from operonx.core.ops.flow.branch_op import if_
from operonx.core.ops.graph.graph_op import GraphOp, graph
from operonx.core.ops.transform.func_op import op


@op
def is_big(n: int = 0) -> bool:
    return n > 5


@op
def big() -> dict:
    return {"w": "big"}


@op
def small() -> dict:
    return {"w": "small"}


@op
def other() -> dict:
    return {"o": "other"}


CASES = [(10, "big"), (1, "small")]


def _entries(g):
    return sorted(g.entries)


class TestRefCondition:
    @pytest.mark.parametrize("n,want", CASES)
    async def test_on_a_parent_input(self, n, want):
        with GraphOp(name="g") as g:
            b, s = big(), small()
            START >> if_(PARENT["n"] > 5, b).else_(s)
            b >> END
            s >> END
        out = await Operon(g).run(inputs={"n": n})
        assert out["w"] == want

    @pytest.mark.parametrize("n,want", CASES)
    async def test_on_a_graph_parameter(self, n, want):
        @graph
        def flow(n):
            b, s = big(), small()
            START >> if_(n > 5, b).else_(s)
            b >> END
            s >> END

        out = await Operon(flow, params={"n": None}).run(inputs={"n": n})
        assert out["w"] == want


class TestPredicateCondition:
    def _graph(self):
        with GraphOp(name="g") as g:
            b, s = big(), small()
            START >> if_(is_big(n=PARENT["n"]), b).else_(s)
            b >> END
            s >> END
        return g

    def test_start_feeds_the_predicate(self):
        """The predicate is the entry; the branch waits on it."""
        g = self._graph()
        assert _entries(g) == ["is_big"]

    @pytest.mark.parametrize("n,want", CASES)
    async def test_on_a_parent_input(self, n, want):
        out = await Operon(self._graph()).run(inputs={"n": n})
        assert out["w"] == want

    @pytest.mark.parametrize("n,want", CASES)
    async def test_on_a_graph_parameter(self, n, want):
        @graph
        def flow(n):
            b, s = big(), small()
            START >> if_(is_big(n=n), b).else_(s)
            b >> END
            s >> END

        out = await Operon(flow, params={"n": None}).run(inputs={"n": n})
        assert out["w"] == want

    @pytest.mark.parametrize("n,want", CASES)
    async def test_in_a_list_after_start(self, n, want):
        with GraphOp(name="g") as g:
            b, s = big(), small()
            o = other()
            START >> [if_(is_big(n=PARENT["n"]), b).else_(s), o]
            b >> END
            s >> END
            o >> END
        assert _entries(g) == ["is_big", "o"]
        out = await Operon(g).run(inputs={"n": n})
        assert out["w"] == want
        assert out["o"] == "other"
