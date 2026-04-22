"""Fixture: `PromptOp` with a literal messages list (no rendering)."""

from operon.core import END, PARENT, START, GraphOp
from operon.providers.ops.prompt import PromptOp


def build_graph() -> GraphOp:
    with GraphOp(name="main") as graph:
        fixed = PromptOp(
            name="fixed",
            inputs={
                "template": [
                    {"role": "system", "content": "You are terse."},
                    {"role": "user", "content": "Summarize."},
                ],
            },
            outputs={"messages": PARENT["messages"]},
        )
        START >> fixed >> END
    return graph
