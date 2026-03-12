"""04 LLM Advanced — Run workflows with Hush Python engine.

Chạy: cd examples && uv run python 04_llm_advanced/demo.py
"""

import asyncio
import json

from hush.core import Hush
from workflow import build_multi_turn, build_structured_output, build_tool_calling


async def main():
    # 1. Structured Output
    print("=" * 50)
    print("1. Structured Output (JSON Schema)")
    print("=" * 50)
    engine = Hush(build_structured_output())
    texts = [
        "Sản phẩm tuyệt vời, rất hài lòng!",
        "Dịch vụ quá tệ, không bao giờ quay lại.",
        "Bình thường, không có gì đặc biệt.",
    ]
    for text in texts:
        result = await engine.run(inputs={"text": text})
        analysis = json.loads(result["analysis"])
        print(f"  '{text[:40]}' -> {analysis['sentiment']} ({analysis['confidence']})")

    # 2. Tool Calling
    print()
    print("=" * 50)
    print("2. Tool Calling")
    print("=" * 50)
    engine = Hush(build_tool_calling())
    result = await engine.run(inputs={"query": "Tính 25 * 4 + 100"})
    print(f"  Tool called: {result['has_tool_call']}")
    if result["tool_result"]:
        print(f"  Result: {result['tool_result']}")
    else:
        print(f"  LLM response: {result['llm_response']}")

    # 3. Multi-turn Chat
    print()
    print("=" * 50)
    print("3. Multi-turn Chat")
    print("=" * 50)
    engine = Hush(build_multi_turn())
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


if __name__ == "__main__":
    asyncio.run(main())
