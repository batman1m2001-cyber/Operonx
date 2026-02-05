"""Tutorial 11: Agent Workflow — AI Agent sử dụng tools.

Cần: OPENAI_API_KEY trong .env + llm:gpt-4o-mini trong resources.yaml

Học được:
- Tool-calling agent pattern: Query → LLM → Execute Tools → Loop → Final Answer
- WhileLoopNode cho agent loop (stop khi LLM không gọi tool nữa)
- Tool definitions (OpenAI function calling format)
- Process tool_calls và feed results back vào messages
- max_iterations để tránh infinite loops

Chạy: cd hush-tutorial && uv run python examples/11_agent_workflow.py
"""

import asyncio
import json
from pathlib import Path
from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent.parent / ".env")

from hush.core import Hush, GraphNode, CodeNode, START, END, PARENT
from hush.core.nodes.iteration.while_loop_node import WhileLoopNode
from hush.providers import LLMNode


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
                "properties": {
                    "query": {"type": "string", "description": "Search query"}
                },
                "required": ["query"],
            },
        },
    },
]


# =============================================================================
# Agent logic
# =============================================================================

def init_agent(query: str):
    """Khởi tạo agent state."""
    return {
        "messages": [
            {"role": "system", "content": "You are a helpful assistant with access to tools. Use them when needed."},
            {"role": "user", "content": query},
        ],
        "iteration": 0,
        "done": False,
        "final_answer": None,
    }


def process_llm_response(content, tool_calls, messages, iteration):
    """Xử lý response từ LLM: execute tools hoặc return final answer."""
    new_messages = messages.copy()

    # Thêm assistant message
    assistant_msg = {"role": "assistant", "content": content}
    if tool_calls:
        assistant_msg["tool_calls"] = tool_calls
    new_messages.append(assistant_msg)

    if tool_calls:
        # Execute từng tool và thêm kết quả vào messages
        for tool_call in tool_calls:
            func_name = tool_call["function"]["name"]
            args = json.loads(tool_call["function"]["arguments"])

            if func_name in TOOLS:
                result = TOOLS[func_name](**args)
            else:
                result = {"error": f"Unknown tool: {func_name}"}

            new_messages.append({
                "role": "tool",
                "tool_call_id": tool_call["id"],
                "content": json.dumps(result),
            })

        return {
            "new_messages": new_messages,
            "new_iteration": iteration + 1,
            "is_done": False,
            "answer": None,
        }
    else:
        # Không có tool calls → LLM đã có final answer
        return {
            "new_messages": new_messages,
            "new_iteration": iteration + 1,
            "is_done": True,
            "answer": content,
        }


# =============================================================================
# Ví dụ 1: Simple tool-calling agent
# =============================================================================

async def example_1_simple_agent():
    """Agent loop: LLM gọi tools → execute → feed back → repeat."""
    print("=" * 50)
    print("Ví dụ 1: Tool-calling Agent")
    print("=" * 50)

    import os
    if not os.environ.get("OPENAI_API_KEY"):
        print("  Skipped — OPENAI_API_KEY chưa set")
        return

    with GraphNode(name="simple-agent") as graph:
        init = CodeNode(
            name="init",
            code_fn=init_agent,
            inputs={"query": PARENT["query"]},
            outputs={"messages": PARENT, "iteration": PARENT, "done": PARENT, "final_answer": PARENT},
        )

        with WhileLoopNode(
            name="agent_loop",
            inputs={
                "messages": PARENT["messages"],
                "iteration": PARENT["iteration"],
                "done": PARENT["done"],
                "final_answer": PARENT["final_answer"],
            },
            stop_condition="done == True or iteration >= 5",
            max_iterations=10,
        ) as loop:
            # Gọi LLM với tools
            llm = LLMNode(
                name="llm",
                resource_key="gpt-4o-mini",
                inputs={
                    "messages": PARENT["messages"],
                    "tools": TOOL_DESCRIPTIONS,
                },
            )

            # Xử lý response
            process = CodeNode(
                name="process",
                code_fn=process_llm_response,
                inputs={
                    "content": llm["content"],
                    "tool_calls": llm["tool_calls"],
                    "messages": PARENT["messages"],
                    "iteration": PARENT["iteration"],
                },
            )

            # Update loop state
            process["new_messages"] >> PARENT["messages"]
            process["new_iteration"] >> PARENT["iteration"]
            process["is_done"] >> PARENT["done"]
            process["answer"] >> PARENT["final_answer"]

            START >> llm >> process >> END

        loop["final_answer"] >> PARENT["answer"]
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
        print(f"  A: {answer[:150]}{'...' if len(str(answer)) > 150 else ''}")


# =============================================================================
# Main
# =============================================================================

async def main():
    await example_1_simple_agent()


if __name__ == "__main__":
    asyncio.run(main())
