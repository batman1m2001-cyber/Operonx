"""Operon telemetry — Langfuse, OpenTelemetry, and OperonEyes tracers.

Backend-agnostic observability: every tracer extends ``operonx.core.tracing.Tracer``
and flushes in the background thread pool, never blocking the main async thread.

Example::

    from operonx import Operon
    from operonx.telemetry import LangfuseTracer

    tracer = LangfuseTracer(resource="langfuse:default")
    engine = Operon(graph, tracer=tracer)
    result = await engine.run(inputs={...})

Prompt management (requires the Langfuse SDK)::

    from operonx.telemetry import LangfuseConfig, LangfusePromptManager

    pm = LangfusePromptManager(config=LangfuseConfig.from_env())
    prompt = pm["my-prompt"]
"""

# Auto-register backends to ResourceHub on import
import operonx.telemetry.plugin  # noqa: F401

from operonx.core.tracing import Tracer
from operonx.telemetry.backends import (
    LangfuseClient,
    LangfuseConfig,
    LangfusePromptManager,
    OTELClient,
    OTELConfig,
)
from operonx.telemetry.tracers import (
    LangfuseTracer,
    OperonEyesTracer,
    OTELTracer,
)

__all__ = [
    # Base (re-exported from operonx.core.tracing for convenience)
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
