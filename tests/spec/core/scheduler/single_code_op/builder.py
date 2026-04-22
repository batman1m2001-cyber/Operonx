"""Fixture: `double(x=5) -> {result: 10}`."""

from operon.core import END, PARENT, START, GraphOp

from tests.spec._ops import double


def build_graph() -> GraphOp:
    with GraphOp(name="main") as graph:
        step = double(x=PARENT["x"], outputs={"result": PARENT["result"]})
        START >> step >> END
    return graph
