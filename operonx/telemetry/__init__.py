"""Operon telemetry — Langfuse exporter + backwards-compat shims.

The new event-stream pipeline lives in ``operonx.core.tracing``. Heavier
exporters (Langfuse) live here under ``operonx.telemetry.exporters``;
their HTTP clients live under ``operonx.telemetry.backends``.

Example — explicit pipeline (recommended for new code)::

    from operonx import Operon
    from operonx.core.tracing import TracePipeline
    from operonx.telemetry.exporters import LangfuseTreeExporter

    pipeline = TracePipeline(exporters=[
        LangfuseTreeExporter(resource="langfuse:default"),
    ])
    engine = Operon(graph, tracer=pipeline)

Example — legacy constructor (kept for back-compat)::

    from operonx.telemetry import LangfuseTracer

    tracer = LangfuseTracer(resource="langfuse:default")  # IS a TracePipeline
    engine = Operon(graph, tracer=tracer)

Prompt management (requires the Langfuse SDK)::

    from operonx.telemetry import LangfuseConfig, LangfusePromptManager

    pm = LangfusePromptManager(config=LangfuseConfig.from_env())
    prompt = pm["my-prompt"]
"""

# Auto-register backends to ResourceHub on import
import operonx.telemetry.plugin  # noqa: F401
from operonx.telemetry.backends import (
    LangfuseClient,
    LangfuseConfig,
    LangfusePromptManager,
)
from operonx.telemetry.tracers import LangfuseTracer

__all__ = [
    "LangfuseTracer",
    "LangfuseConfig",
    "LangfuseClient",
    "LangfusePromptManager",
]
