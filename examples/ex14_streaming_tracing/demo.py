"""14 Streaming Tracing — Generator ops + engine.stream() + Langfuse tracing.

Demonstrates:
- Generator ops (yield-based) that produce streaming events
- engine.stream() for real-time token delivery
- engine.run() with generator ops (accumulated result)
- Langfuse tracing with streaming metadata (kind, yield_count, spawned_by)

Examples 1-3: No API keys needed.
Example 4: Requires LANGFUSE keys in .env.

Chạy: cd examples && uv run python ex14_streaming_tracing/demo.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import asyncio
import uuid
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent.parent / ".env")

from hush.core import Hush

from ex14_streaming_tracing.workflow import build_async_pipeline, build_text_pipeline

SAMPLE_TEXT = (
    "The streaming architecture enables real-time token delivery "
    "from generator ops through an event queue scheduler with "
    "tuple contexts and proper EOF propagation"
)


async def example_1_run():
    """engine.run() with generator ops — returns accumulated result."""
    print("=" * 55)
    print("1. engine.run() with generator ops")
    print("=" * 55)

    engine = Hush(build_text_pipeline())
    result = await engine.run(inputs={"text": SAMPLE_TEXT, "chunk_size": 3})
    print(f"  Chunks analyzed: {len(result['result'])}")
    for line in result["result"]:
        print(f"    {line}")


async def example_2_stream():
    """engine.stream() — real-time token events."""
    print()
    print("=" * 55)
    print("2. engine.stream() — real-time events")
    print("=" * 55)

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
            print(f"  TOKEN [{event.get('op', '?')}]: {event['data']}")
        elif event["type"] == "done":
            results = event["data"].get("result", [])
            print(f"\n  DONE: {token_count} tokens, {len(results)} chunks")


async def example_3_async_generator():
    """Async generator with engine.stream()."""
    print()
    print("=" * 55)
    print("3. Async generator stream")
    print("=" * 55)

    engine = Hush(build_async_pipeline())
    token_count = 0

    async for event in engine.stream(inputs={"n": 5}):
        if event["type"] == "token":
            token_count += 1
            print(f"  TOKEN: {event['data']}")
        elif event["type"] == "done":
            labels = event["data"].get("label", [])
            print(f"\n  DONE: {token_count} tokens, {len(labels)} labels")


async def example_4_langfuse():
    """Langfuse tracing with streaming generators."""
    import os

    print()
    print("=" * 55)
    print("4. Langfuse tracing")
    print("=" * 55)

    if not os.environ.get("LANGFUSE_PUBLIC_KEY"):
        print("  Skipped — LANGFUSE keys not set in .env")
        return

    from hush.telemetry import LangfuseConfig, LangfuseTracer

    config = LangfuseConfig.from_env()
    request_id = str(uuid.uuid4())
    tracer = LangfuseTracer(config=config, tags=["streaming", "generator-ops"])

    engine = Hush(build_text_pipeline())
    result = await engine.run(
        inputs={"text": SAMPLE_TEXT, "chunk_size": 3},
        tracer=tracer,
        user_id="streaming-demo",
        session_id="streaming-session",
        request_id=request_id,
    )

    print(f"  Chunks analyzed: {len(result['result'])}")

    from hush.core.tracing import get_flush_worker

    errors = get_flush_worker().wait(timeout=30)
    if errors:
        for err in errors:
            print(f"  FLUSH ERROR: {err}")
    else:
        from hush.telemetry.backends.langfuse import LangfuseClient

        client = LangfuseClient(config)
        print(f"  Trace: {client.trace_url(request_id)}")


async def main():
    await example_1_run()
    await example_2_stream()
    await example_3_async_generator()
    await example_4_langfuse()


if __name__ == "__main__":
    asyncio.run(main())
