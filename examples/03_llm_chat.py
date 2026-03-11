"""Tutorial 03: LLM Chat — Gọi LLM qua ResourceHub.

Cần: OPENAI_API_KEY hoặc OPENROUTER_API_KEY trong .env + resources.yaml

Học được:
- load_dotenv() để load API keys
- PromptOp.of(): tạo messages cho LLM
- LLMOp.of(): gọi LLM qua resource
- chain(): kết hợp prompt + LLM trong 1 graph
- @op + PromptOp.of() + LLMOp.of() pipeline (tiền xử lý → prompt → LLM)

Chạy: uv run python examples/03_llm_chat.py
"""

import asyncio
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent.parent / ".env")

from hush.core import END, PARENT, START, GraphOp, Hush
from hush.core.ops.transform.func_op import op
from hush.providers import LLMOp, PromptOp, chain


async def example_1_basic_chat():
    """PromptOp.of() + LLMOp.of() — Cách cơ bản nhất."""
    print("=" * 50)
    print("Ví dụ 1: Basic Chat (PromptOp.of + LLMOp.of)")
    print("=" * 50)

    with GraphOp(name="basic-chat") as graph:
        p = PromptOp.of(
            template={
                "system": "Bạn là trợ lý AI thân thiện. Trả lời ngắn gọn.",
                "user": "{question}",
            },
            question=PARENT["question"],
        )
        llm = LLMOp.of(
            resource="gpt-4o-mini",
            messages=p["messages"],
            outputs={"content": PARENT["answer"]},
        )
        START >> p >> llm >> END

    engine = Hush(graph)
    result = await engine.run(inputs={"question": "Python là gì? Trả lời trong 1 câu."})
    print(f"Trả lời: {result['answer']}")


async def example_2_chain_node():
    """chain() — All-in-one, gọn hơn."""
    print()
    print("=" * 50)
    print("Ví dụ 2: chain() (all-in-one)")
    print("=" * 50)

    with GraphOp(name="chain-chat") as graph:
        chat = chain(
            resource="gpt-4o-mini",
            template={
                "system": "Bạn là assistant hữu ích. Trả lời ngắn gọn.",
                "user": "{query}",
            },
            query=PARENT["query"],
            outputs={"content": PARENT["response"]},
        )
        START >> chat >> END

    engine = Hush(graph)
    result = await engine.run(inputs={"query": "Hush workflow engine là gì?"})
    print(f"Trả lời: {result['response']}")


async def example_3_text_summarization():
    """Pipeline: tiền xử lý → prompt → LLM."""
    print()
    print("=" * 50)
    print("Ví dụ 3: Text Summarization Pipeline")
    print("=" * 50)

    @op(rust="./rust_ops::text::clean_text")
    def clean_text(text: str):
        cleaned = " ".join(text.split()).strip()
        return {"cleaned_text": cleaned}

    with GraphOp(name="summarize-pipeline") as graph:
        preprocess = clean_text(text=PARENT["text"])
        p = PromptOp.of(
            template={
                "system": "Bạn là chuyên gia tóm tắt văn bản. Tóm tắt ngắn gọn trong 1-2 câu.",
                "user": "Tóm tắt:\n\n{text}",
            },
            text=preprocess["cleaned_text"],
        )
        summarize = LLMOp.of(
            resource="gpt-4o-mini",
            messages=p["messages"],
            outputs={"content": PARENT["summary"]},
        )
        START >> preprocess >> p >> summarize >> END

    engine = Hush(graph)
    result = await engine.run(
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

    print(f"Tóm tắt: {result['summary']}")


async def main():
    await example_1_basic_chat()
    await example_2_chain_node()
    await example_3_text_summarization()


if __name__ == "__main__":
    asyncio.run(main())
