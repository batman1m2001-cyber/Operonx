"""Fixture: SCRATCH seeded via engine.run(scratch=...) is visible to entry op."""

from operonx.core import END, PARENT, START, GraphOp
from tests.spec._ops import passthrough, scratch_read


def build_graph() -> GraphOp:
    with GraphOp(name="main") as graph:
        r = scratch_read(name="reader", key=PARENT["k"])
        out = passthrough(
            name="out",
            value=r["value"],
            outputs={"value": PARENT["result"]},
        )
        START >> r >> out >> END
    return graph
