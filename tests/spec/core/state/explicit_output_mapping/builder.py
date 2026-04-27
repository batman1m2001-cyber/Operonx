"""Fixture: rename op output via `outputs={"result": PARENT["renamed"]}`."""

from operonx.core import END, PARENT, START, GraphOp
from tests.spec._ops import double


def build_graph() -> GraphOp:
    with GraphOp(name="main") as graph:
        step = double(
            name="step",
            x=PARENT["x"],
            outputs={"result": PARENT["renamed"]},
        )
        START >> step >> END
    return graph
