"""New tracing system — collector separated from ops.

Usage:
    from hush.core.tracing import HushEyesTracer

    engine = Hush(graph)
    result = await engine.run({"x": 5}, tracer=HushEyesTracer())
"""

from hush.core.tracing.base import Tracer
from hush.core.tracing.collector import TraceCollector
from hush.core.tracing.flush_worker import FlushWorker, get_flush_worker
from hush.core.tracing.hush_eyes import HushEyesTracer
from hush.core.tracing.local import LocalTracer
from hush.core.tracing.models import NodeStructure, TracePayload, TraceRecord, TraceSummary

__all__ = [
    "Tracer",
    "TraceCollector",
    "FlushWorker",
    "get_flush_worker",
    "HushEyesTracer",
    "LocalTracer",
    "NodeStructure",
    "TracePayload",
    "TraceRecord",
    "TraceSummary",
]
