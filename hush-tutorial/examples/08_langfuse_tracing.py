"""Tutorial 08: Langfuse Tracing — Gửi traces lên Langfuse cloud.

Cần: LANGFUSE_PUBLIC_KEY, LANGFUSE_SECRET_KEY, LANGFUSE_HOST trong .env
     + langfuse:default trong resources.yaml

Học được:
- LangfuseTracer qua ResourceHub (resource_key)
- LangfuseTracer qua direct config (LangfuseConfig.from_env)
- Static tags (set on tracer) vs dynamic tags ($tags từ code_node)
- user_id, session_id, request_id: correlation trong Langfuse UI
- Truy cập $state sau khi run

Chạy: cd hush-tutorial && uv run python examples/08_langfuse_tracing.py
"""

import asyncio
from pathlib import Path
from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent.parent / ".env")

from hush.core import Hush, GraphNode, START, END, PARENT
from hush.core.nodes.iteration.map_node import MapNode
from hush.core.nodes.iteration.base import Each
from hush.core.nodes.transform.code_node import code_node
from hush.core.tracers import BaseTracer


# =============================================================================
# Code nodes với dynamic tags
# =============================================================================

@code_node
def preprocess(text: str):
    """Tiền xử lý text, thêm dynamic tags."""
    cleaned = text.strip().lower()
    tags = ["preprocessed"]
    if len(cleaned) > 30:
        tags.append("long-text")
    return {"cleaned": cleaned, "$tags": tags}


@code_node
def tokenize(text: str):
    """Tách text thành tokens."""
    tokens = text.split()
    tags = ["tokenized"]
    if len(tokens) > 5:
        tags.append("many-tokens")
    return {"tokens": tokens, "count": len(tokens), "$tags": tags}


@code_node
def score_token(token: str, multiplier: int):
    """Tính score cho 1 token."""
    return {"score": len(token) * multiplier}


@code_node
def aggregate(scores: list):
    """Tổng hợp scores."""
    total = sum(scores) if scores else 0
    avg = total / len(scores) if scores else 0
    tags = ["aggregated"]
    if avg > 20:
        tags.append("high-score")
    return {"total": total, "average": avg, "$tags": tags}


@code_node
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
    """Pipeline: preprocess → tokenize → map(score) → aggregate → classify."""
    with GraphNode(name="text-analysis") as graph:
        prep = preprocess(
            name="preprocess",
            inputs={"text": PARENT["text"]},
        )
        tok = tokenize(
            name="tokenize",
            inputs={"text": prep["cleaned"]},
        )
        with MapNode(
            name="score_tokens",
            inputs={
                "token": Each(tok["tokens"]),
                "multiplier": PARENT["multiplier"],
            }
        ) as map_node:
            sc = score_token(
                name="score",
                inputs={"token": PARENT["token"], "multiplier": PARENT["multiplier"]},
                outputs={"*": PARENT},
            )
            START >> sc >> END

        agg = aggregate(
            name="aggregate",
            inputs={"scores": map_node["score"]},
        )
        cls = classify(
            name="classify",
            inputs={"score": agg["average"]},
            outputs={"category": PARENT},
        )
        agg["total"] >> PARENT["total"]
        agg["average"] >> PARENT["average"]

        START >> prep >> tok >> map_node >> agg >> cls >> END
    return graph


# =============================================================================
# Ví dụ 1: LangfuseTracer qua ResourceHub
# =============================================================================

async def example_1_resource_hub():
    """Dùng resource_key để load config từ resources.yaml."""
    print("=" * 50)
    print("Ví dụ 1: LangfuseTracer via ResourceHub")
    print("=" * 50)

    import os
    if not os.environ.get("LANGFUSE_PUBLIC_KEY"):
        print("  Skipped — LANGFUSE keys chưa set trong .env")
        return

    from hush.observability import LangfuseTracer

    # Tạo tracer với static tags
    tracer = LangfuseTracer(
        resource_key="langfuse:default",
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

    from hush.observability import LangfuseTracer, LangfuseConfig

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

    from hush.observability import LangfuseTracer

    engine = Hush(build_text_analysis())

    users = [
        {"user": "alice", "text": "Deep learning neural networks", "mult": 5},
        {"user": "bob", "text": "Cloud computing scalability", "mult": 2},
    ]

    for u in users:
        tracer = LangfuseTracer(
            resource_key="langfuse:default",
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
# Cleanup & main
# =============================================================================

async def main():
    await example_1_resource_hub()
    await example_2_direct_config()
    await example_3_multi_user()

    # Flush traces
    print()
    print("Flushing traces to Langfuse...")
    BaseTracer.shutdown_executor()
    print("Done!")


if __name__ == "__main__":
    asyncio.run(main())
