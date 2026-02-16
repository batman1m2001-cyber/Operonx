"""
Hush Observability Package

Backend-agnostic observability with support for multiple tracing frameworks.

This package provides:
- Backend clients (LangfuseClient, OTELClient) registered to ResourceHub
- Tracers (LangfuseTracer, OTELTracer) that extend hush.core.tracing.Tracer

Example:
    ```python
    from hush.telemetry import LangfuseTracer, OTELTracer

    # Langfuse tracer
    tracer = LangfuseTracer(resource="langfuse:default")

    # OpenTelemetry tracer (exports to Jaeger, Zipkin, etc.)
    tracer = OTELTracer(resource="otel:jaeger")

    # Use with workflow engine
    result = await engine.run(inputs={...}, tracer=tracer)
    ```

    ```python
    # Direct client access
    from hush.core.registry import get_hub

    # Langfuse client
    langfuse = get_hub().langfuse("default")
    prompt = langfuse.get_prompt("my-prompt")

    # OpenTelemetry client
    otel = get_hub().otel("jaeger")
    with otel.start_span("my-operation") as span:
        span.set_attribute("key", "value")
    ```
"""

# Auto-register backends to ResourceHub on import
from hush.core.tracing import Tracer

# Backends (configs + clients)
from hush.telemetry.backends import (
    LangfuseClient,
    LangfuseConfig,
    OTELClient,
    OTELConfig,
)
from hush.telemetry.plugin import ObservabilityPlugin  # noqa: F401

# Tracers
from hush.telemetry.tracers import (
    LangfuseTracer,
    OTELTracer,
)

__version__ = "0.1.0"

__all__ = [
    # Backends - Configs
    "LangfuseConfig",
    "OTELConfig",
    # Backends - Clients
    "LangfuseClient",
    "OTELClient",
    # Tracers
    "LangfuseTracer",
    "OTELTracer",
    # Base class (from hush.core.tracing)
    "Tracer",
]
