"""
Hush Observability Package

Backend-agnostic observability with support for multiple tracing frameworks.

This package provides:
- Backend clients (LangfuseClient, OTELClient) registered to ResourceHub
- Tracers (LangfuseTracer, OTELTracer) that use ResourceHub to get clients

Example:
    ```python
    from hush.ops import LangfuseTracer, OTELTracer

    # Langfuse tracer
    tracer = LangfuseTracer(resource_key="langfuse:default")

    # OpenTelemetry tracer (exports to Jaeger, Zipkin, etc.)
    tracer = OTELTracer(resource_key="otel:jaeger")

    # Use with workflow
    workflow = MyWorkflow(tracer=tracer)
    await workflow.run(inputs={...})
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
# Re-export core utilities for convenience
from hush.core.tracers import (
    MEDIA_KEY,
    BaseTracer,
    MediaAttachment,
    get_registered_tracers,
    register_tracer,
    serialize_media_attachments,
)

# Backends (configs + clients)
from hush.ops.backends import (
    LangfuseClient,
    LangfuseConfig,
    OTELClient,
    OTELConfig,
)
from hush.ops.plugin import ObservabilityPlugin  # noqa: F401

# Tracers
from hush.ops.tracers import (
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
    # Core utilities (re-exported)
    "BaseTracer",
    "register_tracer",
    "get_registered_tracers",
    "MEDIA_KEY",
    "MediaAttachment",
    "serialize_media_attachments",
]
