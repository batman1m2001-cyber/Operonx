"""Tutorial 02: Data Pipeline — Multi-step processing.

Không cần API key. Chỉ dùng hush-core.

Học được:
- Pipeline nhiều bước: fetch → transform → aggregate
- @code_node decorator: định nghĩa node từ function
- node["key"]: truyền data giữa sibling nodes
- PARENT["key"]: đọc external inputs

Chạy: cd hush-tutorial && uv run python examples/02_data_pipeline.py
"""

import asyncio

from hush.core import END, PARENT, START, GraphNode, Hush
from hush.core.nodes.transform.code_node import code_node

# =============================================================================
# Định nghĩa functions cho các nodes
# =============================================================================


@code_node
def fetch_data():
    """Bước 1: Lấy data (giả lập)."""
    return {"data": [1, 2, 3, 4, 5]}


@code_node
def transform(data: list):
    """Bước 2: Nhân đôi mỗi phần tử."""
    return {"transformed": [x * 2 for x in data]}


@code_node
def aggregate(data: list):
    """Bước 3: Tính tổng và trung bình."""
    return {
        "total": sum(data),
        "average": sum(data) / len(data),
        "count": len(data),
    }


# =============================================================================
# Text processing pipeline
# =============================================================================


@code_node
def clean_text(text: str):
    """Tiền xử lý: loại bỏ whitespace thừa, lowercase."""
    cleaned = " ".join(text.split()).strip().lower()
    return {"cleaned_text": cleaned}


@code_node
def count_words(text: str):
    """Đếm số từ."""
    words = text.split()
    return {
        "word_count": len(words),
        "unique_words": len(set(words)),
        "words": words,
    }


@code_node
def summarize_stats(word_count: int, unique_words: int, cleaned_text: str):
    """Tổng hợp thống kê."""
    return {
        "report": (
            f"Văn bản có {word_count} từ, "
            f"{unique_words} từ unique, "
            f"tỉ lệ unique: {unique_words / word_count:.0%}"
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
        f = fetch_data()
        t = transform(data=f["data"])
        a = aggregate(data=t["transformed"])

        START >> f >> t >> a >> END

    engine = Hush(graph)
    result = await engine.run(inputs={})

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
        c = clean_text(text=PARENT["text"])
        w = count_words(text=c["cleaned_text"])
        s = summarize_stats(
            word_count=w["word_count"],
            unique_words=w["unique_words"],
            cleaned_text=c["cleaned_text"],
        )

        START >> c >> w >> s >> END

    engine = Hush(graph)
    result = await engine.run(
        inputs={
            "text": """
        Trí tuệ nhân tạo   đang thay đổi   cách chúng ta sống
        và   làm việc. Trí tuệ nhân tạo   đã trở thành
        một phần không thể thiếu trong cuộc sống hàng ngày.
        """
        }
    )

    print(f"Report:         {result['report']}")


if __name__ == "__main__":
    asyncio.run(main())
