"""Tracers for various observability backends.

Each tracer extends hush.core.tracers.BaseTracer and uses
ResourceHub to get the backend client in the subprocess.

Available tracers:
- LangfuseTracer: Langfuse observability platform
- OTELTracer: OpenTelemetry (vendor-neutral, exports to Jaeger/Zipkin/Datadog/etc.)
"""

from hush.ops.tracers.langfuse import LangfuseTracer
from hush.ops.tracers.otel import OTELTracer

__all__ = [
    "LangfuseTracer",
    "OTELTracer",
]
