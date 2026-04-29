"""02 Data Pipeline — two pure-compute pipelines, no API keys.

Run from this directory:

    uv sync
    uv run python main.py
"""

from __future__ import annotations

import asyncio

from operonx.core import END, START, Operon, graph, op

SAMPLE_TEXT = (
    "        Trí tuệ nhân tạo   đang thay đổi   cách chúng ta sống\n"
    "        và   làm việc. Trí tuệ nhân tạo   đã trở thành\n"
    "        một phần không thể thiếu trong cuộc sống hàng ngày.\n"
    "        "
)

# ── Pipeline 1: data transformation ─────────────────────────────────────


@op
def fetch_data():
    return {"data": [1, 2, 3, 4, 5]}


@op
def transform(data: list):
    return {"transformed": [x * 2 for x in data]}


@op
def aggregate(data: list):
    return {
        "total": sum(data),
        "average": sum(data) / len(data),
        "count": len(data),
    }


@graph
def data_pipeline():
    """fetch → transform → aggregate."""
    f = fetch_data()
    t = transform(data=f["data"])
    a = aggregate(data=t["transformed"])
    START >> f >> t >> a >> END


# ── Pipeline 2: text processing ─────────────────────────────────────────


@op
def clean_text(text: str):
    cleaned = " ".join(text.split()).strip().lower()
    return {"cleaned_text": cleaned}


@op
def count_words(text: str):
    words = text.split()
    return {
        "word_count": len(words),
        "unique_words": len(set(words)),
        "words": words,
    }


@op
def summarize_stats(word_count: int, unique_words: int, cleaned_text: str):
    return {
        "report": (
            f"Văn bản có {word_count} từ, "
            f"{unique_words} từ unique, "
            f"tỉ lệ unique: {unique_words / word_count:.0%}"
        )
    }


@graph
def text_pipeline(text):
    """clean → count_words → summarize_stats."""
    c = clean_text(text=text)
    w = count_words(text=c["cleaned_text"])
    s = summarize_stats(
        word_count=w["word_count"],
        unique_words=w["unique_words"],
        cleaned_text=c["cleaned_text"],
    )
    START >> c >> w >> s >> END


# ── Entry point ─────────────────────────────────────────────────────────


async def main() -> None:
    runs = [
        ("data", data_pipeline()),
        ("text", text_pipeline(text=SAMPLE_TEXT)),
    ]
    for label, g in runs:
        result = await Operon(g).run(inputs={})
        print(f"[{label}] {result}")


if __name__ == "__main__":
    asyncio.run(main())
