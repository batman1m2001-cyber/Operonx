"""03 LLM Chat — Run workflows with Hush Python engine.

Chạy: cd examples && uv run python 03_llm_chat/demo.py
"""

import asyncio
import sys

from hush.core import Hush
from workflow import build_basic_chat, build_chain_chat, build_summarize

sys.stdout.reconfigure(encoding="utf-8", errors="replace")


async def main():
    print("=" * 50)
    print("1. Basic Chat (PromptOp.of + LLMOp.of)")
    print("=" * 50)
    result = await Hush(build_basic_chat()).run(
        inputs={"question": "Python là gì? Trả lời trong 1 câu."}
    )
    print(f"  Trả lời: {result['answer']}")

    print()
    print("=" * 50)
    print("2. chain() (all-in-one)")
    print("=" * 50)
    result = await Hush(build_chain_chat()).run(inputs={"query": "Hush workflow engine là gì?"})
    print(f"  Trả lời: {result['response']}")

    print()
    print("=" * 50)
    print("3. Text Summarization Pipeline")
    print("=" * 50)
    result = await Hush(build_summarize()).run(
        inputs={
            "text": """
        Trí tuệ nhân tạo (AI) đang thay đổi cách chúng ta sống và làm việc.
        Từ xe tự lái đến trợ lý ảo, AI đã trở thành một phần không thể thiếu
        trong cuộc sống hàng ngày. Các công ty công nghệ lớn đang đầu tư
        hàng tỷ đô la vào nghiên cứu AI, với hy vọng tạo ra những đột phá
        mới trong lĩnh vực này.
        """
        }
    )
    print(f"  Tóm tắt: {result['summary']}")


if __name__ == "__main__":
    asyncio.run(main())
