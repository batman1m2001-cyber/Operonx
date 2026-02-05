"""Tutorial 04: LLM Nâng cao — Structured output, Tool calling, Multi-turn chat.

Cần: OPENAI_API_KEY trong .env + resources.yaml

Học được:
- Structured output (JSON schema) — force LLM trả về JSON
- Tool use / Function calling — LLM gọi tools
- Multi-turn conversation — giữ history qua nhiều lượt
- Generation parameters — temperature, max_tokens

Chạy: cd hush-tutorial && uv run python examples/04_llm_advanced.py
"""

import asyncio
import json
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent.parent / ".env")

from hush.core import END, PARENT, START, CodeNode, GraphNode, Hush
from hush.providers import LLMNode, PromptNode


async def example_1_structured_output():
    """Force LLM trả về JSON theo schema."""
    print("=" * 50)
    print("Ví dụ 1: Structured Output (JSON Schema)")
    print("=" * 50)

    with GraphNode(name="sentiment-analysis") as graph:
        prompt = PromptNode(
            name="prompt",
            inputs={
                "template": {
                    "system": "Phân tích sentiment của văn bản. Trả về JSON.",
                    "user": "{text}",
                },
                "text": PARENT["text"],
            },
        )
        llm = LLMNode(
            name="llm",
            resource_key="gpt-4o-mini",
            inputs={
                "messages": prompt["messages"],
                "response_format": {
                    "type": "json_schema",
                    "json_schema": {
                        "name": "sentiment_response",
                        "schema": {
                            "type": "object",
                            "properties": {
                                "sentiment": {
                                    "type": "string",
                                    "enum": ["positive", "negative", "neutral"],
                                },
                                "confidence": {"type": "number"},
                                "reason": {"type": "string"},
                            },
                            "required": ["sentiment", "confidence", "reason"],
                        },
                    },
                },
            },
            outputs={"content": PARENT["analysis"]},
        )
        START >> prompt >> llm >> END

    engine = Hush(graph)

    texts = [
        "Sản phẩm tuyệt vời, rất hài lòng!",
        "Dịch vụ quá tệ, không bao giờ quay lại.",
        "Bình thường, không có gì đặc biệt.",
    ]
    for text in texts:
        result = await engine.run(inputs={"text": text})
        analysis = json.loads(result["analysis"])
        print(f"  '{text[:40]}...' → {analysis['sentiment']} ({analysis['confidence']})")


async def example_2_tool_calling():
    """LLM sử dụng tools (function calling)."""
    print()
    print("=" * 50)
    print("Ví dụ 2: Tool Calling")
    print("=" * 50)

    # Định nghĩa tools
    tools = [
        {
            "type": "function",
            "function": {
                "name": "calculate",
                "description": "Tính toán biểu thức toán học",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "expression": {
                            "type": "string",
                            "description": "Biểu thức toán (vd: 2+3*4)",
                        }
                    },
                    "required": ["expression"],
                },
            },
        }
    ]

    # Tool implementation
    def execute_tool(expression: str) -> str:
        try:
            return str(eval(expression))  # Chỉ dùng cho demo, production cần safer eval
        except Exception as e:
            return f"Error: {e}"

    with GraphNode(name="tool-calling") as graph:
        prompt = PromptNode(
            name="prompt",
            inputs={
                "template": {
                    "system": "Bạn có thể tính toán. Dùng tool calculate khi cần.",
                    "user": "{query}",
                },
                "query": PARENT["query"],
            },
        )
        llm = LLMNode(
            name="llm",
            resource_key="gpt-4o-mini",
            inputs={
                "messages": prompt["messages"],
                "tools": tools,
                "tool_choice": "auto",
            },
        )
        process = CodeNode(
            name="process",
            code_fn=lambda content, tool_calls: {
                "has_tool_call": bool(tool_calls),
                "tool_result": (
                    execute_tool(json.loads(tool_calls[0]["function"]["arguments"])["expression"])
                    if tool_calls
                    else None
                ),
                "llm_response": content,
            },
            inputs={
                "content": llm["content"],
                "tool_calls": llm["tool_calls"],
            },
            outputs={
                "has_tool_call": PARENT,
                "tool_result": PARENT,
                "llm_response": PARENT,
            },
        )
        START >> prompt >> llm >> process >> END

    engine = Hush(graph)
    result = await engine.run(inputs={"query": "Tính 25 * 4 + 100"})

    print(f"  Tool called: {result['has_tool_call']}")
    if result["tool_result"]:
        print(f"  Kết quả: {result['tool_result']}")
    else:
        print(f"  LLM trả lời: {result['llm_response']}")


async def example_3_multi_turn_chat():
    """Multi-turn conversation giữ history."""
    print()
    print("=" * 50)
    print("Ví dụ 3: Multi-turn Chat")
    print("=" * 50)

    with GraphNode(name="multi-turn-chat") as graph:
        prompt = PromptNode(
            name="prompt",
            inputs={
                "template": {
                    "system": "Bạn là assistant hữu ích. Trả lời ngắn gọn.",
                    "user": "{message}",
                },
                "conversation_history": PARENT["history"],
                "message": PARENT["message"],
            },
        )
        llm = LLMNode(
            name="llm",
            resource_key="gpt-4o-mini",
            inputs={
                "messages": prompt["messages"],
                "temperature": 0.7,
                "max_tokens": 200,
            },
            outputs={"content": PARENT["response"]},
        )
        update = CodeNode(
            name="update_history",
            code_fn=lambda history, message, response: {
                "new_history": history
                + [
                    {"role": "user", "content": message},
                    {"role": "assistant", "content": response},
                ]
            },
            inputs={
                "history": PARENT["history"],
                "message": PARENT["message"],
                "response": PARENT["response"],
            },
            outputs={"new_history": PARENT},
        )
        START >> prompt >> llm >> update >> END

    engine = Hush(graph)

    # Simulate multi-turn conversation
    history = []
    messages = [
        "Xin chào! Tên tôi là An.",
        "Tôi thích lập trình Python.",
        "Tôi tên gì nhỉ?",
    ]

    for msg in messages:
        result = await engine.run(inputs={"message": msg, "history": history})
        print(f"  User: {msg}")
        print(f"  Bot:  {result['response']}")
        print()
        history = result["new_history"]


async def main():
    await example_1_structured_output()
    await example_2_tool_calling()
    await example_3_multi_turn_chat()


if __name__ == "__main__":
    asyncio.run(main())
