"""Shared workflow definitions for 02_data_pipeline.

Defines ops and graphs — imported by demo.py, serve_python.py, serve_rust.py.
"""

from hush.core import END, PARENT, START, GraphOp
from hush.core.ops.transform.func_op import op

# =============================================================================
# Pipeline 1: Data transformation ops
# =============================================================================


@op(rust="./rust_ops::pipeline::fetch_data")
def fetch_data():
    """Bước 1: Lấy data (giả lập)."""
    return {"data": [1, 2, 3, 4, 5]}


@op(rust="./rust_ops::pipeline::transform_double")
def transform(data: list):
    """Bước 2: Nhân đôi mỗi phần tử."""
    return {"transformed": [x * 2 for x in data]}


@op(rust="./rust_ops::analytics::aggregate_data")
def aggregate(data: list):
    """Bước 3: Tính tổng và trung bình."""
    return {
        "total": sum(data),
        "average": sum(data) / len(data),
        "count": len(data),
    }


# =============================================================================
# Pipeline 2: Text processing ops
# =============================================================================


@op(rust="./rust_ops::text::clean_text")
def clean_text(text: str):
    """Tiền xử lý: loại bỏ whitespace thừa, lowercase."""
    cleaned = " ".join(text.split()).strip().lower()
    return {"cleaned_text": cleaned}


@op(rust="./rust_ops::analytics::count_words")
def count_words(text: str):
    """Đếm số từ."""
    words = text.split()
    return {
        "word_count": len(words),
        "unique_words": len(set(words)),
        "words": words,
    }


@op(rust="./rust_ops::analytics::summarize_stats")
def summarize_stats(word_count: int, unique_words: int, cleaned_text: str):
    """Tổng hợp thống kê."""
    return {
        "report": (
            f"Văn bản có {word_count} từ, "
            f"{unique_words} từ unique, "
            f"tỉ lệ unique: {unique_words / word_count:.0%}"
        )
    }


# =============================================================================
# Graph builders
# =============================================================================


def build_data_pipeline():
    """Data transformation: fetch → transform → aggregate."""
    with GraphOp(name="data-pipeline") as graph:
        f = fetch_data()
        t = transform(data=f["data"])
        a = aggregate(data=t["transformed"])
        START >> f >> t >> a >> END
    return graph


def build_text_pipeline():
    """Text processing: clean → count_words → summarize."""
    with GraphOp(name="text-pipeline") as graph:
        c = clean_text(text=PARENT["text"])
        w = count_words(text=c["cleaned_text"])
        s = summarize_stats(
            word_count=w["word_count"],
            unique_words=w["unique_words"],
            cleaned_text=c["cleaned_text"],
        )
        START >> c >> w >> s >> END
    return graph
