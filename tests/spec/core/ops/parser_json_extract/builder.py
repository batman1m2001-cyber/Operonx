"""Fixture: ParserOp extracts dot-path fields from a JSON input.

Locks down the Stage-7 architecture parity: both Python and Rust parse
identical text, walk the same dot path, and emit identical typed values.
"""

from operonx.core import END, PARENT, START, GraphOp
from operonx.core.ops import ParserOp


def build_graph() -> GraphOp:
    with GraphOp(name="main") as graph:
        p = ParserOp(
            format="json",
            extract=["user.name: str", "user.age: int"],
            inputs={"text": PARENT["text"]},
        )
        p["name"] >> PARENT["name"]
        p["age"] >> PARENT["age"]
        p["error"] >> PARENT["error"]
        START >> p >> END
    return graph
