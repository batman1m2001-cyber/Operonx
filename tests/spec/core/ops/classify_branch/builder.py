"""Fixture: `classify_size(n, threshold) -> {label}`."""

from operonx.core import END, PARENT, START, GraphOp
from tests.spec._ops import classify_size


def build_graph() -> GraphOp:
    with GraphOp(name="main") as graph:
        classify = classify_size(
            name="classify",
            n=PARENT["n"],
            threshold=PARENT["threshold"],
            outputs={"label": PARENT["label"]},
        )
        START >> classify >> END
    return graph
