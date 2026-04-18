"""Tracing system — collector separated from ops.

Usage:
    from operon.core.tracing import LocalTracer

    engine = Operon(graph, tracer=LocalTracer())
    result = await engine.run({"x": 5})

For external tracers (OperonEyesTracer, LangfuseTracer, OTELTracer),
see operon-telemetry package.
"""

from operon.core.tracing.base import Tracer
from operon.core.tracing.collector import TraceCollector
from operon.core.tracing.flush_worker import FlushWorker, get_flush_worker
from operon.core.tracing.labels import label
from operon.core.tracing.local import LocalTracer
from operon.core.tracing.models import (
    TraceNode,
    TraceSummary,
)
from operon.core.tracing.trace_filter import TraceFilter

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
