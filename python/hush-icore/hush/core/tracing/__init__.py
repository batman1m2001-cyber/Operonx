"""Tracing system — collector separated from ops.

Usage:
    from hush.core.tracing import LocalTracer

    engine = Hush(graph, tracer=LocalTracer())
    result = await engine.run({"x": 5})

For external tracers (HushEyesTracer, LangfuseTracer, OTELTracer),
see hush-telemetry package.
"""

from hush.core.tracing.base import Tracer
from hush.core.tracing.collector import TraceCollector
from hush.core.tracing.flush_worker import FlushWorker, get_flush_worker
from hush.core.tracing.labels import label
from hush.core.tracing.local import LocalTracer
from hush.core.tracing.models import (
    TraceNode,
    TraceSummary,
)
from hush.core.tracing.trace_filter import TraceFilter

__all__ = [
    "Tracer",
    "TraceCollector",
    "TraceFilter",
    "FlushWorker",
    "get_flush_worker",
    "label",
    "LocalTracer",
    "TraceNode",
    "TraceSummary",
]
