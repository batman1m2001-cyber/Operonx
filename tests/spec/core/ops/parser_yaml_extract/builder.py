"""Fixture: ParserOp extracts from YAML."""

from operonx.core import END, PARENT, START, GraphOp
from operonx.core.ops import ParserOp


def build_graph() -> GraphOp:
    with GraphOp(name="main") as graph:
        p = ParserOp(
            format="yaml",
            extract=["user.name: str", "user.age: int"],
            inputs={"text": PARENT["text"]},
        )
        p["name"] >> PARENT["name"]
        p["age"] >> PARENT["age"]
        p["error"] >> PARENT["error"]
        START >> p >> END
    return graph
