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
    engine = Hush(graph, tracer=tracer)
    result = await engine.run(inputs={...})
    ```

    ```python
    # Prompt management (requires langfuse SDK)
    from hush.telemetry import LangfusePromptManager, LangfuseConfig

    pm = LangfusePromptManager(config=LangfuseConfig.from_env())
    prompt = pm["my-prompt"]
    ```
"""

# Auto-register backends to ResourceHub on import
import hush.telemetry.plugin  # noqa: F401 — auto-registers on import
from hush.core.tracing import Tracer

# Backends (configs + clients)
from hush.telemetry.backends import (
    LangfuseClient,
    LangfuseConfig,
    LangfusePromptManager,
    OTELClient,
    OTELConfig,
)

# Tracers
from hush.telemetry.tracers import (
    HushEyesTracer,
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
    "LangfusePromptManager",
    "OTELClient",
    # Tracers
    "HushEyesTracer",
    "LangfuseTracer",
    "OTELTracer",
    # Base class (from hush.core.tracing)
    "Tracer",
]
