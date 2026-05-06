"""Tracing system — event-stream pipeline.

Default usage::

    from operonx.core.tracing import TracePipeline
    from operonx.core.tracing.exporters import JsonFileExporter

    pipeline = TracePipeline(exporters=[JsonFileExporter()])
    engine = Operon(graph, tracer=pipeline)

For Langfuse + other heavier exporters see ``operonx.telemetry.exporters``.

See ``docs/TRACING_REDESIGN_PLAN.md``.
"""

from operonx.core.tracing.emitter import (
    EventEmitter,
    NullEmitter,
    current_emitter,
)
from operonx.core.tracing.events import EventKind, TraceEvent
from operonx.core.tracing.pipeline import (
    AtScheduledExit,
    Exporter,
    FlushOnGroupEnd,
    FlushOnSize,
    FlushStrategy,
    Processor,
    TracePipeline,
)
from operonx.core.tracing.trace_filter import TraceFilter

__all__ = [
    # Core types
    "TraceEvent",
    "EventKind",
    # Emitter
    "EventEmitter",
    "NullEmitter",
    "current_emitter",
    # Pipeline
    "TracePipeline",
    "Processor",
    "Exporter",
    "FlushStrategy",
    "AtScheduledExit",
    "FlushOnGroupEnd",
    "FlushOnSize",
    # Backwards-compat for existing TraceFilter consumers; use processors
    # directly in new code (operonx.core.tracing.processors).
    "TraceFilter",
]
