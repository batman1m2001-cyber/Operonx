"""Fixture: single-op graph with implicit `>> END` output forwarding."""

from operon.core import END, PARENT, START, GraphOp

from tests.spec._ops import lowercase


def build_graph() -> GraphOp:
    with GraphOp(name="main") as graph:
        lower = lowercase(
            name="lower",
            text=PARENT["text"],
            outputs={"result": PARENT["result"]},
        )
        START >> lower >> END
    return graph
