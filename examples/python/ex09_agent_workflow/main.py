"""09 Agent Workflow — tool-calling agent built on @graph.loop.

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


@graph.loop(until="done == True", max_iterations=10)
def agent_loop(messages, done, answer):
    """Repeat LLM → process until `done` is True."""
    llm = LLMOp.of(
        resource="gpt-4o-mini",
        messages=messages,
        tools=TOOL_DESCRIPTIONS,
    )
    proc = process_response(
        content=llm["content"],
        tool_calls=llm["tool_calls"],
        messages=messages,
    )
    proc["messages"] >> PARENT["messages"]
    proc["done"] >> PARENT["done"]
    proc["answer"] >> PARENT["answer"]
    START >> llm >> proc >> END


@graph
def agent(query):
    """init → loop until the LLM stops calling tools."""
    init = init_agent(query=query)
    loop = agent_loop(
        messages=init["messages"],
        done=init["done"],
        answer=init["answer"],
    )
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
