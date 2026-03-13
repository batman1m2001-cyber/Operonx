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
from hush.core.tracing.local import LocalTracer
from hush.core.tracing.models import (
    TraceNode,
    TraceSummary,
)

__all__ = [
    "Tracer",
    "TraceCollector",
    "FlushWorker",
    "get_flush_worker",
    "LocalTracer",
    "TraceNode",
    "TraceSummary",
]
