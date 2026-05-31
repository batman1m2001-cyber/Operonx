"""Fixture: ParserOp extracts from XML with bool type coercion."""

from operonx.core import END, PARENT, START, GraphOp
from operonx.core.ops import ParserOp


def build_graph() -> GraphOp:
    with GraphOp(name="main") as graph:
        p = ParserOp(
            format="xml",
            extract=["root.flag: bool", "root.name: str"],
            inputs={"text": PARENT["text"]},
        )
        p["flag"] >> PARENT["flag"]
        p["name"] >> PARENT["name"]
        p["error"] >> PARENT["error"]
        START >> p >> END
    return graph
