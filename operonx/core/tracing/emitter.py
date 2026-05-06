"""EventEmitter — what ops + scheduler call to record trace events.

All methods are sync + O(1) per the execution model in
``docs/TRACING_REDESIGN_PLAN.md`` §3.7. They append to the bound pipeline's
buffer and (for some kinds) bookkeep an in-flight start_time map for
``OP_END(status="cancelled")`` synthesis (Rule 3).

The emitter is bound per ``engine.start()`` call. ``NullEmitter`` is bound
when no tracer is configured — its methods are no-ops.

Two ContextVars carry the per-call binding through ``asyncio.create_task``:

- ``_current_emitter_var``: the active emitter (set once at engine.start)
- ``_current_op_var``: the ``(op_name, ctx)`` of the op currently executing,
  set/cleared by ``_pump`` around each invocation

Used together to scope ``annotate(key, value)`` to the current op without
threading inputs.
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
from time import perf_counter
from typing import TYPE_CHECKING, Any, Dict, Iterator, Optional, Tuple

from operonx.core.tracing.events import EventKind, TraceEvent

if TYPE_CHECKING:
    from operonx.core.tracing.pipeline import TracePipeline


_current_emitter_var: ContextVar["EventEmitter"] = ContextVar(
    "operonx_current_emitter"
)
"""Per-call binding of the active emitter. Set by ``engine.start()``."""

_current_op_var: ContextVar[Tuple[str, Tuple[str, ...]]] = ContextVar(
    "operonx_current_op"
)
"""``(op_name, ctx)`` of the op currently executing. Set/cleared by ``_pump``
around each op invocation."""


def current_emitter() -> "EventEmitter":
    """Return the emitter bound to this call, or ``NullEmitter`` if none.

    Use from inside an op when you need to call ``annotate`` or emit a
    custom event. Returning a singleton ``NullEmitter`` means callers never
    have to ``if emitter is None`` — invoking methods is always safe.
    """
    try:
        return _current_emitter_var.get()
    except LookupError:
        return _NULL_EMITTER


class EventEmitter:
    """Pushes events into the bound pipeline's buffer.

    Sync, O(1) per method. The execution model (§3.7) forbids any
    processor or exporter work on the emit path — that all happens inside
    ``pipeline.flush()`` which runs as a separate ``asyncio.create_task``.
    """

    def __init__(self, pipeline: "TracePipeline", request_id: str) -> None:
        self._pipeline = pipeline
        self._request_id = request_id
        self._seq = 0
        self._start_times: Dict[Tuple[str, Tuple[str, ...]], float] = {}
        """``(op_name, ctx) → perf_counter()`` at OP_START. Looked up by
        ``start_time_of`` for cancel-emit duration_ms (Rule 3)."""

    # ------------------------------------------------------------------
    # Core emit
    # ------------------------------------------------------------------

    def emit(self, event: TraceEvent) -> None:
        """Append an event to the pipeline buffer.

        Called by helpers below or directly by user code that constructs
        bespoke events (rare).
        """
        self._pipeline._push(event)

    # ------------------------------------------------------------------
    # Convenience helpers — construct + emit the common kinds
    # ------------------------------------------------------------------

    def op_start(
        self,
        op_name: str,
        ctx: Tuple[str, ...],
        inputs: Optional[Dict[str, Any]] = None,
        media_refs: Optional[list] = None,
    ) -> None:
        """Fired in ``_pump`` after ``get_inputs()`` resolves, before
        ``_exec_core()`` enters.

        ``media_refs`` is a parallel ``list[MediaRef]`` produced by
        ``extract_media`` over the trace-time view of inputs. Bytes live on
        the refs (not in event memory beyond what they'd cost in the I/O
        dict anyway); the exporter uploads them at flush and substitutes
        Langfuse media tokens for the ``<media:N>`` placeholders left in
        ``inputs``. Empty list when no Media instances were found.
        """
        self._start_times[(op_name, ctx)] = perf_counter()
        self.emit(self._build(EventKind.OP_START, op_name, ctx, {
            "inputs": inputs or {},
            "media_refs": media_refs or [],
        }))

    def op_end(
        self,
        op_name: str,
        ctx: Tuple[str, ...],
        outputs: Optional[Dict[str, Any]] = None,
        status: str = "ok",
        duration_ms: Optional[float] = None,
        yield_count: int = 0,
        media_refs: Optional[list] = None,
    ) -> None:
        """Fired in ``_pump``'s ``finally`` after ``store_result``.

        ``status`` is one of ``"ok"``, ``"error"``, ``"cancelled"``.
        ``duration_ms`` defaults to elapsed since OP_START. Pops the in-flight
        entry — calling twice for the same op is a no-op the second time
        (idempotent), needed for the cancel-double-emit guard in Rule 3.

        ``media_refs`` mirrors ``op_start``'s parameter, for outputs.
        """
        key = (op_name, ctx)
        if key not in self._start_times:
            # Already ended (cancel-emit raced with finally) — drop second.
            return
        if duration_ms is None:
            duration_ms = (perf_counter() - self._start_times[key]) * 1000.0
        del self._start_times[key]
        self.emit(self._build(EventKind.OP_END, op_name, ctx, {
            "outputs": outputs or {},
            "status": status,
            "duration_ms": duration_ms,
            "yield_count": yield_count,
            "media_refs": media_refs or [],
        }))

    def op_yield(
        self,
        op_name: str,
        ctx: Tuple[str, ...],
        yielded: Dict[str, Any],
        idx: int,
        media_refs: Optional[list] = None,
    ) -> None:
        """Fired per-yield from a generator op, gated by ``emit_yields=N``.

        ``media_refs`` carries any Media stripped out of ``yielded`` so the
        event buffer doesn't hold raw bytes. Currently unused by the default
        Langfuse exporter (which folds yields into the parent observation's
        ``last_yielded`` metadata) — present for forward-compat with custom
        exporters that render per-yield observations.
        """
        self.emit(self._build(EventKind.OP_YIELD, op_name, ctx, {
            "yielded": yielded,
            "idx": idx,
            "media_refs": media_refs or [],
        }))

    def annotate(self, key: str, value: Any) -> None:
        """Attach metadata to the currently executing op.

        Reads ``_current_op_var`` (set by ``_pump``). Raises ``LookupError``
        if called outside an op scope — programming error.
        """
        op_name, ctx = _current_op_var.get()
        self.emit(self._build(EventKind.ANNOTATION, op_name, ctx, {
            "key": key,
            "value": value,
        }))

    def llm_usage(
        self,
        op_name: str,
        ctx: Tuple[str, ...],
        model: str,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        total_tokens: int = 0,
        cost_usd: float = 0.0,
    ) -> None:
        """Token / cost report. MUST be called before the matching OP_END."""
        self.emit(self._build(EventKind.LLM_USAGE, op_name, ctx, {
            "model": model,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": total_tokens,
            "cost_usd": cost_usd,
        }))

    def media_ref(
        self,
        op_name: str,
        ctx: Tuple[str, ...],
        handle: str,
        mime: str,
        size_bytes: int,
    ) -> None:
        """Reference a blob stored separately in the pipeline's MediaStore.
        Bytes are NOT in the event payload — call
        ``pipeline.media_store.put(handle, data, mime)`` first."""
        self.emit(self._build(EventKind.MEDIA_REF, op_name, ctx, {
            "handle": handle,
            "mime": mime,
            "size_bytes": size_bytes,
        }))

    @contextmanager
    def group(self, name: str, **metadata: Any) -> Iterator[None]:
        """Manual logical group via ``with emitter.group("xyz"): ...``.

        Most users won't call this directly — ``GroupBy`` processor emits
        ``GROUP_START``/``GROUP_END`` based on event boundaries in the
        stream. Use this when grouping is structural in user code rather
        than derived from events.
        """
        self.emit(self._build(EventKind.GROUP_START, None, (), {
            "name": name, **metadata,
        }))
        try:
            yield
        finally:
            self.emit(self._build(EventKind.GROUP_END, None, (), {
                "name": name, "status": "ok",
            }))

    # ------------------------------------------------------------------
    # Read-only accessors used by the scheduler for cancel-emit (Rule 3)
    # ------------------------------------------------------------------

    def start_time_of(
        self,
        op_name: str,
        ctx: Tuple[str, ...],
    ) -> float:
        """``perf_counter()`` snapshot from this op's OP_START, or 0.0 if
        unknown (e.g. cancel-before-start, where OP_START never fired)."""
        return self._start_times.get((op_name, ctx), 0.0)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _build(
        self,
        kind: EventKind,
        op_name: Optional[str],
        ctx: Tuple[str, ...],
        payload: Dict[str, Any],
    ) -> TraceEvent:
        """Construct a TraceEvent with this emitter's seq + request_id.

        ``event_id`` is ``"{request_id}-{seq}"`` rather than UUID4 — uniqueness
        within a pipeline is the only requirement, and avoiding ``uuid.uuid4()``
        keeps the emit hot path under budget (§3.7 TR13).
        """
        seq = self._seq
        self._seq = seq + 1
        return TraceEvent(
            event_id=f"{self._request_id}-{seq}",
            request_id=self._request_id,
            kind=kind,
            op_name=op_name,
            ctx=ctx,
            timestamp=datetime.now(timezone.utc),
            seq=seq,
            payload=payload,
        )


class NullEmitter(EventEmitter):
    """Bound when no tracer is configured. All methods are no-ops.

    The fast path: op code calls ``emit_*`` blindly; cost is one method-table
    dispatch (~ns). Subclasses ``EventEmitter`` so type checks against the
    base class still work.
    """

    def __init__(self) -> None:
        # Do NOT call super().__init__ — NullEmitter never needs a pipeline.
        # Skipping it avoids allocating the start_times dict.
        pass

    def emit(self, event: TraceEvent) -> None:
        pass

    def op_start(self, *_: Any, **__: Any) -> None:
        pass

    def op_end(self, *_: Any, **__: Any) -> None:
        pass

    def op_yield(self, *_: Any, **__: Any) -> None:
        pass

    def annotate(self, *_: Any, **__: Any) -> None:
        pass

    def llm_usage(self, *_: Any, **__: Any) -> None:
        pass

    def media_ref(self, *_: Any, **__: Any) -> None:
        pass

    @contextmanager
    def group(self, name: str, **metadata: Any) -> Iterator[None]:
        yield

    def start_time_of(self, *_: Any) -> float:
        return 0.0


_NULL_EMITTER = NullEmitter()
"""Singleton — returned by ``current_emitter()`` when nothing is bound.
Safe to share: NullEmitter has no per-call state."""
