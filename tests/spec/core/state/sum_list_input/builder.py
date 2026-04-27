"""Fixture: typed list input (`sum_list(xs: list[int])`)."""

from operonx.core import END, PARENT, START, GraphOp
from tests.spec._ops import sum_list


def build_graph() -> GraphOp:
    with GraphOp(name="main") as graph:
        summer = sum_list(
            name="summer",
            xs=PARENT["xs"],
            outputs={"total": PARENT["total"]},
        )
        START >> summer >> END
    return graph
