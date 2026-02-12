"""Observability backends for various providers.

Each backend provides:
- Config class (YamlModel): For ResourceHub registration
- Client class: For interacting with the backend

Available backends:
- langfuse: Langfuse observability platform
- otel: OpenTelemetry (vendor-neutral, exports to Jaeger/Zipkin/Datadog/etc.)
"""

from hush.ops.backends.langfuse import LangfuseClient, LangfuseConfig
from hush.ops.backends.otel import OTELClient, OTELConfig

__all__ = [
    # Langfuse
    "LangfuseConfig",
    "LangfuseClient",
    # OpenTelemetry
    "OTELConfig",
    "OTELClient",
]
