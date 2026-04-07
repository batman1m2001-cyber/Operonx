"""14 Streaming Tracing — Generator ops + engine.start() + Langfuse tracing.

Demonstrates:
- Generator ops (yield-based) that produce streaming events
- engine.start() for real-time frame delivery via ExecutionHandle
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

from hush.core import Hush

from ex14_streaming_tracing.workflow import build_async_pipeline, build_text_pipeline

EXAMPLES_DIR = Path(__file__).resolve().parents[1]
ENV_FILE = str(EXAMPLES_DIR / ".env")
RESOURCES_FILE = str(EXAMPLES_DIR.parent / "resources.yaml")

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

    engine = Hush(build_text_pipeline(), env=ENV_FILE, resources=RESOURCES_FILE)
    result = await engine.run(inputs={"text": SAMPLE_TEXT, "chunk_size": 3})
    print(f"  Chunks analyzed: {len(result['result'])}")
    for line in result["result"]:
        print(f"    {line}")


async def example_2_stream():
    """engine.start() — real-time frame events via ExecutionHandle."""
    print()
    print("=" * 55)
    print("2. engine.start() — real-time frames")
    print("=" * 55)

    engine = Hush(build_text_pipeline(), env=ENV_FILE, resources=RESOURCES_FILE)
    handle = engine.start(
        inputs={
            "text": "Hush workflows support both batch and streaming execution modes "
            "with zero changes to op definitions",
            "chunk_size": 2,
        },
    )
    frame_count = 0

    async for op, ctx, data in handle:
        frame_count += 1
        print(f"  FRAME [{op}]: {data}")

    result = await handle.result()
    results = result.get("result", [])
    print(f"\n  DONE: {frame_count} frames, {len(results)} chunks")


async def example_3_async_generator():
    """Async generator with engine.start()."""
    print()
    print("=" * 55)
    print("3. Async generator stream")
    print("=" * 55)

    engine = Hush(build_async_pipeline(), env=ENV_FILE, resources=RESOURCES_FILE)
    handle = engine.start(inputs={"n": 5})
    frame_count = 0

    async for op, ctx, data in handle:
        frame_count += 1
        print(f"  FRAME [{op}]: {data}")

    result = await handle.result()
    labels = result.get("label", [])
    print(f"\n  DONE: {frame_count} frames, {len(labels)} labels")


async def example_4_langfuse():
    """Langfuse tracing with streaming generators."""
    print()
    print("=" * 55)
    print("4. Langfuse tracing")
    print("=" * 55)

    from hush.telemetry import LangfuseConfig, LangfuseTracer

    config = LangfuseConfig.from_env()
    request_id = str(uuid.uuid4())
    tracer = LangfuseTracer(config=config, tags=["streaming", "generator-ops"])

    engine = Hush(build_text_pipeline(), env=ENV_FILE, resources=RESOURCES_FILE)
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
