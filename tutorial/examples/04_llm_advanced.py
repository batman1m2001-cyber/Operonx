"""Tutorial 04: LLM Nâng cao — Structured output, Tool calling, Multi-turn chat.

Cần: OPENAI_API_KEY trong .env + resources.yaml

Học được:
- Structured output (JSON schema) — force LLM trả về JSON
- Tool use / Function calling — LLM gọi tools
- Multi-turn conversation — giữ history qua nhiều lượt
- Generation parameters — temperature, max_tokens

Chạy: cd tutorial && uv run python examples/04_llm_advanced.py
"""

import asyncio
import json
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent.parent / ".env")

from hush.core import END, PARENT, START, GraphOp, Hush
from hush.core.ops.transform.func_op import op
from hush.providers import LLMOp, PromptOp


async def example_1_structured_output():
    """Force LLM trả về JSON theo schema."""
    print("=" * 50)
    print("Ví dụ 1: Structured Output (JSON Schema)")
    print("=" * 50)

    with GraphOp(name="sentiment-analysis") as graph:
        p = PromptOp.of(
            template={
                "system": "Phân tích sentiment của văn bản. Trả về JSON.",
                "user": "{text}",
            },
            text=PARENT["text"],
        )
        llm = LLMOp.of(
            resource="gpt-4o-mini",
            messages=p["messages"],
            response_format={
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
            outputs={"content": PARENT["analysis"]},
        )
        START >> p >> llm >> END

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

    @op
    def process_response(content, tool_calls):
        return {
            "has_tool_call": bool(tool_calls),
            "tool_result": (
                execute_tool(json.loads(tool_calls[0]["function"]["arguments"])["expression"])
                if tool_calls
                else None
            ),
            "llm_response": content,
        }

    with GraphOp(name="tool-calling") as graph:
        p = PromptOp.of(
            template={
                "system": "Bạn có thể tính toán. Dùng tool calculate khi cần.",
                "user": "{query}",
            },
            query=PARENT["query"],
        )
        llm = LLMOp.of(
            resource="gpt-4o-mini",
            messages=p["messages"],
            tools=tools,
            tool_choice="auto",
        )
        proc = process_response(
            content=llm["content"],
            tool_calls=llm["tool_calls"],
        )
        START >> p >> llm >> proc >> END

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

    @op
    def update_history(history, message, response):
        return {
            "new_history": history
            + [
                {"role": "user", "content": message},
                {"role": "assistant", "content": response},
            ]
        }

    with GraphOp(name="multi-turn-chat") as graph:
        p = PromptOp.of(
            template={
                "system": "Bạn là assistant hữu ích. Trả lời ngắn gọn.",
                "user": "{message}",
            },
            conversation_history=PARENT["history"],
            message=PARENT["message"],
        )
        llm = LLMOp.of(
            resource="gpt-4o-mini",
            messages=p["messages"],
            temperature=0.7,
            max_tokens=200,
            outputs={"content": PARENT["response"]},
        )
        upd = update_history(
            history=PARENT["history"],
            message=PARENT["message"],
            response=llm["content"],
        )
        START >> p >> llm >> upd >> END

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
