"""Fixture: `double -> add_one` chain."""

from operon.core import END, PARENT, START, GraphOp

from tests.spec._ops import add_one, double


def build_graph() -> GraphOp:
    with GraphOp(name="main") as graph:
        d = double(x=PARENT["x"])
        a = add_one(n=d["result"], outputs={"answer": PARENT["answer"]})
        START >> d >> a >> END
    return graph
