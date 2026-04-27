"""Fixture: `PromptOp` with system+user template and one variable."""

from operonx.core import END, PARENT, START, GraphOp
from operonx.providers.ops.prompt import PromptOp


def build_graph() -> GraphOp:
    with GraphOp(name="main") as graph:
        greet = PromptOp(
            name="greet",
            inputs={
                "template": {"system": "You are friendly.", "user": "Hello {name}!"},
                "name": PARENT["name"],
            },
            outputs={"messages": PARENT["messages"]},
        )
        START >> greet >> END
    return graph
