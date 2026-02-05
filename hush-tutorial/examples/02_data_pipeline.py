"""Tutorial 02: Data Pipeline — Multi-step processing.

Không cần API key. Chỉ dùng hush-core.

Học được:
- Pipeline nhiều bước: fetch → transform → aggregate
- inputs/outputs mapping chi tiết
- Truyền data giữa các nodes qua PARENT state
- Dùng named function thay vì lambda

Chạy: cd hush-tutorial && uv run python examples/02_data_pipeline.py
"""

import asyncio
from hush.core import Hush, GraphNode, CodeNode, START, END, PARENT


# =============================================================================
# Định nghĩa functions cho các nodes
# =============================================================================

def fetch_data():
    """Bước 1: Lấy data (giả lập)."""
    return {"data": [1, 2, 3, 4, 5]}


def transform(data: list) -> dict:
    """Bước 2: Nhân đôi mỗi phần tử."""
    return {"transformed": [x * 2 for x in data]}


def aggregate(data: list) -> dict:
    """Bước 3: Tính tổng và trung bình."""
    return {
        "total": sum(data),
        "average": sum(data) / len(data),
        "count": len(data),
    }


# =============================================================================
# Text processing pipeline
# =============================================================================

def clean_text(text: str) -> dict:
    """Tiền xử lý: loại bỏ whitespace thừa, lowercase."""
    cleaned = " ".join(text.split()).strip().lower()
    return {"cleaned_text": cleaned}


def count_words(text: str) -> dict:
    """Đếm số từ."""
    words = text.split()
    return {
        "word_count": len(words),
        "unique_words": len(set(words)),
        "words": words,
    }


def summarize_stats(word_count: int, unique_words: int, cleaned_text: str) -> dict:
    """Tổng hợp thống kê."""
    return {
        "report": (
            f"Văn bản có {word_count} từ, "
            f"{unique_words} từ unique, "
            f"tỉ lệ unique: {unique_words/word_count:.0%}"
        )
    }


async def main():
    # =========================================================================
    # Pipeline 1: Data transformation
    # =========================================================================
    print("=" * 50)
    print("Pipeline 1: Data Transformation")
    print("=" * 50)

    with GraphNode(name="data-pipeline") as graph:
        step_fetch = CodeNode(
            name="fetch",
            code_fn=fetch_data,
            outputs={"data": PARENT},
        )
        step_transform = CodeNode(
            name="transform",
            code_fn=transform,
            inputs={"data": PARENT["data"]},
            outputs={"transformed": PARENT},
        )
        step_aggregate = CodeNode(
            name="aggregate",
            code_fn=aggregate,
            inputs={"data": PARENT["transformed"]},
            outputs={"total": PARENT, "average": PARENT, "count": PARENT},
        )

        START >> step_fetch >> step_transform >> step_aggregate >> END

    engine = Hush(graph)
    result = await engine.run(inputs={})

    print(f"Data gốc:       {result['data']}")
    print(f"Sau transform:  {result['transformed']}")
    print(f"Tổng:           {result['total']}")
    print(f"Trung bình:     {result['average']}")
    print(f"Số phần tử:     {result['count']}")

    # =========================================================================
    # Pipeline 2: Text processing
    # =========================================================================
    print()
    print("=" * 50)
    print("Pipeline 2: Text Processing")
    print("=" * 50)

    with GraphNode(name="text-pipeline") as graph:
        step_clean = CodeNode(
            name="clean",
            code_fn=clean_text,
            inputs={"text": PARENT["text"]},
            outputs={"cleaned_text": PARENT},
        )
        step_count = CodeNode(
            name="count",
            code_fn=count_words,
            inputs={"text": PARENT["cleaned_text"]},
            outputs={"word_count": PARENT, "unique_words": PARENT, "words": PARENT},
        )
        step_summary = CodeNode(
            name="summary",
            code_fn=summarize_stats,
            inputs={
                "word_count": PARENT["word_count"],
                "unique_words": PARENT["unique_words"],
                "cleaned_text": PARENT["cleaned_text"],
            },
            outputs={"report": PARENT},
        )

        START >> step_clean >> step_count >> step_summary >> END

    engine = Hush(graph)
    result = await engine.run(inputs={
        "text": """
        Trí tuệ nhân tạo   đang thay đổi   cách chúng ta sống
        và   làm việc. Trí tuệ nhân tạo   đã trở thành
        một phần không thể thiếu trong cuộc sống hàng ngày.
        """
    })

    print(f"Text đã clean:  {result['cleaned_text']}")
    print(f"Số từ:          {result['word_count']}")
    print(f"Từ unique:      {result['unique_words']}")
    print(f"Report:         {result['report']}")


if __name__ == "__main__":
    asyncio.run(main())
