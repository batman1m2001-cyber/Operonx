"""Observability backends for various providers.

Each backend provides:
- Config class (YamlModel): For ResourceHub registration
- Client class: For interacting with the backend

Available backends:
- langfuse: Langfuse observability platform
- otel: OpenTelemetry (vendor-neutral, exports to Jaeger/Zipkin/Datadog/etc.)
"""

from hush.observability.backends.langfuse import LangfuseConfig, LangfuseClient
from hush.observability.backends.otel import OTELConfig, OTELClient

__all__ = [
    # Langfuse
    "LangfuseConfig",
    "LangfuseClient",
    # OpenTelemetry
    "OTELConfig",
    "OTELClient",
]
