"""Tutorial 13: Advanced Parallel Execution — Fan-out/fan-in và concurrency control.

Không cần API key cho ví dụ 1-3.
Ví dụ 4 cần OPENAI_API_KEY (parallel LLM calls).

Học được:
- Fan-out / fan-in pattern: split → parallel branches → merge
- MapNode với max_concurrency để control rate limiting
- Partial failure handling trong MapNode
- Parallel LLM calls: nhiều prompts cùng lúc
- Batch LLM với MapNode: process list of queries song song

Chạy: cd hush-tutorial && uv run python examples/13_parallel_advanced.py
"""

import asyncio
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent.parent / ".env")

from hush.core import END, PARENT, START, CodeNode, GraphNode, Hush
from hush.core.nodes.iteration.base import Each
from hush.core.nodes.iteration.map_node import MapNode
from hush.core.nodes.transform.code_node import code_node

# =============================================================================
# Ví dụ 1: Fan-out / Fan-in
# =============================================================================


@code_node
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


@code_node
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


@code_node
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

    with GraphNode(name="fan-out-fan-in") as graph:
        # Fan-out: 3 branches chạy song song
        sentiment = analyze_sentiment(
            name="sentiment",
            inputs={"text": PARENT["text"]},
        )
        keywords = extract_keywords(
            name="keywords",
            inputs={"text": PARENT["text"]},
        )
        stats = count_stats(
            name="stats",
            inputs={"text": PARENT["text"]},
        )

        # Fan-in: merge results
        merge = CodeNode(
            name="merge",
            code_fn=lambda s, k, wc, cc, awl: {
                "analysis": {
                    "sentiment": s,
                    "keywords": k,
                    "word_count": wc,
                    "char_count": cc,
                    "avg_word_len": awl,
                }
            },
            inputs={
                "s": sentiment["sentiment"],
                "k": keywords["keywords"],
                "wc": stats["word_count"],
                "cc": stats["char_count"],
                "awl": stats["avg_word_len"],
            },
            outputs={"analysis": PARENT},
        )

        # [sentiment, keywords, stats] chạy song song
        START >> [sentiment, keywords, stats] >> merge >> END

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
# Ví dụ 2: MapNode với concurrency control
# =============================================================================


@code_node
def process_item(item: int):
    """Process 1 item (giả lập I/O với sleep)."""
    import time

    time.sleep(0.05)  # Simulate API call
    return {"result": item * item, "status": "ok"}


async def example_2_concurrency_control():
    """MapNode max_concurrency — giới hạn số tasks song song."""
    print()
    print("=" * 50)
    print("Ví dụ 2: MapNode Concurrency Control")
    print("=" * 50)

    with GraphNode(name="concurrency-demo") as graph:
        with MapNode(
            name="parallel_process",
            inputs={"item": Each(PARENT["items"])},
            max_concurrency=3,  # Max 3 concurrent tasks
        ) as map_node:
            proc = process_item(
                name="process",
                inputs={"item": PARENT["item"]},
                outputs={"*": PARENT},
            )
            START >> proc >> END

        map_node["result"] >> PARENT["results"]
        START >> map_node >> END

    engine = Hush(graph)

    import time

    items = list(range(1, 10))
    start = time.time()
    result = await engine.run(inputs={"items": items})
    elapsed = time.time() - start

    print(f"  Items: {items}")
    print(f"  Results: {result['results']}")
    print(f"  Time: {elapsed:.2f}s (max_concurrency=3, ~{len(items) // 3} batches)")


# =============================================================================
# Ví dụ 3: Partial failure handling
# =============================================================================


@code_node
def risky_process(item: int):
    """Process that fails for even numbers."""
    if item % 2 == 0:
        raise ValueError(f"Cannot process even number: {item}")
    return {"result": item * 10, "error": None}


async def example_3_partial_failure():
    """MapNode xử lý partial failures — items lỗi không ảnh hưởng items khác."""
    print()
    print("=" * 50)
    print("Ví dụ 3: Partial Failure Handling")
    print("=" * 50)

    with GraphNode(name="partial-failure") as graph:
        # Wrap risky logic trong try/catch
        with MapNode(
            name="safe_process",
            inputs={"item": Each(PARENT["items"])},
        ) as map_node:

            @code_node
            def safe_op(item: int):
                if item % 2 != 0:
                    return {"result": item * 10, "error": None}
                return {"result": None, "error": f"Even number: {item}"}

            proc = safe_op(
                name="process",
                inputs={"item": PARENT["item"]},
                outputs={"result": PARENT, "error": PARENT},
            )
            START >> proc >> END

        # Filter results
        filter_node = CodeNode(
            name="filter",
            code_fn=lambda results, errors: {
                "successful": [r for r, e in zip(results, errors) if e is None],
                "failed": [e for e in errors if e is not None],
            },
            inputs={
                "results": map_node["result"],
                "errors": map_node["error"],
            },
            outputs={"*": PARENT},
        )

        START >> map_node >> filter_node >> END

    engine = Hush(graph)
    result = await engine.run(inputs={"items": [1, 2, 3, 4, 5, 6, 7]})

    print("  Input:      [1, 2, 3, 4, 5, 6, 7]")
    print(f"  Successful: {result['successful']}")
    print(f"  Failed:     {result['failed']}")


# =============================================================================
# Ví dụ 4: Parallel LLM calls + Batch LLM
# =============================================================================


async def example_4_parallel_llm():
    """Nhiều LLM prompts song song + batch queries qua MapNode."""
    print()
    print("=" * 50)
    print("Ví dụ 4: Parallel & Batch LLM Calls")
    print("=" * 50)

    import os

    if not os.environ.get("OPENAI_API_KEY"):
        print("  Skipped — OPENAI_API_KEY chưa set")
        return

    from hush.providers import LLMNode, PromptNode

    # --- Part A: Parallel prompts (different tasks, same input) ---
    print("\n  A) Parallel prompts (summary + keywords from same text):")

    with GraphNode(name="parallel-llm") as graph:
        prompt_summary = PromptNode(
            name="prompt_summary",
            inputs={
                "template": {"system": "Summarize in one sentence.", "user": "{text}"},
                "text": PARENT["text"],
            },
        )
        prompt_keywords = PromptNode(
            name="prompt_keywords",
            inputs={
                "template": {
                    "system": "List 3 keywords, comma-separated.",
                    "user": "{text}",
                },
                "text": PARENT["text"],
            },
        )

        llm_summary = LLMNode(
            name="llm_summary",
            resource_key="gpt-4o-mini",
            inputs={"messages": prompt_summary["messages"]},
        )
        llm_keywords = LLMNode(
            name="llm_keywords",
            resource_key="gpt-4o-mini",
            inputs={"messages": prompt_keywords["messages"]},
        )

        merge = CodeNode(
            name="merge",
            code_fn=lambda s, k: {"summary": s, "keywords": k},
            inputs={"s": llm_summary["content"], "k": llm_keywords["content"]},
            outputs={"*": PARENT},
        )

        START >> [prompt_summary, prompt_keywords]
        prompt_summary >> llm_summary
        prompt_keywords >> llm_keywords
        [llm_summary, llm_keywords] >> merge >> END

    engine = Hush(graph)
    result = await engine.run(
        inputs={
            "text": "Python is a versatile programming language used in web development, data science, and AI."
        }
    )
    print(f"    Summary:  {result['summary']}")
    print(f"    Keywords: {result['keywords']}")

    # --- Part B: Batch LLM via MapNode ---
    print("\n  B) Batch LLM (multiple queries via MapNode):")

    with GraphNode(name="batch-llm") as graph:
        with MapNode(
            name="llm_batch",
            inputs={"query": Each(PARENT["queries"])},
            max_concurrency=3,
        ) as map_node:
            prompt = PromptNode(
                name="prompt",
                inputs={
                    "template": {
                        "system": "Answer in one sentence.",
                        "user": "{query}",
                    },
                    "query": PARENT["query"],
                },
            )
            llm = LLMNode(
                name="llm",
                resource_key="gpt-4o-mini",
                inputs={"messages": prompt["messages"]},
                outputs={"content": PARENT["answer"]},
            )
            START >> prompt >> llm >> END

        map_node["answer"] >> PARENT["answers"]
        START >> map_node >> END

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
