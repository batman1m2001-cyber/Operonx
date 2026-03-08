"""Tutorial 20: Callbot — Nested Streaming with Trace Visualization

Demonstrates:
- Multi-level streaming: audio → VAD → STT → LLM router → TTS
- N-to-M generator relationship (VAD: fixed chunks in, variable segments out)
- Nested @graph for modular sub-workflows (LLM router)
- collect_tree() trace output: named contexts, zero-yield pruning, flattening
- Pretty-print trace tree for debugging
- Langfuse tracing with nested streaming (requires LANGFUSE keys in .env)

Example 1-2: No API keys needed (local trace tree + stream mode)
Example 3: Requires LANGFUSE_PUBLIC_KEY, LANGFUSE_SECRET_KEY, LANGFUSE_HOST in .env

Run:
    cd tutorial && uv run python examples/20_callbot_streaming.py
"""

import asyncio
import uuid
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent.parent / ".env")

from hush.core import END, PARENT, START, GraphOp, Hush, graph, op
from hush.core.tracing.collector import TraceCollector

# =============================================================================
# Mock ops — simulate a voice callbot pipeline
# =============================================================================


@op
async def customer_audio(sample_count: int):
    """Simulate microphone input — yields fixed-size 32ms audio chunks.

    In production this would read from a WebSocket or audio stream.
    Each chunk is 32ms of audio data.
    """
    for i in range(sample_count):
        await asyncio.sleep(0.005)  # simulate real-time arrival
        yield {"audio": f"chunk_{i}", "timestamp_ms": i * 32}


@op
async def vad(audio: str, timestamp_ms: int):
    """Voice Activity Detection — N-to-M generator.

    Receives fixed 32ms chunks but yields variable-length speech segments.
    Silence chunks yield nothing (0 outputs). Speech chunks yield 1 segment.

    In production: a VAD model accumulates audio and emits segments
    when it detects speech boundaries (not 1:1 with input chunks).
    """
    # Simulate: chunks at 64ms and 128ms contain speech
    speech_timestamps = {64, 128}
    if timestamp_ms in speech_timestamps:
        yield {
            "segment": f"speech_from_{audio}",
            "start_ms": timestamp_ms,
            "end_ms": timestamp_ms + 32,
        }
    # Silence chunks → yield nothing → zero-yield generator execution


@op
def stt(segment: str, start_ms: int, end_ms: int):
    """Speech-to-Text — transcribe a VAD segment.

    In production: call Whisper, Deepgram, or similar ASR service.
    """
    return {"transcript": f"Hello from {segment} [{start_ms}-{end_ms}ms]"}


@op
def classify_intent(transcript: str):
    """Classify user intent from transcript.

    In production: call an LLM or intent classifier.
    """
    if "hello" in transcript.lower():
        return {"intent": "greeting", "confidence": 0.95}
    return {"intent": "general", "confidence": 0.8}


@op
def handle_intent(intent: str, transcript: str):
    """Generate a response based on intent.

    In production: call an LLM with the appropriate system prompt.
    """
    if intent == "greeting":
        return {"response": "Hello! How can I help you today?"}
    return {"response": f"I understand. Let me help with: {transcript}"}


@graph
def llm_router(transcript):
    """Nested graph: classify intent → generate response.

    This becomes a single 'graph' node in the trace tree,
    with classify and handle as children.
    """
    c = classify_intent(transcript=transcript)
    h = handle_intent(intent=c["intent"], transcript=transcript)
    START >> c >> h >> END


@op
async def tts(response: str):
    """Text-to-Speech — yields audio chunks word by word.

    In production: call a TTS service (ElevenLabs, Azure TTS)
    that streams audio chunks back.
    """
    words = response.split()
    for i, word in enumerate(words):
        await asyncio.sleep(0.003)  # simulate synthesis time
        yield {"audio_out": f"tts_{i}_{word}", "index": i}


# =============================================================================
# Build the callbot pipeline
# =============================================================================
def build_callbot():
    """Build the full callbot pipeline.

    Pipeline:
        customer_audio (yields chunks)
            → vad (yields segments, 0 for silence)
                → stt (transcribe)
                    → llm_router (nested graph: classify → handle)
                        → tts (yields audio output)

    Expected trace tree (with 5 audio chunks, speech at chunks 2 and 4):

        callbot (trace)
        ├── audio (generator, yields=5)
        ├── audio:2                          ← named after spawner
        │   ├── v (generator, yields=1)
        │   ├── transcribe                   ← flattened from v:0
        │   ├── router (graph)
        │   │   ├── c
        │   │   └── h
        │   └── speak (generator, yields=N)
        └── audio:4
            ├── v (generator, yields=1)
            ├── transcribe
            ├── router (graph)
            │   ├── c
            │   └── h
            └── speak (generator, yields=N)

    Chunks 0, 1, 3 → vad yields=0 → pruned from trace (zero-yield)
    v:0 inner context → flattened (single-yield)
    """
    with GraphOp(name="callbot") as g:
        audio = customer_audio(sample_count=PARENT["samples"])
        v = vad(audio=audio["audio"], timestamp_ms=audio["timestamp_ms"])
        transcribe = stt(
            segment=v["segment"],
            start_ms=v["start_ms"],
            end_ms=v["end_ms"],
        )
        router = llm_router(transcript=transcribe["transcript"])
        speak = tts(response=router["response"])

        START >> audio >> v >> transcribe >> router >> speak >> END

    return g


# =============================================================================
# Pretty-print trace tree
# =============================================================================
def print_trace_tree(nodes):
    """Print trace nodes as an indented tree."""
    # Build parent → children map
    key_to_node = {n["trace_key"]: n for n in nodes}
    children_of = {}
    for n in nodes:
        pk = n["parent_trace_key"]
        if pk not in children_of:
            children_of[pk] = []
        children_of[pk].append(n)

    def _print(node, prefix="", is_last=True):
        connector = "└── " if is_last else "├── "
        # Build label
        name = node["display_name"]
        kind = node["kind"]
        meta = node.get("metadata", {})

        parts = [name]
        if kind == "generator":
            yc = meta.get("yield_count", "?")
            parts.append(f"(generator, yields={yc})")
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

        # Print children
        child_prefix = prefix + ("    " if is_last else "│   ")
        kids = children_of.get(node["trace_key"], [])
        for i, child in enumerate(kids):
            _print(child, child_prefix, i == len(kids) - 1)

    # Find root
    roots = [n for n in nodes if n["parent_trace_key"] is None]
    for root in roots:
        _print(root)


# =============================================================================
# Example 1: Run callbot and inspect trace
# =============================================================================
async def example_run_and_trace():
    """Run the callbot pipeline and print the trace tree."""
    print("=" * 60)
    print("Callbot Pipeline — 5 audio chunks, speech at chunks 2 & 4")
    print("=" * 60)
    print()

    callbot = build_callbot()
    engine = Hush(callbot)

    result = await engine.run(inputs={"samples": 5})
    state = result["$state"]

    # Show results
    print("Results:")
    tts_outputs = result.get("audio_out", [])
    if isinstance(tts_outputs, list):
        print(f"  TTS chunks generated: {len(tts_outputs)}")
        for chunk in tts_outputs[:5]:
            print(f"    {chunk}")
        if len(tts_outputs) > 5:
            print(f"    ... and {len(tts_outputs) - 5} more")
    print()

    # Collect and print trace tree
    collector = TraceCollector()
    trace = collector.collect_tree(callbot, state)

    print("Trace Tree:")
    print("-" * 60)
    print_trace_tree(trace["nodes"])
    print()

    # Show summary
    summary = trace["summary"]
    print("Summary:")
    print(f"  Total ops:     {summary['total_ops']}")
    print(f"  Total records: {summary['total_records']}")
    print(f"  Generators:    {summary['stream_count']}")
    print(f"  Total yields:  {summary['total_yields']}")
    print()

    # Verify key properties
    nodes = trace["nodes"]
    ctx_nodes = [n for n in nodes if n["kind"] == "stream_context"]
    ctx_names = [n["display_name"] for n in ctx_nodes]
    print("Stream contexts:", ctx_names)

    gen_nodes = [n for n in nodes if n["kind"] == "generator"]
    gen_info = [(n["display_name"], n["metadata"].get("yield_count", "?")) for n in gen_nodes]
    print("Generators:", gen_info)

    graph_nodes = [n for n in nodes if n["kind"] == "graph"]
    print("Nested graphs:", [n["display_name"] for n in graph_nodes])

    # Verify no orphaned nodes
    all_keys = {n["trace_key"] for n in nodes}
    orphans = [
        n["display_name"]
        for n in nodes
        if n["parent_trace_key"] is not None and n["parent_trace_key"] not in all_keys
    ]
    print(f"Orphaned nodes: {orphans if orphans else 'none'}")
    print()


# =============================================================================
# Example 2: Stream mode — real-time token events
# =============================================================================
async def example_stream():
    """Stream the callbot and show real-time events."""
    print("=" * 60)
    print("Callbot Stream Mode — real-time token events")
    print("=" * 60)
    print()

    callbot = build_callbot()
    engine = Hush(callbot)

    token_count = 0
    async for event in engine.stream(inputs={"samples": 5}):
        if event["type"] == "token":
            token_count += 1
            op_name = event.get("op", "?")
            data = event["data"]
            # Only print a subset to keep output manageable
            if token_count <= 10:
                print(f"  TOKEN [{op_name}]: {data}")
            elif token_count == 11:
                print("  ... (more tokens)")
        elif event["type"] == "done":
            print(f"\n  DONE: {token_count} token events total")
            tts_out = event["data"].get("audio_out", [])
            print(f"  TTS output chunks: {len(tts_out) if isinstance(tts_out, list) else '?'}")
    print()


# =============================================================================
# Example 3: Langfuse tracing — see callbot trace in Langfuse dashboard
# =============================================================================
async def example_langfuse_tracing():
    """Push callbot trace to Langfuse — nested streaming with named contexts."""
    import os

    print("=" * 60)
    print("Callbot + Langfuse Tracing")
    print("=" * 60)
    print()

    if not os.environ.get("LANGFUSE_PUBLIC_KEY"):
        print("  Skipped — LANGFUSE keys not set in .env")
        print("  Set LANGFUSE_PUBLIC_KEY, LANGFUSE_SECRET_KEY, LANGFUSE_HOST")
        return

    from hush.telemetry import LangfuseConfig, LangfuseTracer

    # Verify auth first
    config = LangfuseConfig.from_env()
    from hush.telemetry.backends.langfuse import LangfuseClient

    client = LangfuseClient(config)
    if not client.auth_check():
        print("  ERROR: Langfuse auth failed! Check your keys.")
        return
    print("  Langfuse auth OK")

    request_id = str(uuid.uuid4())
    tracer = LangfuseTracer(
        config=config,
        tags=["callbot", "nested-streaming"],
    )

    callbot = build_callbot()
    engine = Hush(callbot)

    result = await engine.run(
        inputs={"samples": 5},
        tracer=tracer,
        user_id="callbot-demo",
        session_id="callbot-session",
        request_id=request_id,
    )

    tts_outputs = result.get("audio_out", [])
    print(f"  TTS chunks: {len(tts_outputs) if isinstance(tts_outputs, list) else '?'}")

    # Wait for flush to complete and surface any errors
    from hush.core.tracing import get_flush_worker

    errors = get_flush_worker().wait(timeout=30)
    if errors:
        print()
        print("  FLUSH ERRORS:")
        for err in errors:
            print(f"    {err}")
    else:
        trace_url = client.trace_url(request_id)
        print(f"  Trace URL: {trace_url}")
    print()


# =============================================================================
# Main
# =============================================================================
async def main():
    await example_run_and_trace()
    await example_stream()
    await example_langfuse_tracing()


if __name__ == "__main__":
    asyncio.run(main())
