"""Tracing system — collector separated from ops.

Usage:
    from operonx.core.tracing import LocalTracer

    engine = Operon(graph, tracer=LocalTracer())
    result = await engine.run({"x": 5})

For external tracers (OperonEyesTracer, LangfuseTracer, OTELTracer),
see operonx-telemetry package.
"""

from operonx.core.tracing.base import Tracer
from operonx.core.tracing.collector import TraceCollector
from operonx.core.tracing.flush_worker import FlushWorker, get_flush_worker
from operonx.core.tracing.labels import label
from operonx.core.tracing.local import LocalTracer
from operonx.core.tracing.models import (
    TraceNode,
    TraceSummary,
)
from operonx.core.tracing.trace_filter import TraceFilter

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
