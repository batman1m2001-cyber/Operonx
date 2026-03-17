"""15 Callbot Streaming — Nested streaming with trace visualization.

Demonstrates:
- Multi-level streaming: audio → VAD → STT → LLM router → TTS
- N-to-M generator relationship (VAD: fixed chunks in, variable segments out)
- Nested @graph for modular sub-workflows (LLM router)
- collect_tree() trace output: named contexts, zero-yield pruning, flattening
- Pretty-print trace tree for debugging
- Langfuse tracing with nested streaming

Examples 1-2: No API keys needed.
Example 3: Requires LANGFUSE_HUSH_* keys in .env.

Chạy: cd examples && uv run python ex15_callbot_streaming/demo.py
"""

import asyncio
import uuid
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent.parent / ".env")

from hush.core import Hush
from hush.core.tracing.collector import TraceCollector
from workflow import build_callbot

# =============================================================================
# Pretty-print trace tree
# =============================================================================


def print_trace_tree(nodes):
    """Print trace nodes as an indented tree."""
    children_of = {}
    for n in nodes:
        pk = n["parent_trace_key"]
        if pk not in children_of:
            children_of[pk] = []
        children_of[pk].append(n)

    def _print(node, prefix="", is_last=True):
        connector = "└── " if is_last else "├── "
        name = node["display_name"]
        kind = node["kind"]
        meta = node.get("metadata", {})

        parts = [name]
        if kind == "generator":
            parts.append(f"(generator, yields={meta.get('yield_count', '?')})")
        elif kind == "graph":
            parts.append("(graph)")
        elif kind == "stream_context":
            parts.append("(context)")
        elif node["node_type"] == "trace":
            parts.append("(trace)")

        label = " ".join(parts)

        if node["parent_trace_key"] is None:
            print(label)
        else:
            print(f"{prefix}{connector}{label}")

        child_prefix = prefix + ("    " if is_last else "│   ")
        kids = children_of.get(node["trace_key"], [])
        for i, child in enumerate(kids):
            _print(child, child_prefix, i == len(kids) - 1)

    roots = [n for n in nodes if n["parent_trace_key"] is None]
    for root in roots:
        _print(root)


# =============================================================================
# Examples
# =============================================================================


async def example_1_run_and_trace():
    """Run the callbot pipeline and print the trace tree."""
    print("=" * 55)
    print("1. Callbot Pipeline — 5 audio chunks, speech at 2 & 4")
    print("=" * 55)
    print()

    callbot = build_callbot()
    engine = Hush(callbot)
    result = await engine.run(inputs={"samples": 5})
    state = result["$state"]

    # Show results
    tts_outputs = result.get("audio_out", [])
    if isinstance(tts_outputs, list):
        print(f"  TTS chunks: {len(tts_outputs)}")
        for chunk in tts_outputs[:5]:
            print(f"    {chunk}")
        if len(tts_outputs) > 5:
            print(f"    ... and {len(tts_outputs) - 5} more")
    print()

    # Trace tree
    collector = TraceCollector(callbot)
    trace = collector.collect(state)
    print("  Trace Tree:")
    print("  " + "-" * 50)
    print_trace_tree(trace["nodes"])
    print()

    summary = trace["summary"]
    print(
        f"  Total ops: {summary['total_ops']}, generators: {summary['stream_count']}, yields: {summary['total_yields']}"
    )


async def example_2_stream():
    """Stream the callbot — real-time token events."""
    print()
    print("=" * 55)
    print("2. Callbot Stream Mode — real-time events")
    print("=" * 55)
    print()

    engine = Hush(build_callbot())
    token_count = 0

    async for event in engine.stream(inputs={"samples": 5}):
        if event["type"] == "token":
            token_count += 1
            if token_count <= 10:
                print(f"  TOKEN [{event.get('op', '?')}]: {event['data']}")
            elif token_count == 11:
                print("  ... (more tokens)")
        elif event["type"] == "done":
            tts_out = event["data"].get("audio_out", [])
            print(
                f"\n  DONE: {token_count} tokens, {len(tts_out) if isinstance(tts_out, list) else '?'} TTS chunks"
            )


async def example_3_langfuse():
    """Push callbot trace to Langfuse."""
    import os

    print()
    print("=" * 55)
    print("3. Callbot + Langfuse Tracing")
    print("=" * 55)

    if not os.environ.get("LANGFUSE_HUSH_PUBLIC_KEY"):
        print("  Skipped — LANGFUSE_HUSH keys not set in .env")
        return

    from hush.telemetry import LangfuseTracer

    request_id = str(uuid.uuid4())
    tracer = LangfuseTracer(resource="langfuse:hush", tags=["callbot", "nested-streaming"])

    engine = Hush(build_callbot())
    result = await engine.run(
        inputs={"samples": 5},
        tracer=tracer,
        user_id="callbot-demo",
        session_id="callbot-session",
        request_id=request_id,
    )

    tts_outputs = result.get("audio_out", [])
    print(f"  TTS chunks: {len(tts_outputs) if isinstance(tts_outputs, list) else '?'}")

    from hush.core.tracing import get_flush_worker

    errors = get_flush_worker().wait(timeout=30)
    if errors:
        for err in errors:
            print(f"  FLUSH ERROR: {err}")
    else:
        host = os.environ.get("LANGFUSE_HUSH_BASE_URL", "").rstrip("/")
        print(f"  Trace: {host}/trace/{request_id}")


async def main():
    await example_1_run_and_trace()
    await example_2_stream()
    await example_3_langfuse()


if __name__ == "__main__":
    asyncio.run(main())
