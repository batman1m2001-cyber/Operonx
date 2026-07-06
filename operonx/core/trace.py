"""Trace event stream — Langfuse-shaped ergonomics, sink renders.

Two verbs mirror the two Langfuse observation types:

    from operonx import event, span

    @op
    def add(a, b):
        c = a + b
        span("math/add", input={"a": a, "b": b}, output={"result": c})
        return {"result": c}

    @op
    async def stt(audio):
        event("speech/stt", {"audio_id": id}, kind="input")
        async for chunk in audio:
            partial = recognize(chunk)
            event("speech/stt", {"partial": partial}, kind="output")

- ``span(path, input=..., output=...)`` — paired atomic call, renders as a
  Langfuse **SPAN** (has ``input`` + ``output`` + duration).
- ``event(path, data, kind=...)`` — single point on the timeline, renders
  as a Langfuse **EVENT** (has ``input`` OR ``output`` OR ``metadata``
  depending on ``kind``).

The engine sets a ContextVar around ``Operon.run()`` carrying
(trace_id, sink). Ops call ``event()`` or ``span()`` in their bodies.
Absent sink → both are cheap no-ops. Explicit opt-in tracing, zero cost
off. See ``docs/TRACING_V2_PLAN.md`` for the full design.
"""

from __future__ import annotations

import time
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Tuple

__all__ = [
    "TraceEvent",
    "Sink",
    "TraceRecorder",
    "event",
    "span",
    "set_sink",
    "clear_sink",
]

# Fixed vocabulary — engine + sink agree on these strings.
KIND_INPUT = "input"
KIND_OUTPUT = "output"
KIND_LOG = "log"
KIND_ERROR = "error"
# Paired atomic call — sink renders as a Langfuse SPAN with input+output.
KIND_CALL = "call"


@dataclass
class TraceEvent:
    """One point on the trace stream.

    Attributes:
        trace_id: Correlation key across a run (e.g. call_id).
        path:     Trace-tree location — e.g. ``"speech/stt"``.
        kind:     One of ``"input"``, ``"output"``, ``"log"``, ``"error"``,
                  ``"call"``. See :func:`event` and :func:`span`.
        time:     ``time.perf_counter()`` timestamp at emit.
        data:     Payload dict — for ``"call"``, ``{"inputs":..., "outputs":...}``;
                  for other kinds, the plain user-supplied dict.
        ctx:      Optional discriminator for invocations of the same
                  path. Different ctx → different span in the sink. Use
                  for loop iterations, streaming sub-contexts, retries.
                  ``None`` means the default (single) context.
    """

    trace_id: str
    path: str
    kind: str
    time: float
    data: Dict[str, Any]
    ctx: Optional[str] = None


Sink = Callable[[TraceEvent], None]

# Per-execution context — set by Operon.run(), read by event() / span().
# None means "no sink installed" → both are no-ops.
_current: ContextVar[Optional[Tuple[str, Sink]]] = ContextVar("operonx_trace", default=None)

# Per-op-invocation ctx — set by BaseOp.run() to the scheduler's
# ``context_id`` tuple (e.g. ``("main",)``, ``("main", "[0]")`` for a
# streaming sub-context). Read by event() / span() to auto-inject a
# per-invocation ctx on every emitted TraceEvent. Author never touches
# this — the sink uses it to distinguish spans across invocations of
# the same op path.
_current_op_ctx: ContextVar[Optional[tuple]] = ContextVar("operonx_op_ctx", default=None)


def _format_runtime_ctx(runtime_ctx: Optional[tuple]) -> Optional[str]:
    """Convert the scheduler's ctx tuple into a compact display string.

    ``("main",)`` or ``None`` → ``None`` (root / single context).
    ``("main", "[0]")`` → ``"0"`` (streaming yield sub-context).
    ``("main", "iter_1")`` → ``"iter_1"``.
    ``("main", "[0]", "[3]")`` → ``"0.3"`` (nested).

    Kept in the engine layer so the sink sees a simple string.
    """
    if runtime_ctx is None or runtime_ctx == ("main",):
        return None
    parts = [p.strip("[]") for p in runtime_ctx[1:]]
    parts = [p for p in parts if p]
    return ".".join(parts) if parts else None


def event(path: str, data: Dict[str, Any], kind: str = KIND_LOG) -> None:
    """Emit a single timeline event — renders as a Langfuse EVENT.

    Cheap no-op when no sink is installed. Sink exceptions are swallowed
    and logged — tracing must never break op execution.

    Use for streaming / async / M-to-N patterns where input and output
    don't happen at the same moment. For paired atomic calls (sync ops
    where input and output are known together), use :func:`span` which
    renders as a proper span with both fields.

    ``ctx`` on the TraceEvent is auto-populated from the engine's
    per-op-invocation ContextVar — different op invocations (loop
    iterations, streaming sub-contexts, retries) automatically produce
    different Langfuse spans in the sink.

    Args:
        path: Trace-tree location; ``"/"``-separated segments become
              nested container spans (e.g. ``"speech/stt"``).
        data: Payload dict; sink renders as Langfuse event body.
        kind: One of ``"input"``, ``"output"``, ``"log"``, ``"error"``.
              Defaults to ``"log"``. Each kind maps to a distinct
              Langfuse event field (input/output/metadata) in the sink.
    """
    ctx_var = _current.get()
    if ctx_var is None:
        return
    trace_id, sink = ctx_var
    runtime_ctx = _format_runtime_ctx(_current_op_ctx.get())
    try:
        sink(TraceEvent(trace_id, path, kind, time.perf_counter(), data, runtime_ctx))
    except Exception:  # noqa: BLE001 — best-effort: never break the run
        import logging

        logging.getLogger("operonx.tracing").exception("sink raised on event(%r)", path)


def span(
    path: str,
    *,
    input: Optional[Dict[str, Any]] = None,
    output: Optional[Dict[str, Any]] = None,
) -> None:
    """Emit a paired atomic call — renders as a Langfuse SPAN.

    Use for sync-shaped ops where input and output are known together
    (``a + b → c``, ``format(x) → y``). The sink renders this as a single
    Langfuse span with both ``input`` and ``output`` fields set — not two
    separate events.

    Multiple ``span()`` calls at the same path within one op invocation
    (e.g. inside a for-loop) are auto-indexed by the sink — the first is
    named plainly, subsequent ones get ``[1]``, ``[2]`` … suffixes. No
    author-side counter needed.

    For streaming / async / M-to-N patterns, use :func:`event` with
    ``kind="input"`` / ``kind="output"`` (each becomes a separate
    Langfuse event on the timeline).

    Args:
        path:   Trace-tree location; ``"/"``-separated segments become
                nested container spans (e.g. ``"math/add"``).
        input:  Input dict. Default ``{}``.
        output: Output dict. Default ``{}``.
    """
    ctx_var = _current.get()
    if ctx_var is None:
        return
    trace_id, sink = ctx_var
    runtime_ctx = _format_runtime_ctx(_current_op_ctx.get())
    data = {"inputs": input or {}, "outputs": output or {}}
    try:
        sink(TraceEvent(trace_id, path, KIND_CALL, time.perf_counter(), data, runtime_ctx))
    except Exception:  # noqa: BLE001
        import logging

        logging.getLogger("operonx.tracing").exception("sink raised on span(%r)", path)


def set_sink(trace_id: str, sink: Sink):
    """Install ``sink`` for the current context.

    Returns a token you MUST pass to :func:`clear_sink` to restore the
    previous state. Prefer using ``Operon.run(sink=...)`` which sets and
    resets automatically.
    """
    return _current.set((trace_id, sink))


def clear_sink(token) -> None:
    """Restore the previous sink state (undo :func:`set_sink`)."""
    _current.reset(token)


class TraceRecorder:
    """In-memory sink — accumulates events, exposes them for tests.

    Example::

        recorder = TraceRecorder()
        await engine.run(sink=recorder, trace_id="t1")
        assert recorder.events[0].kind == "input"
    """

    __slots__ = ("events",)

    def __init__(self) -> None:
        self.events: List[TraceEvent] = []

    def __call__(self, ev: TraceEvent) -> None:
        self.events.append(ev)

    def clear(self) -> None:
        self.events.clear()

    def by_path(self, path: str) -> List[TraceEvent]:
        """Return events matching a given path (helper for tests)."""
        return [e for e in self.events if e.path == path]

    def by_kind(self, kind: str) -> List[TraceEvent]:
        """Return events matching a given kind."""
        return [e for e in self.events if e.kind == kind]
