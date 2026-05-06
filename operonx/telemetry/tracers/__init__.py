"""Backwards-compat shim for the legacy tracer constructor.

Pre-T2: each backend had its own ``Tracer`` subclass.
Post-T2.12: ``LangfuseTracer`` is a ``TracePipeline`` subclass that wires
up a ``LangfuseTreeExporter`` internally. New code should use
``operonx.telemetry.exporters.LangfuseTreeExporter`` (or
``LangfuseGroupedTimelineExporter``) inside an explicit ``TracePipeline``.
"""

from operonx.telemetry.tracers.langfuse import LangfuseTracer

__all__ = ["LangfuseTracer"]
