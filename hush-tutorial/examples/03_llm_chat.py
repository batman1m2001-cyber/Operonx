"""Tutorial 03: LLM Chat — Gọi LLM qua ResourceHub.

Cần: OPENAI_API_KEY hoặc OPENROUTER_API_KEY trong .env + resources.yaml

Học được:
- load_dotenv() để load API keys
- PromptNode: tạo messages cho LLM
- LLMNode: gọi LLM qua resource_key
- LLMChainNode: kết hợp prompt + LLM trong 1 node
- CodeNode + PromptNode + LLMNode pipeline (tiền xử lý → prompt → LLM)

Chạy: cd hush-tutorial && uv run python examples/03_llm_chat.py
"""

import asyncio
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent.parent / ".env")

from hush.core import END, PARENT, START, CodeNode, GraphNode, Hush
from hush.providers import LLMChainNode, LLMNode, PromptNode


async def example_1_basic_chat():
    """PromptNode + LLMNode — Cách cơ bản nhất."""
    print("=" * 50)
    print("Ví dụ 1: Basic Chat (PromptNode + LLMNode)")
    print("=" * 50)

    with GraphNode(name="basic-chat") as graph:
        prompt = PromptNode(
            name="prompt",
            inputs={
                "template": {
                    "system": "Bạn là trợ lý AI thân thiện. Trả lời ngắn gọn.",
                    "user": "{question}",
                },
                "question": PARENT["question"],
            },
        )
        llm = LLMNode(
            name="llm",
            resource_key="gpt-4o-mini",
            inputs={"messages": prompt["messages"]},
            outputs={"content": PARENT["answer"]},
        )
        START >> prompt >> llm >> END

    engine = Hush(graph)
    result = await engine.run(inputs={"question": "Python là gì? Trả lời trong 1 câu."})
    print(f"Trả lời: {result['answer']}")


async def example_2_chain_node():
    """LLMChainNode — All-in-one, gọn hơn."""
    print()
    print("=" * 50)
    print("Ví dụ 2: LLMChainNode (all-in-one)")
    print("=" * 50)

    with GraphNode(name="chain-chat") as graph:
        chain = LLMChainNode(
            name="chain",
            resource_key="gpt-4o-mini",
            inputs={
                "template": {
                    "system": "Bạn là assistant hữu ích. Trả lời ngắn gọn.",
                    "user": "{query}",
                },
                "query": PARENT["query"],
                "*": PARENT,
            },
            outputs={"content": PARENT["response"]},
        )
        START >> chain >> END

    engine = Hush(graph)
    result = await engine.run(inputs={"query": "Hush workflow engine là gì?"})
    print(f"Trả lời: {result['response']}")


async def example_3_text_summarization():
    """Pipeline: tiền xử lý → prompt → LLM."""
    print()
    print("=" * 50)
    print("Ví dụ 3: Text Summarization Pipeline")
    print("=" * 50)

    def clean_text(text: str) -> dict:
        cleaned = " ".join(text.split()).strip()
        return {"cleaned_text": cleaned}

    with GraphNode(name="summarize-pipeline") as graph:
        preprocess = CodeNode(
            name="preprocess",
            code_fn=clean_text,
            inputs={"text": PARENT["text"]},
            outputs={"cleaned_text": PARENT},
        )
        prompt = PromptNode(
            name="prompt",
            inputs={
                "template": {
                    "system": "Bạn là chuyên gia tóm tắt văn bản. Tóm tắt ngắn gọn trong 1-2 câu.",
                    "user": "Tóm tắt:\n\n{text}",
                },
                "text": PARENT["cleaned_text"],
            },
        )
        summarize = LLMNode(
            name="summarize",
            resource_key="gpt-4o-mini",
            inputs={"messages": prompt["messages"]},
            outputs={"content": PARENT["summary"]},
        )
        START >> preprocess >> prompt >> summarize >> END

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

    print(f"Text gốc (đã clean): {result['cleaned_text'][:80]}...")
    print(f"Tóm tắt: {result['summary']}")


async def main():
    await example_1_basic_chat()
    await example_2_chain_node()
    await example_3_text_summarization()


if __name__ == "__main__":
    asyncio.run(main())
