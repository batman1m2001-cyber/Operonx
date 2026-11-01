"""A branch on a value the build already knows: ``if_(action == "view", show)``.

A parameter a variant binds (``compile_graph(bind=)``, ``Operon(params=)``)
is a plain value inside the graph function, so comparing it gives a plain
``bool``. ``if_`` used to refuse that (a condition had to be a Ref or an
op), and a graph routing on a bound ``action`` needed an op that only
handed the value back, to get a Ref to test. Now the bool is the
condition: the build decides the arm, and the arms it does not take never
run. The same graph with ``action`` left a runtime input routes as before.
"""

import pytest

from operonx.core import Operon
from operonx.core.ops.base import END, START
from operonx.core.ops.flow.branch_op import BranchOp, if_
from operonx.core.ops.graph.graph_op import graph
from operonx.core.ops.transform.func_op import op

RAN = []


@op
def show() -> dict:
    RAN.append("show")
    return {"w": "show"}


@op
def edit() -> dict:
    RAN.append("edit")
    return {"w": "edit"}


@op
def ask() -> dict:
    RAN.append("ask")
    return {"w": "ask"}


@graph
def flow(action="view"):
    s, e, a = show(), edit(), ask()
    START >> if_(action == "view", s).if_(action == "edit", e).else_(a)
    s >> END
    e >> END
    a >> END


@pytest.fixture(autouse=True)
def _clear():
    RAN.clear()


class TestBoundParameter:
    @pytest.mark.parametrize("action,want", [("view", "show"), ("edit", "edit"), ("x", "ask")])
    async def test_takes_the_arm_the_build_decided_and_only_that_one(self, action, want):
        out = await Operon(flow, params={"action": action}).run(inputs={})
        assert out["w"] == want
        assert RAN == [want]

    async def test_the_conditions_read_as_what_they_were(self):
        engine = Operon(flow, params={"action": "edit"})
        (branch,) = [o for o in engine.graph._ops.values() if isinstance(o, BranchOp)]
        assert branch._case_descriptions == ["False", "True"]
        assert [c["condition"] for c in branch.serialize()["cases"]] == [
            {"constant": False},
            {"constant": True},
        ]


class TestRuntimeParameter:
    @pytest.mark.parametrize("action,want", [("view", "show"), ("edit", "edit"), ("x", "ask")])
    async def test_the_same_graph_still_routes_at_run_time(self, action, want):
        out = await Operon(flow, params={"action": None}).run(inputs={"action": action})
        assert out["w"] == want
        assert RAN == [want]


def test_a_condition_that_is_neither_ref_op_nor_bool_is_refused():
    with pytest.raises(TypeError, match="a Ref, an op or a bool, got str"):
        if_("view", "show").else_("ask")
