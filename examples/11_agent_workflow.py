"""Tutorial 11: Agent Workflow — AI Agent su dung tools.

Can: OPENAI_API_KEY trong .env + llm:gpt-4o-mini trong resources.yaml

Hoc duoc:
- Tool-calling agent pattern: Query -> LLM -> Execute Tools -> Loop -> Final Answer
- @graph.loop() decorator cho agent loop (stop khi LLM khong goi tool nua)
- Tool definitions (OpenAI function calling format)
- Process tool_calls va feed results back vao messages
- max_iterations de tranh infinite loops

Chay: uv run python examples/11_agent_workflow.py
"""

import asyncio
import json
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent.parent / ".env")

from hush.core import END, PARENT, START, GraphOp, Hush, graph
from hush.core.ops import op
from hush.providers import LLMOp

# =============================================================================
# Tool definitions (OpenAI function calling format)
# =============================================================================


def calculator(expression: str) -> dict:
    """Evaluate math expressions."""
    try:
        result = eval(expression, {"__builtins__": {}}, {})
        return {"result": str(result)}
    except Exception as e:
        return {"error": str(e)}


def search(query: str) -> dict:
    """Mock search — in production, call real search API."""
    knowledge = {
        "python": "Python is a high-level programming language created by Guido van Rossum in 1991.",
        "hush": "Hush is an async workflow orchestration engine for GenAI applications.",
        "vietnam": "Vietnam is a country in Southeast Asia. Capital: Hanoi. Population: ~100 million.",
        "machine learning": "Machine learning is a subset of AI that learns patterns from data.",
    }
    query_lower = query.lower()
    for key, value in knowledge.items():
        if key in query_lower:
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
                    "expression": {
                        "type": "string",
                        "description": "Math expression to evaluate",
                    }
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


# =============================================================================
# Agent ops
# =============================================================================


@op(rust="./rust_ops::pipeline::init_agent")
def init_agent(query: str):
    """Khoi tao agent state."""
    return {
        "messages": [
            {
                "role": "system",
                "content": "You are a helpful assistant with access to tools. "
                "Use them when needed.",
            },
            {"role": "user", "content": query},
        ],
        "done": False,
        "answer": "",
    }


@op(rust="./rust_ops::pipeline::process_agent_response")
def process_response(content, tool_calls, messages):
    """Xu ly response tu LLM: execute tools hoac return final answer."""
    new_messages = list(messages)

    # Them assistant message
    assistant_msg = {"role": "assistant", "content": content or ""}
    if tool_calls:
        assistant_msg["tool_calls"] = tool_calls
    new_messages.append(assistant_msg)

    if tool_calls:
        # Execute tung tool va them ket qua vao messages
        for tool_call in tool_calls:
            func_name = tool_call["function"]["name"]
            args = json.loads(tool_call["function"]["arguments"])

            if func_name in TOOLS:
                result = TOOLS[func_name](**args)
            else:
                result = {"error": f"Unknown tool: {func_name}"}

            new_messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tool_call["id"],
                    "content": json.dumps(result),
                }
            )

        return {
            "messages": new_messages,
            "done": False,
            "answer": "",
        }
    else:
        # Khong co tool calls -> LLM da co final answer
        return {
            "messages": new_messages,
            "done": True,
            "answer": content or "",
        }


# =============================================================================
# @graph.loop — agent loop decorator
# =============================================================================


@graph.loop(until="done == True", max_iterations=10)
def agent_loop(messages, done, answer):
    """Repeat LLM -> process until done == True."""
    # Goi LLM voi tools
    llm = LLMOp.of(
        resource="gpt-4o-mini",
        messages=messages,
        tools=TOOL_DESCRIPTIONS,
    )

    # Xu ly response: execute tools hoac set done=True
    proc = process_response(
        content=llm["content"],
        tool_calls=llm["tool_calls"],
        messages=messages,
    )

    # Update loop state
    proc["messages"] >> PARENT["messages"]
    proc["done"] >> PARENT["done"]
    proc["answer"] >> PARENT["answer"]

    START >> llm >> proc >> END


# =============================================================================
# Vi du 1: Simple tool-calling agent
# =============================================================================


async def example_1_simple_agent():
    """Agent loop: LLM goi tools -> execute -> feed back -> repeat."""
    print("=" * 50)
    print("Vi du 1: Tool-calling Agent (@graph.loop)")
    print("=" * 50)

    import os

    if not os.environ.get("OPENAI_API_KEY"):
        print("  Skipped — OPENAI_API_KEY chua set")
        return

    with GraphOp(name="simple-agent") as graph:
        init = init_agent(query=PARENT["query"])

        loop = agent_loop(
            messages=init["messages"],
            done=init["done"],
            answer=init["answer"],
        )

        loop["answer"] >> PARENT["answer"]
        START >> init >> loop >> END

    engine = Hush(graph)

    # Test queries
    queries = [
        "What is 25 * 4 + 100?",
        "Tell me about Python programming language.",
        "What is 15 * 7, and also tell me about machine learning?",
    ]

    for query in queries:
        result = await engine.run(inputs={"query": query})
        answer = result.get("answer", "No answer")
        print(f"\n  Q: {query}")
        print(f"  A: {str(answer)[:150]}{'...' if len(str(answer)) > 150 else ''}")


# =============================================================================
# Main
# =============================================================================


async def main():
    await example_1_simple_agent()


if __name__ == "__main__":
    asyncio.run(main())
