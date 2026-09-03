"""03 LLM Chat — two LLM chat graphs.

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
from operonx.providers import LLMOp


@op
def clean_text(text: str):
    return {"cleaned_text": " ".join(text.split()).strip()}


@graph
def basic_chat(question):
    """Single-op form: LLMOp formats the prompt and calls the model."""
    llm = LLMOp.of(
        resource="gpt-4o-mini",
        prompt={
            "system": "Bạn là trợ lý AI thân thiện. Trả lời ngắn gọn.",
            "user": "{question}",
        },
        question=question,
    )
    START >> llm >> END


@graph
def summarize_pipeline(text):
    """Pre-process → LLMOp with template."""
    pre = clean_text(text=text)
    summarize = LLMOp.of(
        resource="gpt-4o-mini",
        prompt={
            "system": "Bạn là chuyên gia tóm tắt văn bản. Tóm tắt ngắn gọn trong 1-2 câu.",
            "user": "Tóm tắt:\n\n{text}",
        },
        text=pre["cleaned_text"],
    )
    START >> pre >> summarize >> END


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
        ("summarize", summarize_pipeline(text=SAMPLE_TEXT)),
    ]
    for label, g in runs:
        result = await Operon(g).run(inputs={})
        # Strip $state for tidier output
        content = {k: v for k, v in result.items() if k != "$state"}
        print(f"[{label}] {content}")


if __name__ == "__main__":
    asyncio.run(main())

# ── the served front door ───────────────────────────────────────────────
# Every operonx project serves. The [[serve]] block in operonx.toml names
# this graph, `operonx-serve` boots it, and the studio draws it as the
# entry node feeding the flow — no pipeline begins from nowhere.
#
# `ingress` yields one item per request payload and `egress` writes the
# reply back to the caller. Neither names a resource: the run was minted
# by a transport and already carries its session — and with no session the
# same graph still runs under a plain `engine.start()`, so serving costs
# the example nothing.
from operonx.core.serve import egress, ingress


@op
def answer(item=None) -> dict:
    """One request in, this example's reply out."""
    return {"reply": f"ex03 saw: {item!r}"}


@graph
def served():
    request = ingress()
    a = answer(item=request["item"])
    out = egress(item=a["reply"])
    START >> request >> a >> out >> END

