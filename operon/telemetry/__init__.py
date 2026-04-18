"""
Operon Observability Package

Backend-agnostic observability with support for multiple tracing frameworks.

This package provides:
- Backend clients (LangfuseClient, OTELClient) registered to ResourceHub
- Tracers (LangfuseTracer, OTELTracer) that extend operon.core.tracing.Tracer

Example:
    ```python
    from operon.telemetry import LangfuseTracer, OTELTracer

    # Langfuse tracer
    tracer = LangfuseTracer(resource="langfuse:default")

    # OpenTelemetry tracer (exports to Jaeger, Zipkin, etc.)
    tracer = OTELTracer(resource="otel:jaeger")

    # Use with workflow engine
    engine = Operon(graph, tracer=tracer)
    result = await engine.run(inputs={...})
    ```

    ```python
    # Prompt management (requires langfuse SDK)
    from operon.telemetry import LangfusePromptManager, LangfuseConfig

    pm = LangfusePromptManager(config=LangfuseConfig.from_env())
    prompt = pm["my-prompt"]
    ```
"""

# Auto-register backends to ResourceHub on import
import operon.telemetry.plugin  # noqa: F401 — auto-registers on import
from operon.core.tracing import Tracer

# Backends (configs + clients)
from operon.telemetry.backends import (
    LangfuseClient,
    LangfuseConfig,
    LangfusePromptManager,
    OTELClient,
    OTELConfig,
)

# Tracers
from operon.telemetry.tracers import (
    OperonEyesTracer,
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
    "OperonEyesTracer",
    "LangfuseTracer",
    "OTELTracer",
    # Base class (from operon.core.tracing)
    "Tracer",
]
