"""Built-in exporters — zero-dep destinations for the trace pipeline.

Heavier exporters with optional 3rd-party deps (Langfuse, OTel) live in
``operonx.telemetry.exporters``.

See ``docs/TRACING_REDESIGN_PLAN.md`` §3.4.
"""

from operonx.core.tracing.exporters.local_file import JsonFileExporter

__all__ = ["JsonFileExporter"]
