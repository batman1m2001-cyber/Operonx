"""The system under test for the TraceView, trajectory and rescore tests.

One graph with every shape a trace view has to read the same way live
and stored: a subgraph (``handle``) holding a branch, a real ``LLMOp``
(``reply``) answering a local stand-in model with a tool call, usage and
a price, and an op (``run_tool``) returning the tool message for that
call, as the agents' dispatch does::

    flow
    ├── handle            (subgraph)
    │   ├── classify
    │   ├── route         (branch: order → lookup_order, else small_talk)
    │   └── lookup_order | small_talk
    ├── reply             (LLMOp, tools=[lookup])
    └── run_tool

``CALLS`` counts every op body that ran, so a test can prove a graph did
not run.
"""

from __future__ import annotations

from collections import Counter

from operonx.core import END, START, graph, op
from operonx.core.ops import if_

CALLS: Counter = Counter()

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "lookup",
            "parameters": {"type": "object", "properties": {"order_id": {"type": "string"}}},
        },
    }
]


@op
def classify(text: str = "") -> dict:
    CALLS["classify"] += 1
    return {"kind": "order" if "order" in text else "chat"}


@op
def lookup_order(text: str = "") -> dict:
    CALLS["lookup_order"] += 1
    return {"found": True}


@op
def small_talk(text: str = "") -> dict:
    CALLS["small_talk"] += 1
    return {"found": False}


@op
def run_tool(tool_calls: list = None) -> dict:
    CALLS["run_tool"] += 1
    call = (tool_calls or [{}])[0]
    if not call:
        return {"tool_message": None}
    return {
        "tool_message": {
            "role": "tool",
            "tool_call_id": call.get("id"),
            "name": call.get("function", {}).get("name"),
            "content": "shipped",
            "status": "success",
        }
    }


@graph
def handle(text: str = ""):
    c = classify(text=text, name="classify")
    o = lookup_order(text=text, name="lookup_order")
    s = small_talk(text=text, name="small_talk")
    route = if_(c["kind"] == "order", o).else_(s)
    START >> c >> route
    o >> END
    s >> END


@graph
def flow(text: str = ""):
    from operonx.providers.ops import LLMOp

    h = handle(text=text, name="handle")
    llm = LLMOp.of(resource="bot", prompt={"user": "{text}"}, tools=TOOLS, text=text, name="reply")
    t = run_tool(tool_calls=llm["tool_calls"], name="run_tool")
    START >> h >> llm >> t >> END
