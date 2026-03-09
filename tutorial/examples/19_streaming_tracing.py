"""Tutorial 19: Streaming Generator Ops + Langfuse Tracing

Demonstrates:
- Generator ops (yield-based) that produce streaming events
- engine.stream() for real-time token delivery
- engine.run() with generator ops (accumulated result)
- Langfuse tracing with streaming metadata (kind, yield_count, spawned_by)

Prerequisites:
- LANGFUSE_PUBLIC_KEY, LANGFUSE_SECRET_KEY, LANGFUSE_HOST in .env
- langfuse:hush in resources.yaml

Run:
    cd tutorial && uv run python examples/19_streaming_tracing.py
"""

import asyncio
import uuid
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent.parent / ".env")

from hush.core import END, PARENT, START, GraphOp, Hush, op

# =============================================================================
# Generator ops — yield items one-by-one
# =============================================================================


@op
def chunk_text(text: str, chunk_size: int):
    """Generator: splits text into chunks, yielding each one."""
    words = text.split()
    for i in range(0, len(words), chunk_size):
        chunk = " ".join(words[i : i + chunk_size])
        yield {"chunk": chunk, "index": i // chunk_size}


@op
def analyze_chunk(chunk: str, index: int):
    """Analyze a single chunk — runs once per yield from chunk_text.

    Returns a score and summary per chunk. The scheduler accumulates
    these into lists for the next batch op downstream.
    """
    word_count = len(chunk.split())
    has_long_word = any(len(w) > 6 for w in chunk.split())
    score = word_count * 10 + (15 if has_long_word else 0)
    return {
        "result": f"[{index}] {word_count}w score={score}{'*' if has_long_word else ''}",
    }


# =============================================================================
# Async generator op
# =============================================================================
@op
async def async_counter(n: int):
    """Async generator: yields numbers 1..n with a simulated delay."""
    for i in range(1, n + 1):
        await asyncio.sleep(0.01)  # simulate async work
        yield {"number": i, "squared": i * i}


@op
def format_square(number: int, squared: int):
    """Format a single squared value — runs once per yield from async_counter."""
    return {"label": f"{number}^2 = {squared}"}


# =============================================================================
# Build workflows
# =============================================================================
def build_text_pipeline():
    """Pipeline: chunk_text (generator) >> analyze >> END.

    chunk_text yields chunks one-by-one. analyze_chunk runs per-yield.
    The scheduler accumulates analyze_chunk's outputs into lists.
    """
    with GraphOp(name="text-chunker") as graph:
        chunker = chunk_text(text=PARENT["text"], chunk_size=PARENT["chunk_size"])
        analyzer = analyze_chunk(chunk=chunker["chunk"], index=chunker["index"])

        START >> chunker >> analyzer >> END
    return graph


def build_async_pipeline():
    """Pipeline: async_counter (async generator) >> format_square.

    async_counter yields {number, squared} per item. format_square runs
    per-yield, producing a label. The scheduler accumulates into lists.
    """
    with GraphOp(name="async-counter") as graph:
        counter = async_counter(n=PARENT["n"])
        fmt = format_square(number=counter["number"], squared=counter["squared"])

        START >> counter >> fmt >> END
    return graph


# =============================================================================
# Example 1: engine.run() — accumulated result
# =============================================================================
async def example_run():
    """Run generator workflow with engine.run() — returns final result."""
    print("=" * 60)
    print("Example 1: engine.run() with generator ops")
    print("=" * 60)

    engine = Hush(build_text_pipeline())
    result = await engine.run(
        inputs={
            "text": "The streaming architecture enables real-time token delivery "
            "from generator ops through an event queue scheduler with "
            "tuple contexts and proper EOF propagation",
            "chunk_size": 3,
        },
    )

    print(f"  Chunks analyzed: {len(result['result'])}")
    for line in result["result"]:
        print(f"    {line}")
    print()


# =============================================================================
# Example 2: engine.stream() — real-time events
# =============================================================================
async def example_stream():
    """Stream generator workflow — yields token events in real-time."""
    print("=" * 60)
    print("Example 2: engine.stream() with real-time events")
    print("=" * 60)

    engine = Hush(build_text_pipeline())
    token_count = 0

    async for event in engine.stream(
        inputs={
            "text": "Hush workflows support both batch and streaming execution modes "
            "with zero changes to op definitions",
            "chunk_size": 2,
        },
    ):
        if event["type"] == "token":
            token_count += 1
            op_name = event.get("op", "?")
            data = event["data"]
            print(f"  TOKEN [{op_name}]: {data}")
        elif event["type"] == "done":
            print(f"\n  DONE: {token_count} token events received")
            results = event["data"].get("result", [])
            print(f"  Final result: {len(results)} chunks analyzed")
    print()


# =============================================================================
# Example 3: async generator + stream
# =============================================================================
async def example_async_generator():
    """Async generator op streamed in real-time."""
    print("=" * 60)
    print("Example 3: Async generator with engine.stream()")
    print("=" * 60)

    engine = Hush(build_async_pipeline())
    token_count = 0

    async for event in engine.stream(
        inputs={"n": 5},
    ):
        if event["type"] == "token":
            token_count += 1
            print(f"  TOKEN: {event['data']}")
        elif event["type"] == "done":
            labels = event["data"].get("label", [])
            print(f"\n  DONE: {token_count} tokens, {len(labels)} labels")
            for lbl in labels:
                print(f"    {lbl}")
    print()


# =============================================================================
# Example 4: Langfuse tracing with streaming metadata
# =============================================================================
async def example_langfuse_tracing():
    """Push streaming traces to Langfuse — shows kind, yield_count in metadata."""
    import os

    print("=" * 60)
    print("Example 4: Langfuse tracing with streaming generators")
    print("=" * 60)

    if not os.environ.get("LANGFUSE_PUBLIC_KEY"):
        print("  Skipped — LANGFUSE keys not set in .env")
        print("  Set LANGFUSE_PUBLIC_KEY, LANGFUSE_SECRET_KEY, LANGFUSE_HOST")
        return

    from hush.telemetry import LangfuseConfig, LangfuseTracer

    config = LangfuseConfig.from_env()

    request_id_1 = str(uuid.uuid4())
    tracer = LangfuseTracer(
        config=config,
        tags=["streaming", "generator-ops"],
    )

    # Use engine.run() so tracing captures the full execution
    engine = Hush(build_text_pipeline())
    result = await engine.run(
        inputs={
            "text": "The event queue scheduler drives generator ops yield by yield "
            "creating stream contexts as tuple suffixes while downstream "
            "batch ops accumulate results into lists automatically",
            "chunk_size": 3,
        },
        tracer=tracer,
        user_id="streaming-demo",
        session_id="streaming-session",
        request_id=request_id_1,
    )

    print(f"  Chunks analyzed: {len(result['result'])}")
    for line in result["result"]:
        print(f"    {line}")
    print()

    # Also trace the async generator pipeline
    engine2 = Hush(build_async_pipeline())
    request_id_2 = str(uuid.uuid4())
    tracer2 = LangfuseTracer(
        config=config,
        tags=["streaming", "async-generator"],
    )

    result2 = await engine2.run(
        inputs={"n": 7},
        tracer=tracer2,
        user_id="streaming-demo",
        session_id="streaming-session",
        request_id=request_id_2,
    )

    print(f"  Squares (1..7): {result2['label']}")
    print()

    # Wait for flush to complete and surface any errors
    from hush.core.tracing import get_flush_worker

    errors = get_flush_worker().wait(timeout=30)
    if errors:
        print("  FLUSH ERRORS:")
        for err in errors:
            print(f"    {err}")
    else:
        from hush.telemetry.backends.langfuse import LangfuseClient

        client = LangfuseClient(config)
        print(f"  Trace 1: {client.trace_url(request_id_1)}")
        print(f"  Trace 2: {client.trace_url(request_id_2)}")
    print()


# =============================================================================
# Main
# =============================================================================
async def main():
    await example_run()
    await example_stream()
    await example_async_generator()
    await example_langfuse_tracing()


if __name__ == "__main__":
    asyncio.run(main())
