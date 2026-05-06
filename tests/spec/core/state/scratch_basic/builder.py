"""Fixture: imperative SCRATCH write in one op, read in a downstream op."""

from operonx.core import END, PARENT, START, GraphOp
from tests.spec._ops import passthrough, scratch_read, scratch_write


def build_graph() -> GraphOp:
    with GraphOp(name="main") as graph:
        w = scratch_write(
            name="writer",
            key=PARENT["k"],
            value=PARENT["v"],
        )
        r = scratch_read(
            name="reader",
            key=PARENT["k"],
            _signal=w["signal"],
        )
        out = passthrough(
            name="out",
            value=r["value"],
            outputs={"value": PARENT["result"]},
        )
        START >> w >> r >> out >> END
    return graph
