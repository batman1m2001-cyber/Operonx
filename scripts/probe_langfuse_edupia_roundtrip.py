"""Probe: end-to-end round-trip against langfuse:edupia.

Runs the same small workflow through THREE trace paths and asserts each
lands correctly on the real backend:

  1. **legacy** — ``LangfuseTracer`` + ``TraceCollector`` + ``flush_worker``
     (the path educa runs in prod today)
  2. **new**    — ``TracePipeline`` + ``LangfuseTreeExporter``
     (the T2.2 replacement, hand-wired)
  3. **shim**   — ``LangfuseTracer.as_pipeline(...)`` + a real TraceFilter
     (the educa migration path: keep your existing TraceFilter, switch to
     the new pipeline with one line of code change)
  4. **media**  — ``TracePipeline`` + ``LangfuseTreeExporter``, with an op
     returning a ``Media`` value, so the upload + token substitution
     path through ``client.upload_media`` round-trips end-to-end (§3.8)

All paths should produce traces with the same observable structure. If any
diverges, this probe says so before educa flips the switch.

Usage:

    uv run python scripts/probe_langfuse_edupia_roundtrip.py            # all four
    uv run python scripts/probe_langfuse_edupia_roundtrip.py --path=legacy
    uv run python scripts/probe_langfuse_edupia_roundtrip.py --path=new
    uv run python scripts/probe_langfuse_edupia_roundtrip.py --path=shim
    uv run python scripts/probe_langfuse_edupia_roundtrip.py --path=media

Exit 0 = all selected paths round-trip OK. Exit 1 = something wrong.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import time
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

import operonx  # noqa: E402
from operonx.core import END, PARENT, START, GraphOp, Media, Operon, op  # noqa: E402
from operonx.core.tracing.pipeline import TracePipeline  # noqa: E402
from operonx.telemetry import LangfuseTracer  # noqa: E402
from operonx.telemetry.exporters import LangfuseTreeExporter  # noqa: E402


# =============================================================================
# Output
# =============================================================================


def _hr(label: str) -> None:
    print(f"\n── {label} ─────────────────────────────────────")


def _fail(msg: str) -> None:
    print(f"❌ FAIL: {msg}")
    sys.exit(1)


def _pass(msg: str) -> None:
    print(f"✅ {msg}")


# =============================================================================
# Workflow
# =============================================================================


@op
def add_one(x: int):
    return {"r": x + 1}


@op
def multiply(r: int):
    return {"product": r * 3}


@op
def emit_three(_signal):
    yield {"v": 1}
    yield {"v": 2}
    yield {"v": 3}


def build_graph() -> GraphOp:
    """Build the same graph used by both paths."""
    with GraphOp(name="probe") as g:
        a = add_one(x=PARENT["x"])
        m = multiply(r=a["r"])
        gen = emit_three(_signal=PARENT["x"])
        START >> a >> m >> END
        START >> gen >> END
        m["product"] >> PARENT["product"]
    return g


# =============================================================================
# Path runners
# =============================================================================


async def run_legacy_path() -> tuple[str, "object"]:
    """Run via the legacy ``LangfuseTracer(resource=...)`` constructor.

    Post-T2.13 this is no longer a separate code path — ``LangfuseTracer``
    is a ``TracePipeline`` subclass — but the constructor surface is the
    one educa uses today, so we keep this probe arm as a regression check
    on backwards compatibility.
    """
    tracer = LangfuseTracer(resource="langfuse:edupia",
                            tags=["probe", "roundtrip", "legacy"])
    engine = Operon(build_graph(), tracer=tracer)
    request_id = f"probe-legacy-{uuid.uuid4().hex[:10]}"
    print(f"  request_id: {request_id}")

    result = await engine.run(
        inputs={"x": 5}, request_id=request_id,
        user_id="probe-user", session_id="probe-session",
    )
    if result.get("product") != 18:
        _fail(f"workflow returned wrong result: {result}")

    _pass("pipeline final flush completed (awaited inline)")
    return request_id, tracer._get_client()


async def run_new_path() -> tuple[str, "object"]:
    """Run the workflow via the new TracePipeline + LangfuseTreeExporter path."""
    exporter = LangfuseTreeExporter(
        resource="langfuse:edupia",
        tags=["probe", "roundtrip", "new"],
        workflow_name="probe",
    )
    pipeline = TracePipeline(exporters=[exporter])
    engine = Operon(build_graph(), tracer=pipeline)
    request_id = f"probe-new-{uuid.uuid4().hex[:10]}"
    print(f"  request_id: {request_id}")

    result = await engine.run(
        inputs={"x": 5}, request_id=request_id,
        user_id="probe-user", session_id="probe-session",
    )
    if result.get("product") != 18:
        _fail(f"workflow returned wrong result: {result}")

    _pass("pipeline final flush completed (awaited inline)")
    return request_id, exporter._get_client()


@op
def emit_media():
    """Producer that returns a ``Media`` value — exercises the upload path."""
    # Tiny WAV header (silence) — small enough to be an obvious test artifact
    # but a valid audio/wav blob in the Langfuse UI.
    payload = (
        b"RIFF$\x00\x00\x00WAVEfmt \x10\x00\x00\x00\x01\x00\x01\x00"
        b"@\x1f\x00\x00\x80>\x00\x00\x02\x00\x10\x00data\x00\x00\x00\x00"
    )
    return {"audio": Media(data=payload, mime_type="audio/wav")}


def build_media_graph() -> GraphOp:
    """Tiny graph: produce Media → forward to PARENT['audio_token']."""
    with GraphOp(name="probe_media") as g:
        m = emit_media()
        START >> m >> END
        m["audio"] >> PARENT["audio_token"]
    return g


async def run_media_path() -> tuple[str, "object"]:
    """Run a Media-producing graph end-to-end and verify the trace's
    observation carries a Langfuse media token, not the raw bytes (§3.8)."""
    exporter = LangfuseTreeExporter(
        resource="langfuse:edupia",
        tags=["probe", "roundtrip", "media"],
        workflow_name="probe_media",
    )
    pipeline = TracePipeline(exporters=[exporter])
    engine = Operon(build_media_graph(), tracer=pipeline)
    request_id = f"probe-media-{uuid.uuid4().hex[:10]}"
    print(f"  request_id: {request_id}")

    result = await engine.run(
        inputs={}, request_id=request_id,
        user_id="probe-user", session_id="probe-session",
    )
    # PARENT output bindings preserve the raw state value — Media wrapper
    # included. Auto-unwrap only happens for consumer-op input binding.
    audio = result.get("audio_token")
    if isinstance(audio, Media):
        size = len(audio.data) if isinstance(audio.data, (bytes, bytearray)) else 0
    elif isinstance(audio, (bytes, bytearray)):
        size = len(audio)
    else:
        size = 0
    if size == 0:
        _fail(f"workflow returned no audio bytes: {type(audio).__name__}")

    _pass("pipeline final flush completed (awaited inline)")
    return request_id, exporter._get_client()


async def run_shim_path() -> tuple[str, "object"]:
    """Run the workflow via ``LangfuseTracer.as_pipeline(...)`` — the migration
    helper path. Uses a real TraceFilter to exercise the adapter."""
    from operonx.core.tracing.trace_filter import TraceFilter

    # Real-shaped TraceFilter — mimics what educa has today
    tf = TraceFilter(
        skip_empty=True,
        max_io_size=2000,
        exclude_ops=["nonexistent_op"],  # adapter passes through unmatched
    )
    pipeline = LangfuseTracer.as_pipeline(
        resource="langfuse:edupia",
        tags=["probe", "roundtrip", "shim"],
        trace_filter=tf,
    )
    engine = Operon(build_graph(), tracer=pipeline)
    request_id = f"probe-shim-{uuid.uuid4().hex[:10]}"
    print(f"  request_id: {request_id}")

    result = await engine.run(
        inputs={"x": 5}, request_id=request_id,
        user_id="probe-user", session_id="probe-session",
    )
    if result.get("product") != 18:
        _fail(f"workflow returned wrong result: {result}")

    _pass("pipeline final flush completed (awaited inline)")
    return request_id, pipeline.exporters[0]._get_client()


# =============================================================================
# Verification — same checks, both paths
# =============================================================================


def fetch_with_retries(client, trace_id: str,
                        max_seconds: float = 30.0,
                        interval_s: float = 1.0) -> dict | None:
    """Poll until the trace appears (Langfuse ingestion is async server-side)."""
    deadline = time.time() + max_seconds
    while time.time() < deadline:
        trace = client.fetch_trace(trace_id, timeout=10)
        if trace is not None:
            return trace
        time.sleep(interval_s)
    return None


def check_structure(trace: dict, request_id: str, path_label: str) -> None:
    """Assert structure matches what the workflow should have produced.

    Same checks for both paths — divergence between legacy and new is what
    this probe is designed to catch.
    """
    if trace.get("id") != request_id:
        _fail(f"[{path_label}] trace.id mismatch: {trace.get('id')!r} vs {request_id!r}")
    _pass(f"[{path_label}] trace.id matches: {request_id}")

    name = trace.get("name")
    if name != "probe":
        _fail(f"[{path_label}] trace.name expected 'probe', got {name!r}")
    _pass(f"[{path_label}] trace.name = {name!r}")

    if trace.get("userId") != "probe-user":
        _fail(f"[{path_label}] userId expected 'probe-user', got {trace.get('userId')!r}")
    _pass(f"[{path_label}] userId OK")

    if trace.get("sessionId") != "probe-session":
        _fail(f"[{path_label}] sessionId expected 'probe-session', got {trace.get('sessionId')!r}")
    _pass(f"[{path_label}] sessionId OK")

    expected_tags = {"probe", "roundtrip"}
    actual_tags = set(trace.get("tags") or [])
    if not expected_tags.issubset(actual_tags):
        missing = expected_tags - actual_tags
        _fail(f"[{path_label}] tags missing {missing} (got {actual_tags})")
    _pass(f"[{path_label}] tags OK ({actual_tags})")

    observations = trace.get("observations") or []
    if not observations:
        _fail(f"[{path_label}] no observations")
    _pass(f"[{path_label}] {len(observations)} observations")

    # Same span set both paths — short var names a, m, gen
    obs_names = {o.get("name") for o in observations}
    expected = {"a", "m", "gen"}
    missing = expected - obs_names
    if missing:
        _fail(f"[{path_label}] missing op spans {missing} (got {obs_names})")
    _pass(f"[{path_label}] all op spans present: {expected}")


def verify_path(label: str, request_id: str, client) -> None:
    """Common verification — fetch + check structure."""
    _hr(f"Fetch trace via public API ({label})")
    trace = fetch_with_retries(client, request_id, max_seconds=30.0)
    if trace is None:
        _fail(f"[{label}] trace {request_id} not found within 30s")
    _pass(f"[{label}] trace fetched")

    _hr(f"Check structure ({label})")
    check_structure(trace, request_id, label)
    print(f"   View: {client.trace_url(request_id)}")


def check_media_structure(trace: dict, request_id: str) -> None:
    """Media-path verification: observation's output carries a Langfuse media
    token (``@@@langfuseMedia:...@@@``), not the raw bytes."""
    if trace.get("id") != request_id:
        _fail(f"[media] trace.id mismatch: {trace.get('id')!r} vs {request_id!r}")
    _pass(f"[media] trace.id matches: {request_id}")

    name = trace.get("name")
    if name != "probe_media":
        _fail(f"[media] trace.name expected 'probe_media', got {name!r}")
    _pass(f"[media] trace.name = {name!r}")

    observations = trace.get("observations") or []
    if not observations:
        _fail("[media] no observations")
    _pass(f"[media] {len(observations)} observations")

    # The single op observation should have an output dict whose `audio` key
    # is a Langfuse media token, not raw bytes / a placeholder.
    matches = [
        o for o in observations
        if (o.get("output") or {}).get("audio") is not None
    ]
    if not matches:
        _fail("[media] no observation with output.audio")
    audio_val = matches[0]["output"]["audio"]
    if not (isinstance(audio_val, str)
            and audio_val.startswith("@@@langfuseMedia:")
            and audio_val.endswith("@@@")):
        _fail(f"[media] output.audio is not a Langfuse media token: {audio_val!r}")
    _pass(f"[media] output.audio is a Langfuse media token: {audio_val[:60]}...")


def verify_media_path(request_id: str, client) -> None:
    _hr("Fetch trace via public API (media)")
    trace = fetch_with_retries(client, request_id, max_seconds=30.0)
    if trace is None:
        _fail(f"[media] trace {request_id} not found within 30s")
    _pass("[media] trace fetched")

    _hr("Check media structure (media)")
    check_media_structure(trace, request_id)
    print(f"   View: {client.trace_url(request_id)}")


# =============================================================================
# Main
# =============================================================================


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--path", choices=["legacy", "new", "shim", "media", "all"], default="all",
        help="Which trace path to probe (default: all)",
    )
    args = parser.parse_args()

    _hr("Probe: langfuse:edupia round-trip")
    if not os.getenv("LANGFUSE_EDUPIA_PUBLIC_KEY"):
        _fail("LANGFUSE_EDUPIA_PUBLIC_KEY not set — check operonx/.env")
    operonx.bootstrap(resources=str(ROOT / "resources.yaml"))

    selected = {args.path} if args.path != "all" else {"legacy", "new", "shim", "media"}
    paths_in_order = [p for p in ("legacy", "new", "shim", "media") if p in selected]

    for path in paths_in_order:
        if path == "legacy":
            _hr("Run via LEGACY path (LangfuseTracer + flush_worker)")
            request_id, client = asyncio.run(run_legacy_path())
            verify_path("legacy", request_id, client)
        elif path == "new":
            _hr("Run via NEW path (TracePipeline + LangfuseTreeExporter)")
            request_id, client = asyncio.run(run_new_path())
            verify_path("new", request_id, client)
        elif path == "shim":
            _hr("Run via SHIM path (LangfuseTracer.as_pipeline + TraceFilter)")
            request_id, client = asyncio.run(run_shim_path())
            verify_path("shim", request_id, client)
        elif path == "media":
            _hr("Run via MEDIA path (Media output → Langfuse upload + token)")
            request_id, client = asyncio.run(run_media_path())
            verify_media_path(request_id, client)

    _hr("Result")
    print(f"✅ Round-trip OK for: {', '.join(paths_in_order)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
