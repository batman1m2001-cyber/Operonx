"""09 Agent Workflow — tool-calling agent with a back-edge inside @graph.

1.0.0 migration note: pre-1.0 versions used @graph(until="done == True",
max_iterations=10) as the loop control. That surface is gone; the loop is
now expressed via a back-edge and the Phase 3 cycle-rewrite pass builds a
hidden _GraphLoop under the hood. The max-iterations cap defaults to 1000
on synthesized loops — the branch is your primary exit.


Requires ``OPENAI_API_KEY`` in ``.env`` and ``llm:gpt-4o-mini`` in
``resources.yaml``. Run from this directory:

    uv sync
    cp .env.example .env  # fill in OPENAI_API_KEY
    uv run python main.py
"""

from __future__ import annotations

import asyncio
import json

import operonx
from operonx.core import END, PARENT, START, Operon, graph, op
from operonx.core.ops.flow.branch_op import if_
from operonx.providers import LLMOp

# ── Tool functions ──────────────────────────────────────────────────────


def calculator(expression: str) -> dict:
    try:
        return {"result": str(eval(expression, {"__builtins__": {}}, {}))}
    except Exception as e:
        return {"error": str(e)}


def search(query: str) -> dict:
    knowledge = {
        "python": "Python is a high-level programming language created by Guido van Rossum in 1991.",
        "operonx": "Operonx is an async workflow orchestration engine for GenAI applications.",
        "vietnam": "Vietnam is a country in Southeast Asia. Capital: Hanoi. Population: ~100 million.",
        "machine learning": "Machine learning is a subset of AI that learns patterns from data.",
    }
    q = query.lower()
    for key, value in knowledge.items():
        if key in q:
            return {"result": value}
    return {"result": "No information found."}


TOOLS = {"calculator": calculator, "search": search}

TOOL_DESCRIPTIONS = [
    {
        "type": "function",
        "function": {
            "name": "calculator",
            "description": "Evaluate mathematical expressions. Example: '25 * 4 + 100'",
            "parameters": {
                "type": "object",
                "properties": {
                    "expression": {"type": "string", "description": "Math expression to evaluate"}
                },
                "required": ["expression"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search",
            "description": "Search for factual information about a topic.",
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string", "description": "Search query"}},
                "required": ["query"],
            },
        },
    },
]


# ── Agent ops ───────────────────────────────────────────────────────────


@op
def init_agent(query: str):
    return {
        "messages": [
            {
                "role": "system",
                "content": "You are a helpful assistant with access to tools. Use them when needed.",
            },
            {"role": "user", "content": query},
        ],
        "done": False,
        "answer": "",
    }


@op
def process_response(content, tool_calls, messages):
    """Run tool calls when present, otherwise mark `done`."""
    new_messages = list(messages)
    assistant = {"role": "assistant", "content": content or ""}
    if tool_calls:
        assistant["tool_calls"] = tool_calls
    new_messages.append(assistant)

    if tool_calls:
        for tc in tool_calls:
            fn_name = tc["function"]["name"]
            args = json.loads(tc["function"]["arguments"])
            if fn_name in TOOLS:
                tool_result = TOOLS[fn_name](**args)
            else:
                tool_result = {"error": f"Unknown tool: {fn_name}"}
            new_messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tc["id"],
                    "content": json.dumps(tool_result),
                }
            )
        return {"messages": new_messages, "done": False, "answer": ""}

    return {"messages": new_messages, "done": True, "answer": content or ""}


# ── Loop body + outer graph ─────────────────────────────────────────────


@graph
def agent_loop():
    """Repeat LLM → process until ``done`` is True. The back-edge from
    ``proc`` to ``llm`` is what makes this a loop; the Phase 3 rewrite
    pass turns it into a hidden ``_GraphLoop`` at build time."""
    PARENT.declare(messages=[], done=False, answer="")
    llm = LLMOp.of(
        resource="gpt-4o-mini",
        messages=PARENT["messages"],
        tools=TOOL_DESCRIPTIONS,
    )
    proc = process_response(
        content=llm["content"],
        tool_calls=llm["tool_calls"],
        messages=PARENT["messages"],
    )
    proc["messages"] >> PARENT["messages"]
    proc["done"] >> PARENT["done"]
    proc["answer"] >> PARENT["answer"]
    START >> llm >> proc >> if_(proc["done"] == True, END).else_(llm)  # noqa: E712


@graph
def agent(query):
    """init → loop until the LLM stops calling tools."""
    init = init_agent(query=query)
    loop = agent_loop()
    # Wire init's outputs into the loop's shared cells at run start.
    init["messages"] >> loop["messages"]
    init["done"] >> loop["done"]
    init["answer"] >> loop["answer"]
    loop["answer"] >> PARENT["answer"]
    START >> init >> loop >> END


async def main() -> None:
    operonx.bootstrap()

    queries = [
        ("calc", "What is 25 * 4 + 100?"),
        ("search", "Tell me about Python programming language."),
        ("combined", "What is 15 * 7, and also tell me about machine learning?"),
    ]
    for label, query in queries:
        try:
            g = agent(query=PARENT["query"])
            result = await Operon(g).run(inputs={"query": query})
            print(f"[{label}] {result.get('answer', result)}")
        except Exception as e:
            print(f"[{label}] error: {e!r}")


if __name__ == "__main__":
    asyncio.run(main())
