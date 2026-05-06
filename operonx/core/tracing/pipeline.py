"""TracePipeline — owns the event buffer, runs processors, dispatches to exporters.

Bound per ``engine.start()`` call. The execution model
(``docs/TRACING_REDESIGN_PLAN.md`` §3.7) constrains what runs where:

- ``_push(event)`` is sync, O(1) — called from ``EventEmitter.emit``
- ``flush_strategy.should_flush(event, buffered)`` is checked per-push;
  on True, ``_schedule_flush()`` fires off ``asyncio.create_task(self._run_flush())``
  so the current op continues without waiting
- ``_run_flush`` runs the processor chain on the event loop (pure-Python work,
  GIL-bound — a thread pool gives no benefit)
- Each exporter's ``export()`` runs via ``loop.run_in_executor(...)`` so
  blocking I/O (HTTP POST, file write) doesn't stall the main loop

Phase T1 ships the skeleton: buffer + flush trigger plumbing only. Processor
chain and exporter dispatch are stubbed; T2 fills them in alongside
re-implementations of the existing tracers.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Callable, Dict, Iterable, List, Optional, Protocol

from operonx.core.tracing.emitter import EventEmitter
from operonx.core.tracing.events import EventKind, TraceEvent

LOGGER = logging.getLogger("operonx.tracing")


Processor = Callable[[Iterable[TraceEvent]], Iterable[TraceEvent]]
"""A pure stream transform. See ``docs/TRACING_REDESIGN_PLAN.md`` §3.3."""


class Exporter(Protocol):
    """Renders + sends a processed event list to a backend.
    See ``docs/TRACING_REDESIGN_PLAN.md`` §3.4."""

    def export(
        self,
        events: List[TraceEvent],
        request_id: str,
        metadata: Dict[str, Any],
    ) -> None: ...


class FlushStrategy(Protocol):
    """Decides whether to flush after each emitted event.
    See ``docs/TRACING_REDESIGN_PLAN.md`` §7."""

    def should_flush(
        self,
        event: TraceEvent,
        buffered: int,
    ) -> bool: ...


class AtScheduledExit:
    """Default — flush only when the scheduler task ends (current behavior)."""

    def should_flush(self, event: TraceEvent, buffered: int) -> bool:
        return False


class FlushOnSize:
    """Flush whenever the buffer hits ``max_events``. Bounded memory."""

    def __init__(self, max_events: int) -> None:
        self.max_events = max_events

    def should_flush(self, event: TraceEvent, buffered: int) -> bool:
        return buffered >= self.max_events


class FlushOnGroupEnd:
    """Flush at every ``GROUP_END`` whose name matches ``pattern``.

    Pattern is a glob — ``"turn-*"`` matches ``"turn-0"``, ``"turn-1"``, etc.
    Used by callbot for per-turn live export to Langfuse.
    """

    def __init__(self, group: str) -> None:
        self.pattern = group

    def should_flush(self, event: TraceEvent, buffered: int) -> bool:
        if event.kind is not EventKind.GROUP_END:
            return False
        from fnmatch import fnmatchcase
        name = event.payload.get("name") or ""
        return fnmatchcase(name, self.pattern)


class TracePipeline:
    """Owns the per-call event buffer + processor chain + exporter dispatch.

    Construction is cheap; the heavy work happens in ``flush()``. One
    pipeline per ``engine.start()`` call — concurrent calls have isolated
    buffers and emitters via the ``_current_emitter_var`` ContextVar.

    Phase T1: ``flush()`` is a stub (no processors run yet). Wire-up to
    base.py / task_scheduler.py emit sites lands in the same phase. The
    actual processor + exporter machinery is built in T2.
    """

    def __init__(
        self,
        processors: Optional[List[Processor]] = None,
        exporters: Optional[List[Exporter]] = None,
        flush_strategy: Optional[FlushStrategy] = None,
        max_buffered_events: int = 100_000,
    ) -> None:
        self.processors: List[Processor] = list(processors or ())
        self.exporters: List[Exporter] = list(exporters or ())
        self.flush_strategy: FlushStrategy = flush_strategy or AtScheduledExit()
        self.max_buffered_events = max_buffered_events

        self._buffer: List[TraceEvent] = []
        self._overflow_warned = False
        self._pending_flush: Optional[asyncio.Task] = None
        # Per-call metadata captured at emitter() time. Forwarded to each
        # exporter as the ``metadata`` arg of ``export(...)``. Keys mirror
        # the legacy LangfuseTracer.flush() shape: workflow_name, user_id,
        # session_id, tags. Exporters pick what they need.
        self._metadata: Dict[str, Any] = {}

    # ------------------------------------------------------------------
    # Per-call binding
    # ------------------------------------------------------------------

    def emitter(
        self,
        request_id: str,
        *,
        workflow_name: Optional[str] = None,
        user_id: Optional[str] = None,
        session_id: Optional[str] = None,
        tags: Optional[List[str]] = None,
    ) -> EventEmitter:
        """Construct a fresh emitter bound to this pipeline + request_id.

        Caller (engine.start) is responsible for setting ``_current_emitter_var``
        so ops + scheduler can find it.

        The keyword args are stashed on the pipeline and forwarded to each
        exporter's ``export(events, request_id, metadata)`` call. They mirror
        the legacy ``LangfuseTracer.flush(trace_data)`` shape.
        """
        self._metadata = {
            "workflow_name": workflow_name,
            "user_id": user_id,
            "session_id": session_id,
            "tags": tags,
        }
        return EventEmitter(pipeline=self, request_id=request_id)

    # ------------------------------------------------------------------
    # Internal — emit path
    # ------------------------------------------------------------------

    def _push(self, event: TraceEvent) -> None:
        """Append + check flush trigger. Called by ``EventEmitter.emit``.

        Hot path: must stay O(1). The flush trigger check is a bool
        predicate. On True, fires an ``asyncio.create_task`` so the current
        op continues without waiting.
        """
        self._buffer.append(event)

        # Bounded buffer — overflow → drop oldest + WARN once.
        if len(self._buffer) > self.max_buffered_events:
            drop_count = len(self._buffer) - self.max_buffered_events
            del self._buffer[:drop_count]
            if not self._overflow_warned:
                LOGGER.warning(
                    "trace buffer overflow at request_id=%s — dropped %d oldest "
                    "events (max_buffered_events=%d). Consider FlushOnGroupEnd "
                    "or FlushOnSize for long-running calls.",
                    event.request_id, drop_count, self.max_buffered_events,
                )
                self._overflow_warned = True

        # Flush trigger — non-blocking schedule.
        if self.flush_strategy.should_flush(event, len(self._buffer)):
            self._schedule_flush(partial=True)

    def _schedule_flush(self, partial: bool) -> None:
        """Fire-and-forget flush task. Returns immediately.

        If a previous flush is still pending, skip — the in-flight task
        will pick up everything currently buffered when it gets to drain.
        """
        if self._pending_flush is not None and not self._pending_flush.done():
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            # No loop running — likely a sync test or shutdown path.
            # Fall back to synchronous flush so events aren't lost.
            self._flush_sync(partial=partial)
            return
        self._pending_flush = loop.create_task(self._run_flush(partial=partial))

    # ------------------------------------------------------------------
    # Flush — runs processors + exporter dispatch
    # ------------------------------------------------------------------

    async def flush(self, partial: bool = False) -> None:
        """Drain the buffer through processors + exporters.

        Called explicitly by ``engine.start``'s finally (final flush) or
        indirectly via ``_schedule_flush`` (partial flushes triggered by
        flush_strategy).
        """
        await self._run_flush(partial=partial)

    async def _run_flush(self, partial: bool) -> None:
        """Run processors on the asyncio loop, exporters in a thread pool."""
        if not self._buffer:
            return

        events: List[TraceEvent] = self._buffer
        self._buffer = []

        # Processors run on the event loop — pure-Python CPU work, GIL-bound.
        try:
            stream: Iterable[TraceEvent] = events
            for proc in self.processors:
                stream = proc(stream)
            processed = list(stream)
        except Exception:
            LOGGER.exception("processor chain failed; dropping batch")
            return

        if not processed:
            return

        # Each exporter runs in the thread pool — HTTP / file I/O.
        loop = asyncio.get_running_loop()
        request_id = processed[0].request_id
        metadata: Dict[str, Any] = {**self._metadata, "partial": partial}

        for exporter in self.exporters:
            try:
                await loop.run_in_executor(
                    None, exporter.export, processed, request_id, metadata,
                )
            except Exception:
                LOGGER.exception(
                    "exporter %s failed for request_id=%s",
                    type(exporter).__name__, request_id,
                )

    def _flush_sync(self, partial: bool) -> None:
        """Synchronous fallback when no event loop is running.

        Used at shutdown or in non-async tests. Processors run inline;
        exporters run inline (no executor). Failures logged, not raised.
        """
        if not self._buffer:
            return
        events = self._buffer
        self._buffer = []
        try:
            stream: Iterable[TraceEvent] = events
            for proc in self.processors:
                stream = proc(stream)
            processed = list(stream)
        except Exception:
            LOGGER.exception("processor chain failed (sync); dropping batch")
            return
        if not processed:
            return
        request_id = processed[0].request_id
        metadata = {**self._metadata, "partial": partial}
        for exporter in self.exporters:
            try:
                exporter.export(processed, request_id, metadata)
            except Exception:
                LOGGER.exception(
                    "exporter %s failed (sync) for request_id=%s",
                    type(exporter).__name__, request_id,
                )
