"""Fixture: declarative ``inputs={"x": SCRATCH["k"]}`` post-resolves per call."""

from operonx.core import END, PARENT, SCRATCH, START, GraphOp
from tests.spec._ops import passthrough


def build_graph() -> GraphOp:
    with GraphOp(name="main") as graph:
        out = passthrough(
            name="out",
            value=SCRATCH["phase"],
            outputs={"value": PARENT["result"]},
        )
        START >> out >> END
    return graph
