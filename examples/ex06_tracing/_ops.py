"""Shared ops for ex06_tracing examples."""

from hush.core import END, PARENT, START, GraphOp
from hush.core.ops.transform.func_op import op


@op
def analyze_text(text: str):
    """Phân tích text và thêm dynamic tags."""
    words = text.split()
    word_count = len(words)

    tags = ["analyzed"]
    if word_count > 10:
        tags.append("long-text")
    else:
        tags.append("short-text")

    return {
        "word_count": word_count,
        "preview": text[:50],
        "$tags": tags,
    }


@op
def classify(word_count: int):
    """Phân loại dựa trên word count."""
    if word_count > 20:
        category = "article"
    elif word_count > 5:
        category = "sentence"
    else:
        category = "phrase"

    return {
        "category": category,
        "$tags": [f"category:{category}"],
    }


def build_text_analyzer():
    """Text analyzer with classification."""
    with GraphOp(name="text-analyzer") as graph:
        analyze = analyze_text(text=PARENT["text"])
        categorize = classify(word_count=analyze["word_count"])
        analyze["word_count"] >> PARENT["word_count"]
        analyze["preview"] >> PARENT["preview"]
        categorize["category"] >> PARENT["category"]
        START >> analyze >> categorize >> END
    return graph
