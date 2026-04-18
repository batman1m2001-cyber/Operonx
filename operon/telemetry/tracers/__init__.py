"""Tracers for various observability backends.

Each tracer extends operon.core.tracing.Tracer. Flush runs in
FlushWorker's thread pool, never blocking the main async thread.

Available tracers:
- OperonEyesTracer: HTTP POST to ui-operon-eyes local server (zero external deps)
- LangfuseTracer: Langfuse observability platform
- OTELTracer: OpenTelemetry (vendor-neutral, exports to Jaeger/Zipkin/Datadog/etc.)
"""

from operon.telemetry.tracers.operon_eyes import OperonEyesTracer
from operon.telemetry.tracers.langfuse import LangfuseTracer
from operon.telemetry.tracers.otel import OTELTracer

__all__ = [
    "OperonEyesTracer",
    "LangfuseTracer",
    "OTELTracer",
]
