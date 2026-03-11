"""Tutorial 13: Advanced Parallel Execution — Fan-out/fan-in và concurrency control.

Không cần API key cho ví dụ 1-3.
Ví dụ 4 cần OPENAI_API_KEY (parallel LLM calls).

Học được:
- Fan-out / fan-in pattern: split → parallel branches → merge
- Generator ops cho iteration (thay thế MapOp + Each)
- Partial failure handling với generator ops
- Parallel LLM calls: nhiều prompts cùng lúc
- Batch LLM với generator ops: process list of queries

Chạy: cd tutorial && uv run python examples/13_parallel_advanced.py
"""

import asyncio
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent.parent / ".env")

from hush.core import END, PARENT, START, GraphOp, Hush
from hush.core.ops import op

# =============================================================================
# Ví dụ 1: Fan-out / Fan-in
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


async def example_1_fan_out_fan_in():
    """3 nhánh phân tích song song → merge kết quả."""
    print("=" * 50)
    print("Ví dụ 1: Fan-out / Fan-in")
    print("=" * 50)

    @op
    def merge(s, k, wc, cc, awl):
        return {
            "analysis": {
                "sentiment": s,
                "keywords": k,
                "word_count": wc,
                "char_count": cc,
                "avg_word_len": awl,
            }
        }

    with GraphOp(name="fan-out-fan-in") as graph:
        # Fan-out: 3 branches chạy song song
        sent = analyze_sentiment(text=PARENT["text"])
        kw = extract_keywords(text=PARENT["text"])
        st = count_stats(text=PARENT["text"])

        # Fan-in: merge results
        m = merge(
            s=sent["sentiment"],
            k=kw["keywords"],
            wc=st["word_count"],
            cc=st["char_count"],
            awl=st["avg_word_len"],
            outputs={"analysis": PARENT},
        )

        # [sent, kw, st] chạy song song
        START >> [sent, kw, st] >> m >> END

    engine = Hush(graph)
    result = await engine.run(
        inputs={"text": "This is a great excellent product with good quality and love it"}
    )
    analysis = result["analysis"]
    print(f"  Sentiment:    {analysis['sentiment']}")
    print(f"  Keywords:     {analysis['keywords']}")
    print(f"  Word count:   {analysis['word_count']}")
    print(f"  Avg word len: {analysis['avg_word_len']}")


# =============================================================================
# Ví dụ 2: Iteration với generator op (thay thế MapOp + concurrency)
# =============================================================================


@op
def each_item(items: list):
    """Yield từng item — thay thế MapOp + Each."""
    for item in items:
        yield {"item": item}


@op
def process_item(item: int):
    """Process 1 item (giả lập I/O với sleep)."""
    import time

    time.sleep(0.05)  # Simulate API call
    return {"result": item * item, "status": "ok"}


async def example_2_concurrency_control():
    """Generator op iteration — downstream ops auto-called per yield."""
    print()
    print("=" * 50)
    print("Ví dụ 2: Generator Iteration (replaces MapOp)")
    print("=" * 50)

    with GraphOp(name="iteration-demo") as graph:
        src = each_item(items=PARENT["items"])
        proc = process_item(item=src["item"])
        proc["result"] >> PARENT["results"]
        START >> src >> proc >> END

    engine = Hush(graph)

    import time

    items = list(range(1, 10))
    start = time.time()
    result = await engine.run(inputs={"items": items})
    elapsed = time.time() - start

    print(f"  Items: {items}")
    print(f"  Results: {result['results']}")
    print(f"  Time: {elapsed:.2f}s")


# =============================================================================
# Ví dụ 3: Partial failure handling
# =============================================================================


async def example_3_partial_failure():
    """Generator op với safe processing — items lỗi trả error thay vì crash."""
    print()
    print("=" * 50)
    print("Ví dụ 3: Partial Failure Handling")
    print("=" * 50)

    @op
    def safe_process(item: int):
        """Process that returns error for even numbers instead of raising."""
        if item % 2 != 0:
            return {"result": item * 10, "error": None}
        return {"result": None, "error": f"Even number: {item}"}

    @op
    def filter_results(results, errors):
        return {
            "successful": [r for r, e in zip(results, errors) if e is None],
            "failed": [e for e in errors if e is not None],
        }

    with GraphOp(name="partial-failure") as graph:
        src = each_item(items=PARENT["items"])
        proc = safe_process(item=src["item"])

        flt = filter_results(
            results=proc["result"],
            errors=proc["error"],
            outputs={"*": PARENT},
        )

        START >> src >> proc >> flt >> END

    engine = Hush(graph)
    result = await engine.run(inputs={"items": [1, 2, 3, 4, 5, 6, 7]})

    print("  Input:      [1, 2, 3, 4, 5, 6, 7]")
    print(f"  Successful: {result['successful']}")
    print(f"  Failed:     {result['failed']}")


# =============================================================================
# Ví dụ 4: Parallel LLM calls + Batch LLM
# =============================================================================


async def example_4_parallel_llm():
    """Nhiều LLM prompts song song + batch queries qua generator op."""
    print()
    print("=" * 50)
    print("Ví dụ 4: Parallel & Batch LLM Calls")
    print("=" * 50)

    import os

    if not os.environ.get("OPENAI_API_KEY"):
        print("  Skipped — OPENAI_API_KEY chưa set")
        return

    from hush.providers import LLMOp, PromptOp

    # --- Part A: Parallel prompts (different tasks, same input) ---
    print("\n  A) Parallel prompts (summary + keywords from same text):")

    @op
    def merge_results(s, k):
        return {"summary": s, "keywords": k}

    with GraphOp(name="parallel-llm") as graph:
        p_summary = PromptOp.of(
            template={"system": "Summarize in one sentence.", "user": "{text}"},
            text=PARENT["text"],
        )
        p_keywords = PromptOp.of(
            template={"system": "List 3 keywords, comma-separated.", "user": "{text}"},
            text=PARENT["text"],
        )

        llm_summary = LLMOp.of(resource="gpt-4o-mini", messages=p_summary["messages"])
        llm_keywords = LLMOp.of(resource="gpt-4o-mini", messages=p_keywords["messages"])

        m = merge_results(
            s=llm_summary["content"],
            k=llm_keywords["content"],
            outputs={"*": PARENT},
        )

        START >> [p_summary, p_keywords]
        p_summary >> llm_summary
        p_keywords >> llm_keywords
        [llm_summary, llm_keywords] >> m >> END

    engine = Hush(graph)
    result = await engine.run(
        inputs={
            "text": "Python is a versatile programming language used in web development, data science, and AI."
        }
    )
    print(f"    Summary:  {result['summary']}")
    print(f"    Keywords: {result['keywords']}")

    # --- Part B: Batch LLM via generator op ---
    print("\n  B) Batch LLM (multiple queries via generator op):")

    @op
    def each_query(queries: list):
        """Yield từng query — thay thế MapOp + Each."""
        for query in queries:
            yield {"query": query}

    with GraphOp(name="batch-llm") as graph:
        src = each_query(queries=PARENT["queries"])
        p = PromptOp.of(
            template={"system": "Answer in one sentence.", "user": "{query}"},
            query=src["query"],
        )
        llm = LLMOp.of(
            resource="gpt-4o-mini",
            messages=p["messages"],
            outputs={"content": PARENT["answer"]},
        )
        llm["answer"] >> PARENT["answers"]
        START >> src >> p >> llm >> END

    engine = Hush(graph)
    result = await engine.run(
        inputs={"queries": ["What is Python?", "What is JavaScript?", "What is Rust?"]}
    )
    for q, a in zip(["Python", "JavaScript", "Rust"], result["answers"]):
        print(f"    {q}: {a[:60]}...")


# =============================================================================
# Main
# =============================================================================


async def main():
    await example_1_fan_out_fan_in()
    await example_2_concurrency_control()
    await example_3_partial_failure()
    await example_4_parallel_llm()


if __name__ == "__main__":
    asyncio.run(main())
