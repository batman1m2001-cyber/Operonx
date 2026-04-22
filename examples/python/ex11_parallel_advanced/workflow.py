"""Shared workflow definitions for ex11_parallel_advanced.

Fan-out/fan-in, generator iteration, and partial-failure handling.
All scenarios are pure compute — no API keys required.
"""

from operon.core import END, PARENT, START, GraphOp, op

# =============================================================================
# Ops — Fan-out / Fan-in
# =============================================================================


@op
def analyze_sentiment(text: str):
    """Phân tích sentiment (giả lập)."""
    positive = sum(
        1 for w in text.lower().split() if w in {"good", "great", "excellent", "love", "happy"}
    )
    negative = sum(
        1 for w in text.lower().split() if w in {"bad", "terrible", "hate", "awful", "sad"}
    )
    return {
        "sentiment": "positive"
        if positive > negative
        else ("negative" if negative > positive else "neutral")
    }


@op
def extract_keywords(text: str):
    """Trích keywords (giả lập)."""
    stop_words = {
        "the",
        "is",
        "a",
        "an",
        "and",
        "or",
        "but",
        "in",
        "on",
        "at",
        "to",
        "for",
        "of",
        "with",
    }
    words = [w.lower().strip(".,!?") for w in text.split()]
    keywords = [w for w in words if w not in stop_words and len(w) > 2]
    return {"keywords": keywords[:5]}


@op
def count_stats(text: str):
    """Đếm thống kê text."""
    words = text.split()
    return {
        "word_count": len(words),
        "char_count": len(text),
        "avg_word_len": round(len(text) / max(len(words), 1), 1),
    }


@op
def merge_analysis(s, k, wc, cc, awl):
    """Merge fan-out results."""
    return {
        "analysis": {
            "sentiment": s,
            "keywords": k,
            "word_count": wc,
            "char_count": cc,
            "avg_word_len": awl,
        }
    }


# =============================================================================
# Ops — Generator iteration
# =============================================================================


@op
def each_item(items: list):
    """Yield từng item — thay thế MapOp + Each."""
    for item in items:
        yield {"item": item}


@op
def process_item(item: int):
    """Process 1 item."""
    return {"result": item * item, "status": "ok"}


# =============================================================================
# Ops — Partial failure
# =============================================================================


@op
def safe_process(item: int):
    """Process that returns error for even numbers instead of raising."""
    if item % 2 != 0:
        return {"result": item * 10, "error": None}
    return {"result": None, "error": f"Even number: {item}"}


# =============================================================================
# Graph builders
# =============================================================================


def build_fan_out() -> GraphOp:
    """Graph: text → [sentiment, keywords, stats] parallel → merge."""
    with GraphOp(name="fan-out-fan-in") as g:
        sent = analyze_sentiment(text=PARENT["text"])
        kw = extract_keywords(text=PARENT["text"])
        st = count_stats(text=PARENT["text"])
        m = merge_analysis(
            s=sent["sentiment"],
            k=kw["keywords"],
            wc=st["word_count"],
            cc=st["char_count"],
            awl=st["avg_word_len"],
            outputs={"analysis": PARENT},
        )
        START >> [sent, kw, st] >> m >> END
    return g


def build_iteration() -> GraphOp:
    """Graph: items → generator → process each → collect results."""
    with GraphOp(name="iteration-demo") as g:
        src = each_item(items=PARENT["items"])
        proc = process_item(item=src["item"])
        proc["result"] >> PARENT["results"]
        START >> src >> proc >> END
    return g


def build_partial_failure() -> GraphOp:
    """Graph: items → generator → safe process → collect results/errors."""
    with GraphOp(name="partial-failure") as g:
        src = each_item(items=PARENT["items"])
        proc = safe_process(item=src["item"])
        proc["result"] >> PARENT["results"]
        proc["error"] >> PARENT["errors"]
        START >> src >> proc >> END
    return g
