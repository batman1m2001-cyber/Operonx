"""03 LLM Chat — three LLM chat graphs.

Requires ``OPENAI_API_KEY`` in ``.env`` and ``llm:gpt-4o-mini`` in
``resources.yaml``. Run from this directory:

    uv sync
    cp .env.example .env  # fill in OPENAI_API_KEY
    uv run python main.py
"""

from __future__ import annotations

import asyncio

import operonx
from operonx.core import END, START, Operon, graph, op
from operonx.providers import LLMOp, PromptOp, chat


@op
def clean_text(text: str):
    return {"cleaned_text": " ".join(text.split()).strip()}


@graph
def basic_chat(question):
    """Two-op explicit form: PromptOp → LLMOp."""
    p = PromptOp.of(
        template={
            "system": "Bạn là trợ lý AI thân thiện. Trả lời ngắn gọn.",
            "user": "{question}",
        },
        question=question,
    )
    llm = LLMOp.of(resource="gpt-4o-mini", messages=p["messages"])
    START >> p >> llm >> END


@graph
def chain_chat(query):
    """Single-op all-in-one: chat() bundles the prompt + LLM."""
    c = chat(
        resource="gpt-4o-mini",
        template={
            "system": "Bạn là assistant hữu ích. Trả lời ngắn gọn.",
            "user": "{query}",
        },
        query=query,
    )
    START >> c >> END


@graph
def summarize_pipeline(text):
    """Pre-process → prompt → LLM."""
    pre = clean_text(text=text)
    p = PromptOp.of(
        template={
            "system": "Bạn là chuyên gia tóm tắt văn bản. Tóm tắt ngắn gọn trong 1-2 câu.",
            "user": "Tóm tắt:\n\n{text}",
        },
        text=pre["cleaned_text"],
    )
    summarize = LLMOp.of(resource="gpt-4o-mini", messages=p["messages"])
    START >> pre >> p >> summarize >> END


SAMPLE_TEXT = (
    "        Trí tuệ nhân tạo (AI) đang thay đổi cách chúng ta sống và làm việc.\n"
    "        Từ xe tự lái đến trợ lý ảo, AI đã trở thành một phần không thể thiếu\n"
    "        trong cuộc sống hàng ngày. Các công ty công nghệ lớn đang đầu tư\n"
    "        hàng tỷ đô la vào nghiên cứu AI, với hy vọng tạo ra những đột phá\n"
    "        mới trong lĩnh vực này.\n"
    "        "
)


async def main() -> None:
    operonx.bootstrap()  # loads ./.env + ./resources.yaml from CWD

    runs = [
        ("basic", basic_chat(question="Python là gì? Trả lời trong 1 câu.")),
        ("chain", chain_chat(query="Operon workflow engine là gì?")),
        ("summarize", summarize_pipeline(text=SAMPLE_TEXT)),
    ]
    for label, g in runs:
        result = await Operon(g).run(inputs={})
        # Strip $state for tidier output
        content = {k: v for k, v in result.items() if k != "$state"}
        print(f"[{label}] {content}")


if __name__ == "__main__":
    asyncio.run(main())
