"""Mock trace → Langfuse Edupia — cover every case in TRACING_V2_PLAN.md §6.

Each case is a real operonx graph: one or more ``@op`` functions composed
in a ``GraphOp``, executed via ``Operon(g).run(sink=..., trace_id=...)``.
This exercises the FULL stack — engine → ContextVar → user op body →
event()/span() → LangfuseSink → ingestion API.

Cases:
  1. sync_call         — sync op emits one paired span
  2. streaming         — async generator op, 1 input + N outputs
  3. many_inputs       — one op emits M inputs + N outputs
  4. path_grouping     — multi-op graph with shared path prefixes
  5. nested_iterations — op with a loop; sink auto-indexes span() collisions
  6. error             — op emits kind="error"
  7. log_annotation    — op emits kind="log" mid-execution
  8. media_upload      — op emits binary bytes → auto-uploaded
  9. mixed_kinds       — one op interleaves input/output/log/error
 10. ctx_streaming     — op with two named ctx streams at the same path

Usage:
    uv run python scripts/mock_trace_langfuse.py

Reads LANGFUSE_EDUPIA_* from ./.env.
"""

from __future__ import annotations

import asyncio
import os
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

from operonx import event, span  # noqa: E402
from operonx.core import END, PARENT, START, GraphOp, Operon, op  # noqa: E402
from operonx.telemetry.backends.langfuse import LangfuseClient, LangfuseConfig  # noqa: E402
from operonx.telemetry.sinks import LangfuseSink  # noqa: E402


# ============================================================
# Setup — Langfuse Edupia client
# ============================================================


def _build_client() -> LangfuseClient:
    config = LangfuseConfig(
        public_key=os.environ["LANGFUSE_EDUPIA_PUBLIC_KEY"],
        secret_key=os.environ["LANGFUSE_EDUPIA_SECRET_KEY"],
        host=os.environ["LANGFUSE_EDUPIA_BASE_URL"],
    )
    return LangfuseClient(config)


# ============================================================
# Ops — one per demo case (real @op decorators, real graphs)
# ============================================================


@op
def op_sync_add(a: int, b: int):
    """Atomic sync op → one Langfuse SPAN with input+output."""
    result = a + b
    span("math/add", input={"a": a, "b": b}, output={"result": result})
    return {"result": result}


@op
async def op_stt_stream(audio_id: str):
    """Streaming op — 1 input event + N output events at same path."""
    event("speech/stt", {"audio_id": audio_id}, kind="input")
    partials = ["xin", "xin chào", "xin chào cô", "xin chào cô giáo"]
    final = partials[-1]
    for p in partials:
        await asyncio.sleep(0.02)
        event("speech/stt", {"partial": p}, kind="output")
    await asyncio.sleep(0.02)
    event("speech/stt", {"final": final}, kind="output")
    return {"result": final}


@op
async def op_many_io(nothing: int = 0):
    """M inputs + N outputs at same path (M != N)."""
    for i in range(3):
        event("audio/consumer", {"chunk_id": i, "size": 512}, kind="input")
        await asyncio.sleep(0.01)
    for j in range(5):
        event("audio/consumer", {"partial_result": f"p{j}"}, kind="output")
        await asyncio.sleep(0.01)
    return {"done": True}


@op
def op_normalize(text: str):
    span("speech/normalize", input={"text": text}, output={"norm": text.strip().lower()})
    return {"norm": text.strip().lower()}


@op
def op_stt(audio_id: str):
    span("speech/stt", input={"audio_id": audio_id}, output={"text": "xin chào"})
    return {"text": "xin chào"}


@op
def op_merge(a: int, b: int):
    span("state/merge", input={"a": a, "b": b}, output={"merged": a + b})
    return {"merged": a + b}


@op
def op_state_transition(fro: str, evt: str):
    to = "CONFIRMED" if evt == "confirm" else "IDLE"
    span("state/transition", input={"from": fro, "event": evt}, output={"to": to})
    return {"to": to}


@op
def op_tts(text: str):
    url = f"s3://tts/{text[:8]}.wav"
    span("tts", input={"text": text}, output={"audio_url": url})
    return {"url": url}


@op
def op_loop_iterations(items: list):
    """Loop calling ``span()`` N times at the same path.

    Sink sees repeated span() emits at the same (path, runtime_ctx) key
    and auto-indexes them — 1st gets a clean name, subsequent get
    ``[1]``, ``[2]`` … suffixes. Author writes no counter.
    """
    for item in items:
        span("loop/step", input={"item": item}, output={"processed": f"[{item}]"})
    return {"count": len(items)}


@op
def op_risky(x: str):
    """Emits kind='error' then re-raises to demonstrate error rendering."""
    event("risky/parse", {"input": x}, kind="input")
    try:
        _ = int(x)  # will raise for non-numeric
    except Exception as e:
        event(
            "risky/parse",
            {"error": f"{type(e).__name__}: {e}"},
            kind="error",
        )
    return {"handled": True}


@op
async def op_llm_generate(prompt: str):
    """Op with mid-execution kind='log' annotations."""
    event("llm/generate", {"prompt": prompt}, kind="input")
    event("llm/generate", {"phase": "prompt tokens=12"}, kind="log")
    event("llm/generate", {"phase": "streaming started"}, kind="log")
    await asyncio.sleep(0.01)
    event("llm/generate", {"phase": "completion tokens=8"}, kind="log")
    event("llm/generate", {"output": "hello!"}, kind="output")
    return {"answer": "hello!"}


@op
def op_media(marker: str):
    """Emits a paired span with big audio bytes → sink auto-uploads."""
    small_bytes = b"small_payload"
    fake_audio = b"WAV" + os.urandom(8192)
    span(
        "media/upload",
        input={"marker": marker, "audio": fake_audio, "tiny": small_bytes},
        output={"result": "processed", "processed_audio": fake_audio[::-1]},
    )
    return {"uploaded": True}


@op
async def op_agent_orchestrate(task: str):
    """Interleaved input/output/log for an orchestration-style op."""
    event("agent/orchestrate", {"task": task}, kind="input")
    event("agent/orchestrate", {"phase": "planning"}, kind="log")
    await asyncio.sleep(0.01)
    event("agent/orchestrate", {"phase": "calling llm"}, kind="log")
    event("agent/orchestrate", {"llm_call": 1, "tokens": 42}, kind="output")
    await asyncio.sleep(0.01)
    event("agent/orchestrate", {"phase": "tool call"}, kind="log")
    event("agent/orchestrate", {"tool": "calendar_api", "status": 200}, kind="output")
    event("agent/orchestrate", {"result": "meeting booked for 2pm"}, kind="output")
    return {"done": True}


@op
def op_two_streams(audio_a: str, audio_b: str):
    """Two logically distinct streams within ONE op invocation.

    Author expresses distinctness via DISTINCT PATHS (``speech/stt/A``,
    ``speech/stt/B``), not by inventing ctx labels. Engine-provided ctx
    handles cross-op-invocation distinction; author-provided path
    handles within-invocation logical grouping.
    """
    # Stream A
    event("speech/stt/A", {"audio_id": audio_a}, kind="input")
    for p in ["hello", "hello world"]:
        event("speech/stt/A", {"partial": p}, kind="output")
    event("speech/stt/A", {"final": "hello world"}, kind="output")
    # Stream B — distinct path
    event("speech/stt/B", {"audio_id": audio_b}, kind="input")
    for p in ["hi", "hi there"]:
        event("speech/stt/B", {"partial": p}, kind="output")
    event("speech/stt/B", {"final": "hi there"}, kind="output")
    return {"streams": 2}


# ============================================================
# Graph builders — each case wraps its op(s) in a GraphOp
# ============================================================


def _single_op_graph(name: str, op_factory, **inputs_map):
    """Build a GraphOp with one op wired to PARENT[...] inputs."""
    with GraphOp(name=name) as g:
        step = op_factory(**inputs_map)
        START >> step >> END
    return g


# ============================================================
# Case runners — build graph, run engine with sink, flush
# ============================================================


async def case_sync_call(sink: LangfuseSink, trace_id: str) -> None:
    print(f"\n[case_sync_call] trace_id={trace_id}")
    g = _single_op_graph("case_sync_call", op_sync_add, a=PARENT["a"], b=PARENT["b"])
    engine = Operon(g)
    try:
        await engine.run(inputs={"a": 3, "b": 4}, sink=sink, trace_id=trace_id)
    finally:
        sink.flush(trace_id)


async def case_streaming(sink: LangfuseSink, trace_id: str) -> None:
    print(f"\n[case_streaming] trace_id={trace_id}")
    g = _single_op_graph("case_streaming", op_stt_stream, audio_id=PARENT["audio_id"])
    engine = Operon(g)
    try:
        await engine.run(inputs={"audio_id": "clip-42"}, sink=sink, trace_id=trace_id)
    finally:
        sink.flush(trace_id)


async def case_many_inputs(sink: LangfuseSink, trace_id: str) -> None:
    print(f"\n[case_many_inputs] trace_id={trace_id}")
    g = _single_op_graph("case_many_inputs", op_many_io, nothing=PARENT["seed"])
    engine = Operon(g)
    try:
        await engine.run(inputs={"seed": 0}, sink=sink, trace_id=trace_id)
    finally:
        sink.flush(trace_id)


async def case_path_grouping(sink: LangfuseSink, trace_id: str) -> None:
    """Multi-op graph — 5 ops in 3 folders (speech/, state/, tts)."""
    print(f"\n[case_path_grouping] trace_id={trace_id}")
    with GraphOp(name="case_path_grouping") as g:
        n = op_normalize(text=PARENT["text"])
        s = op_stt(audio_id=PARENT["audio_id"])
        m = op_merge(a=PARENT["a"], b=PARENT["b"])
        st = op_state_transition(fro=PARENT["state"], evt=PARENT["evt"])
        t = op_tts(text=PARENT["reply"])
        START >> [n, s, m, st, t] >> END
    engine = Operon(g)
    try:
        await engine.run(
            inputs={
                "text": "  Xin Chào  ",
                "audio_id": "clip-1",
                "a": 1,
                "b": 2,
                "state": "IDLE",
                "evt": "confirm",
                "reply": "Cảm ơn bạn nhé",
            },
            sink=sink,
            trace_id=trace_id,
        )
    finally:
        sink.flush(trace_id)


async def case_nested_iterations(sink: LangfuseSink, trace_id: str) -> None:
    print(f"\n[case_nested_iterations] trace_id={trace_id}")
    g = _single_op_graph("case_nested_iterations", op_loop_iterations, items=PARENT["items"])
    engine = Operon(g)
    try:
        await engine.run(
            inputs={"items": ["alpha", "bravo", "charlie", "delta"]},
            sink=sink,
            trace_id=trace_id,
        )
    finally:
        sink.flush(trace_id)


async def case_error(sink: LangfuseSink, trace_id: str) -> None:
    print(f"\n[case_error] trace_id={trace_id}")
    g = _single_op_graph("case_error", op_risky, x=PARENT["x"])
    engine = Operon(g)
    try:
        await engine.run(inputs={"x": "not-a-number"}, sink=sink, trace_id=trace_id)
    finally:
        sink.flush(trace_id)


async def case_log_annotation(sink: LangfuseSink, trace_id: str) -> None:
    print(f"\n[case_log_annotation] trace_id={trace_id}")
    g = _single_op_graph("case_log_annotation", op_llm_generate, prompt=PARENT["prompt"])
    engine = Operon(g)
    try:
        await engine.run(inputs={"prompt": "hi"}, sink=sink, trace_id=trace_id)
    finally:
        sink.flush(trace_id)


async def case_media_upload(sink: LangfuseSink, trace_id: str) -> None:
    print(f"\n[case_media_upload] trace_id={trace_id}")
    g = _single_op_graph("case_media_upload", op_media, marker=PARENT["marker"])
    engine = Operon(g)
    try:
        await engine.run(inputs={"marker": "some info"}, sink=sink, trace_id=trace_id)
    finally:
        sink.flush(trace_id)


async def case_mixed_kinds(sink: LangfuseSink, trace_id: str) -> None:
    print(f"\n[case_mixed_kinds] trace_id={trace_id}")
    g = _single_op_graph("case_mixed_kinds", op_agent_orchestrate, task=PARENT["task"])
    engine = Operon(g)
    try:
        await engine.run(inputs={"task": "book meeting"}, sink=sink, trace_id=trace_id)
    finally:
        sink.flush(trace_id)


async def case_ctx_streaming(sink: LangfuseSink, trace_id: str) -> None:
    print(f"\n[case_ctx_streaming] trace_id={trace_id}")
    g = _single_op_graph(
        "case_ctx_streaming",
        op_two_streams,
        audio_a=PARENT["a"],
        audio_b=PARENT["b"],
    )
    engine = Operon(g)
    try:
        await engine.run(inputs={"a": "A", "b": "B"}, sink=sink, trace_id=trace_id)
    finally:
        sink.flush(trace_id)


# ============================================================
# Runner
# ============================================================


CASES = [
    ("sync_call", case_sync_call),
    ("streaming", case_streaming),
    ("many_inputs", case_many_inputs),
    ("path_grouping", case_path_grouping),
    ("nested_iterations", case_nested_iterations),
    ("error", case_error),
    ("log_annotation", case_log_annotation),
    ("media_upload", case_media_upload),
    ("mixed_kinds", case_mixed_kinds),
    ("ctx_streaming", case_ctx_streaming),
]


async def _main_async() -> int:
    client = _build_client()
    print(f"Langfuse Edupia host: {client.config.host}")
    if not client.auth_check():
        print("ERROR: auth check failed", file=sys.stderr)
        return 1
    print("Auth OK. Building and running graphs for every case...")

    sink = LangfuseSink(client=client, workflow_name="tracing_v2_mock")

    urls = []
    for name, fn in CASES:
        trace_id = f"tracing-v2-{name}-{int(time.time())}"
        await fn(sink, trace_id)
        urls.append((name, trace_id, client.trace_url(trace_id)))

    print("\n" + "=" * 60)
    print("Traces sent. Review in Langfuse UI:")
    print("=" * 60)
    for name, trace_id, url in urls:
        print(f"  [{name:<20}] {url}")
    print()
    return 0


def main() -> int:
    return asyncio.run(_main_async())


if __name__ == "__main__":
    raise SystemExit(main())
