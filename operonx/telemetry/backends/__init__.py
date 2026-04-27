"""Observability backends for various providers.

Each backend provides:
- Config class (YamlModel): For ResourceHub registration
- Client class: For interacting with the backend

Available backends:
- langfuse: Langfuse observability platform
- otel: OpenTelemetry (vendor-neutral, exports to Jaeger/Zipkin/Datadog/etc.)
"""

from operonx.telemetry.backends.langfuse import LangfuseClient, LangfuseConfig, LangfusePromptManager
from operonx.telemetry.backends.otel import OTELClient, OTELConfig

__all__ = [
    # Langfuse
    "LangfuseConfig",
    "LangfuseClient",
    "LangfusePromptManager",
    # OpenTelemetry
    "OTELConfig",
    "OTELClient",
]
