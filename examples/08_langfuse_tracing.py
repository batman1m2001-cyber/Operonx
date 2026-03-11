"""Tutorial 08: Langfuse Tracing — Gửi traces lên Langfuse cloud.

Cần: LANGFUSE_PUBLIC_KEY, LANGFUSE_SECRET_KEY, LANGFUSE_HOST trong .env
     + langfuse:hush trong resources.yaml

Học được:
- LangfuseTracer qua ResourceHub (resource)
- LangfuseTracer qua direct config (LangfuseConfig.from_env)
- Static tags (set on tracer) vs dynamic tags ($tags từ @op)
- user_id, session_id, request_id: correlation trong Langfuse UI
- Truy cập $state sau khi run

Chạy: uv run python examples/08_langfuse_tracing.py
"""

import asyncio
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent.parent / ".env")

from hush.core import END, PARENT, START, GraphOp, Hush
from hush.core.ops import op

# =============================================================================
# Code ops với dynamic tags
# =============================================================================


@op(rust="./rust_ops::text::preprocess")
def preprocess(text: str):
    """Tiền xử lý text, thêm dynamic tags."""
    cleaned = text.strip().lower()
    tags = ["preprocessed"]
    if len(cleaned) > 30:
        tags.append("long-text")
    return {"cleaned": cleaned, "$tags": tags}


@op(rust="./rust_ops::analytics::tokenize")
def tokenize(text: str):
    """Tách text thành tokens."""
    tokens = text.split()
    tags = ["tokenized"]
    if len(tokens) > 5:
        tags.append("many-tokens")
    return {"tokens": tokens, "count": len(tokens), "$tags": tags}


@op(rust="./rust_ops::iteration::each_token")
def each_token(tokens: list):
    """Yield từng token — thay thế MapOp + Each."""
    for token in tokens:
        yield {"token": token}


@op(rust="./rust_ops::math::score_token")
def score_token(token: str, multiplier: int):
    """Tính score cho 1 token."""
    return {"score": len(token) * multiplier}


@op(rust="./rust_ops::analytics::aggregate_stats")
def aggregate(scores: list):
    """Tổng hợp scores."""
    total = sum(scores) if scores else 0
    avg = total / len(scores) if scores else 0
    tags = ["aggregated"]
    if avg > 20:
        tags.append("high-score")
    return {"total": total, "average": avg, "$tags": tags}


@op(rust="./rust_ops::analytics::classify_by_score")
def classify(score: float):
    """Phân loại dựa trên score."""
    if score > 50:
        cat = "high"
    elif score > 25:
        cat = "medium"
    else:
        cat = "low"
    return {"category": cat, "$tags": [f"category:{cat}"]}


# =============================================================================
# Workflow builder
# =============================================================================


def build_text_analysis():
    """Pipeline: preprocess → tokenize → [score each token] → aggregate → classify.

    Generator stream (each_token >> score_token) is wrapped in a subgraph
    so results auto-collect as a list at the subgraph boundary.
    """
    # Subgraph: iterate tokens and score each one
    with GraphOp(name="score-tokens") as score_graph:
        et = each_token(tokens=PARENT["tokens"])
        sc = score_token(token=et["token"], multiplier=PARENT["multiplier"])
        START >> et >> sc >> END

    with GraphOp(name="text-analysis") as graph:
        prep = preprocess(text=PARENT["text"])
        tok = tokenize(text=prep["cleaned"])

        # score_graph returns collected list of scores
        scores = score_graph(
            name="score_tokens",
            tokens=tok["tokens"],
            multiplier=PARENT["multiplier"],
        )

        agg = aggregate(scores=scores["score"])
        cls = classify(score=agg["average"])

        # Output mapping
        agg["total"] >> PARENT["total"]
        agg["average"] >> PARENT["average"]
        cls["category"] >> PARENT["category"]

        START >> prep >> tok >> scores >> agg >> cls >> END
    return graph


# =============================================================================
# Ví dụ 1: LangfuseTracer qua ResourceHub
# =============================================================================


async def example_1_resource_hub():
    """Dùng resource để load config từ resources.yaml."""
    print("=" * 50)
    print("Ví dụ 1: LangfuseTracer via ResourceHub")
    print("=" * 50)

    import os

    if not os.environ.get("LANGFUSE_PUBLIC_KEY"):
        print("  Skipped — LANGFUSE keys chưa set trong .env")
        return

    from hush.telemetry import LangfuseTracer

    # Tạo tracer với static tags
    tracer = LangfuseTracer(
        resource="langfuse:hush",
        tags=["tutorial", "resource-hub"],
    )

    engine = Hush(build_text_analysis())
    result = await engine.run(
        inputs={"text": "Machine learning transforms data processing", "multiplier": 3},
        tracer=tracer,
        user_id="alice",
        session_id="tutorial-session",
        request_id="tutorial-langfuse-1",
    )

    print(f"  Category: {result['category']}")
    print(f"  Total score: {result['total']}, Average: {result['average']:.1f}")

    # Xem dynamic tags đã thu thập
    state = result["$state"]
    print(f"  All tags: {state.tags}")
    print("  → Check Langfuse UI, filter by tag 'tutorial'")


# =============================================================================
# Ví dụ 2: LangfuseTracer qua direct config
# =============================================================================


async def example_2_direct_config():
    """Dùng LangfuseConfig trực tiếp, không cần ResourceHub."""
    print()
    print("=" * 50)
    print("Ví dụ 2: LangfuseTracer via direct config")
    print("=" * 50)

    import os

    if not os.environ.get("LANGFUSE_PUBLIC_KEY"):
        print("  Skipped — LANGFUSE keys chưa set trong .env")
        return

    from hush.telemetry import LangfuseConfig, LangfuseTracer

    # Load config từ env vars (LANGFUSE_PUBLIC_KEY, LANGFUSE_SECRET_KEY, LANGFUSE_HOST)
    config = LangfuseConfig.from_env()
    print(f"  Langfuse host: {config.host}")

    tracer = LangfuseTracer(config=config, tags=["tutorial", "direct-config"])

    engine = Hush(build_text_analysis())
    result = await engine.run(
        inputs={
            "text": "The quick brown fox jumps over the lazy dog and runs into the forest",
            "multiplier": 4,
        },
        tracer=tracer,
        user_id="bob",
        session_id="tutorial-session",
        request_id="tutorial-langfuse-2",
    )

    print(f"  Category: {result['category']}")
    print(f"  Total score: {result['total']}, Average: {result['average']:.1f}")
    state = result["$state"]
    print(f"  All tags: {state.tags}")
    print("  → Filter by 'direct-config' tag in Langfuse")


# =============================================================================
# Ví dụ 3: So sánh traces từ nhiều users
# =============================================================================


async def example_3_multi_user():
    """Chạy cùng workflow cho nhiều users — dùng user_id/session_id để phân biệt."""
    print()
    print("=" * 50)
    print("Ví dụ 3: Multi-user tracing")
    print("=" * 50)

    import os

    if not os.environ.get("LANGFUSE_PUBLIC_KEY"):
        print("  Skipped — LANGFUSE keys chưa set trong .env")
        return

    from hush.telemetry import LangfuseTracer

    engine = Hush(build_text_analysis())

    users = [
        {"user": "alice", "text": "Deep learning neural networks", "mult": 5},
        {"user": "bob", "text": "Cloud computing scalability", "mult": 2},
    ]

    for u in users:
        tracer = LangfuseTracer(
            resource="langfuse:hush",
            tags=["tutorial", "multi-user"],
        )
        result = await engine.run(
            inputs={"text": u["text"], "multiplier": u["mult"]},
            tracer=tracer,
            user_id=u["user"],
            session_id="tutorial-batch",
        )
        print(f"  {u['user']}: category={result['category']}, total={result['total']}")

    print("  → Filter by user_id in Langfuse to compare")


# =============================================================================
# Main
# =============================================================================


async def main():
    await example_1_resource_hub()
    await example_2_direct_config()
    await example_3_multi_user()


if __name__ == "__main__":
    asyncio.run(main())
