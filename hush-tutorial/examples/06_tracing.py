"""Tutorial 06: Tracing — Theo dõi và debug workflows.

Ví dụ 1-2: Không cần API key (LocalTracer)
Ví dụ 3: Cần LANGFUSE keys trong .env

Học được:
- LocalTracer: lưu traces vào SQLite, debug offline
- Dynamic tags ($tags): phân loại traces
- user_id, session_id: correlation
- LangfuseTracer: gửi traces lên cloud
- Truy cập $state sau khi run

Chạy: cd hush-tutorial && uv run python examples/06_tracing.py
"""

import asyncio
from pathlib import Path

from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent.parent / ".env")

from hush.core import Hush, GraphNode, CodeNode, START, END, PARENT
from hush.core.tracers import LocalTracer
from hush.core.nodes.transform.code_node import code_node


# =============================================================================
# Code nodes với dynamic tags
# =============================================================================

@code_node
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
        "$tags": tags,  # Dynamic tags — sẽ được thêm vào trace
    }


@code_node
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


async def example_1_local_tracer():
    """LocalTracer — Debug offline với SQLite."""
    print("=" * 50)
    print("Ví dụ 1: LocalTracer (offline debug)")
    print("=" * 50)

    with GraphNode(name="text-analyzer") as graph:
        analyze = analyze_text(
            name="analyze",
            inputs={"text": PARENT["text"]},
            outputs={"word_count": PARENT, "preview": PARENT},
        )
        categorize = classify(
            name="classify",
            inputs={"word_count": PARENT["word_count"]},
            outputs={"category": PARENT},
        )
        START >> analyze >> categorize >> END

    # Tạo LocalTracer với static tags
    tracer = LocalTracer(
        name="tutorial-tracer",
        tags=["tutorial", "tracing-demo"],
    )

    engine = Hush(graph)
    result = await engine.run(
        inputs={"text": "Hush là async workflow engine cho GenAI applications"},
        tracer=tracer,
        user_id="user-123",
        session_id="session-456",
    )

    print(f"  Word count: {result['word_count']}")
    print(f"  Category:   {result['category']}")

    # Truy cập $state để debug
    state = result["$state"]
    print(f"  User ID:    {state.user_id}")
    print(f"  Exec order: {state.execution_order}")
    print(f"  Tags:       {state.tags}")
    # Tags sẽ gồm: ['tutorial', 'tracing-demo', 'analyzed', 'short-text', 'category:sentence']


async def example_2_multiple_traces():
    """Chạy nhiều traces với user/session khác nhau."""
    print()
    print("=" * 50)
    print("Ví dụ 2: Multiple traces, different users")
    print("=" * 50)

    with GraphNode(name="simple-pipeline") as graph:
        step = analyze_text(
            name="analyze",
            inputs={"text": PARENT["text"]},
            outputs={"word_count": PARENT, "preview": PARENT},
        )
        START >> step >> END

    engine = Hush(graph)

    # Chạy với nhiều users
    test_cases = [
        {"text": "Hello world", "user_id": "alice", "session_id": "s1"},
        {"text": "Đây là một văn bản dài hơn để test dynamic tags và phân loại", "user_id": "bob", "session_id": "s2"},
    ]

    for tc in test_cases:
        tracer = LocalTracer(name="multi-trace", tags=["batch-test"])
        result = await engine.run(
            inputs={"text": tc["text"]},
            tracer=tracer,
            user_id=tc["user_id"],
            session_id=tc["session_id"],
        )
        state = result["$state"]
        print(f"  User={tc['user_id']}: words={result['word_count']}, tags={state.tags}")


async def example_3_langfuse_tracer():
    """LangfuseTracer — Gửi traces lên Langfuse cloud."""
    print()
    print("=" * 50)
    print("Ví dụ 3: LangfuseTracer (cloud)")
    print("=" * 50)

    try:
        from hush.observability import LangfuseTracer
    except ImportError:
        print("  Skipped — hush-observability[langfuse] chưa cài")
        return

    import os
    if not os.environ.get("LANGFUSE_PUBLIC_KEY"):
        print("  Skipped — LANGFUSE keys chưa set trong .env")
        return

    with GraphNode(name="langfuse-demo") as graph:
        step = analyze_text(
            name="analyze",
            inputs={"text": PARENT["text"]},
            outputs={"word_count": PARENT, "preview": PARENT},
        )
        START >> step >> END

    tracer = LangfuseTracer(
        resource_key="langfuse:default",
        tags=["tutorial", "langfuse-demo"],
    )

    engine = Hush(graph)
    result = await engine.run(
        inputs={"text": "Langfuse giúp theo dõi AI workflows trong production"},
        tracer=tracer,
        user_id="tutorial-user",
        session_id="tutorial-session",
    )

    print(f"  Word count: {result['word_count']}")
    print(f"  Trace sent to Langfuse! Check dashboard.")


async def main():
    await example_1_local_tracer()
    await example_2_multiple_traces()
    await example_3_langfuse_tracer()


if __name__ == "__main__":
    asyncio.run(main())
