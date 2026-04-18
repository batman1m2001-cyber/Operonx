"""Operon telemetry — Langfuse, OpenTelemetry, and OperonEyes tracers.

Backend-agnostic observability: every tracer extends ``operon.core.tracing.Tracer``
and flushes in the background thread pool, never blocking the main async thread.

Example::

    from operon import Operon
    from operon.telemetry import LangfuseTracer

    tracer = LangfuseTracer(resource="langfuse:default")
    engine = Operon(graph, tracer=tracer)
    result = await engine.run(inputs={...})

Prompt management (requires the Langfuse SDK)::

    from operon.telemetry import LangfuseConfig, LangfusePromptManager

    pm = LangfusePromptManager(config=LangfuseConfig.from_env())
    prompt = pm["my-prompt"]
"""

# Auto-register backends to ResourceHub on import
import operon.telemetry.plugin  # noqa: F401

from operon.core.tracing import Tracer
from operon.telemetry.backends import (
    LangfuseClient,
    LangfuseConfig,
    LangfusePromptManager,
    OTELClient,
    OTELConfig,
)
from operon.telemetry.tracers import (
    LangfuseTracer,
    OperonEyesTracer,
    OTELTracer,
)

__all__ = [
    # Base (re-exported from operon.core.tracing for convenience)
    "Tracer",
    # Tracers
    "LangfuseTracer",
    "OTELTracer",
    "OperonEyesTracer",
    # Configs
    "LangfuseConfig",
    "OTELConfig",
    # Clients
    "LangfuseClient",
    "LangfusePromptManager",
    "OTELClient",
]
