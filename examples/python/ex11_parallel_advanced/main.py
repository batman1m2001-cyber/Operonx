"""11 Parallel Advanced — fan-out/fan-in, generator iteration, partial failure.

All scenarios are pure compute (tier 1). Run from this directory:

    uv sync
    uv run python main.py
"""

from __future__ import annotations

import asyncio

from operonx.core import END, PARENT, START, Operon, graph, op


@op
def analyze_sentiment(text: str):
    pos = sum(1 for w in text.lower().split() if w in {"good", "great", "excellent", "love", "happy"})
    neg = sum(1 for w in text.lower().split() if w in {"bad", "terrible", "hate", "awful", "sad"})
    if pos > neg:
        s = "positive"
    elif neg > pos:
        s = "negative"
    else:
        s = "neutral"
    return {"sentiment": s}


@op
def extract_keywords(text: str):
    stop = {"the", "is", "a", "an", "and", "or", "but", "in", "on", "at", "to", "for", "of", "with"}
    words = [w.lower().strip(".,!?") for w in text.split()]
    kw = [w for w in words if w not in stop and len(w) > 2]
    return {"keywords": kw[:5]}


@op
def count_stats(text: str):
    words = text.split()
    return {
        "word_count": len(words),
        "char_count": len(text),
        "avg_word_len": round(len(text) / max(len(words), 1), 1),
    }


@op
def merge_analysis(s, k, wc, cc, awl):
    return {
        "analysis": {
            "sentiment": s,
            "keywords": k,
            "word_count": wc,
            "char_count": cc,
            "avg_word_len": awl,
        }
    }


@op
def each_item(items: list):
    for item in items:
        yield {"item": item}


@op
def process_item(item: int):
    return {"result": item * item, "status": "ok"}


@op
def safe_process(item: int):
    if item % 2 != 0:
        return {"result": item * 10, "error": None}
    return {"result": None, "error": f"Even number: {item}"}


@graph
def fan_out(text):
    """text → [sentiment, keywords, stats] parallel → merge."""
    sent = analyze_sentiment(text=text)
    kw = extract_keywords(text=text)
    st = count_stats(text=text)
    m = merge_analysis(
        s=sent["sentiment"],
        k=kw["keywords"],
        wc=st["word_count"],
        cc=st["char_count"],
        awl=st["avg_word_len"],
    )
    START >> [sent, kw, st] >> m >> END


@graph
def iteration(items):
    """Generator yield → process each → collect results."""
    src = each_item(items=items)
    proc = process_item(item=src["item"])
    proc["result"] >> PARENT["results"]
    START >> src >> proc >> END


@graph
def partial_failure(items):
    """Each item produces either a result or an error — both collected."""
    src = each_item(items=items)
    proc = safe_process(item=src["item"])
    proc["result"] >> PARENT["results"]
    proc["error"] >> PARENT["errors"]
    START >> src >> proc >> END


async def main() -> None:
    runs = [
        ("fan_out", fan_out(text=PARENT["text"]),
            {"text": "This is a great excellent product with good quality and love it"}),
        ("iteration", iteration(items=PARENT["items"]),
            {"items": [1, 2, 3, 4, 5, 6, 7, 8, 9]}),
        ("partial_failure", partial_failure(items=PARENT["items"]),
            {"items": [1, 2, 3, 4, 5, 6, 7]}),
    ]
    for label, g, inputs in runs:
        result = await Operon(g).run(inputs=inputs)
        content = {k: v for k, v in result.items() if k != "$state"}
        print(f"[{label}] {content}")


if __name__ == "__main__":
    asyncio.run(main())
