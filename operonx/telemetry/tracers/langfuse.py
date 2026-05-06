"""LangfuseTracer — backwards-compatible shim around the new pipeline.

Pre-T2: this class did the trace-tree → Langfuse-batch translation directly,
consumed by ``flush_worker``. As of T2.12 the heavy lifting moves into
``LangfuseTreeExporter``; this class is a thin pipeline-shaped wrapper that
preserves the legacy constructor signature so existing user code keeps
working without changes:

    tracer = LangfuseTracer(resource="langfuse:edupia", trace_filter=TraceFilter(...))
    engine = Operon(graph, tracer=tracer)   # routes through new pipeline

The class IS a ``TracePipeline``, so the engine binds an emitter and events
flow through processors (derived from any ``trace_filter`` argument) into
the exporter. No legacy ``flush(trace_data)`` path; ``state.tracing`` flag
no longer matters.

Migration off this shim → use ``TracePipeline`` directly with explicit
processors and ``LangfuseTreeExporter``. See
``docs/TRACING_REDESIGN_PLAN.md`` §10 (Educa migration).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Dict, List, Optional

from operonx.core.tracing.legacy import trace_filter_to_processors
from operonx.core.tracing.pipeline import TracePipeline

if TYPE_CHECKING:
    from operonx.core.tracing.trace_filter import TraceFilter
    from operonx.telemetry.backends.langfuse import LangfuseConfig


class LangfuseTracer(TracePipeline):
    """Pipeline-shaped Langfuse tracer with the legacy constructor signature.

    Constructor accepts the same args as the pre-T2 tracer. Internally
    constructs a ``LangfuseTreeExporter`` plus the processor chain derived
    from ``trace_filter`` (via ``trace_filter_to_processors``).

    Args:
        config:       Direct ``LangfuseConfig`` (mutually exclusive with
            ``resource``).
        resource:     ``"langfuse:..."`` ResourceHub key.
        tags:         Static tags attached to every trace.
        trace_filter: Legacy ``TraceFilter`` config; auto-loaded from the
            resource's YAML config block if ``None``.

    Use ``LangfuseTracer.as_pipeline(...)`` to get a plain ``TracePipeline``
    without the legacy attributes — recommended for new code.
    """

    def __init__(
        self,
        config: Optional["LangfuseConfig"] = None,
        resource: Optional[str] = None,
        tags: Optional[List[str]] = None,
        trace_filter: Optional["TraceFilter"] = None,
    ) -> None:
        import warnings
        warnings.warn(
            "LangfuseTracer(...) is deprecated and will be removed in a future "
            "release. Migrate to TracePipeline + LangfuseTreeExporter directly, "
            "or use LangfuseTracer.as_pipeline(...) as a one-line transitional "
            "shim. See docs/TRACING_REDESIGN_PLAN.md §10 (educa migration).",
            DeprecationWarning, stacklevel=2,
        )

        if config is None and resource is None:
            raise ValueError("Must provide either 'config' or 'resource'")
        if config is not None and resource is not None:
            raise ValueError("Cannot provide both 'config' and 'resource'")

        # Auto-load trace_filter from resource YAML if not supplied — same
        # behavior as the pre-T2 ConfigurableTracer base class.
        if trace_filter is None and resource is not None:
            trace_filter = self._auto_load_trace_filter_from_resource(resource)

        # Build the new exporter + processor chain
        from operonx.telemetry.exporters import LangfuseTreeExporter
        exporter = LangfuseTreeExporter(
            config=config, resource=resource, tags=tags,
        )
        processors = (
            trace_filter_to_processors(trace_filter) if trace_filter else []
        )

        # Initialize as a TracePipeline so the engine routes through the new path
        TracePipeline.__init__(
            self, processors=processors, exporters=[exporter],
        )

        # Legacy public attrs — kept so existing callers reading .tags /
        # .resource / .trace_filter / ._get_client() keep working.
        self._config = config
        self._resource = resource
        self.tags = list(tags or [])
        self._trace_filter = trace_filter
        self._exporter = exporter

    # ------------------------------------------------------------------
    # Legacy API surface
    # ------------------------------------------------------------------

    @property
    def resource(self) -> Optional[str]:
        return self._resource

    @property
    def trace_filter(self) -> Optional["TraceFilter"]:
        return self._trace_filter

    def _get_client(self):
        """Return the underlying Langfuse HTTP client.

        Used by legacy callers (e.g. health checks, manual ingest). The
        client lives on the exporter; this delegates so existing call
        sites keep working.
        """
        return self._exporter._get_client()

    def to_config_dict(self) -> Optional[Dict[str, Any]]:
        """Return Langfuse config dict for the Rust backend.

        ``None`` when constructed from a ResourceHub key (in which case the
        Rust side resolves the resource itself).
        """
        if self._config is None:
            return None
        return {
            "public_key": self._config.public_key,
            "secret_key": self._config.secret_key,
            "host": self._config.host,
        }

    def __repr__(self) -> str:
        if self._resource:
            return f"<LangfuseTracer resource={self._resource}>"
        host = self._config.host if self._config else "?"
        return f"<LangfuseTracer host={host}>"

    # ------------------------------------------------------------------
    # Migration helper — explicit conversion to plain TracePipeline
    # ------------------------------------------------------------------

    @classmethod
    def as_pipeline(
        cls,
        config: Optional["LangfuseConfig"] = None,
        resource: Optional[str] = None,
        tags: Optional[List[str]] = None,
        trace_filter: Optional["TraceFilter"] = None,
        *,
        grouped_timeline: bool = False,
    ) -> TracePipeline:
        """Return a plain ``TracePipeline`` configured equivalently.

        Use this when you want the new pipeline shape without the legacy
        attributes (``.tags``, ``.resource``, etc.). The constructor
        provides the same wiring plus those attributes for back-compat.

        Set ``grouped_timeline=True`` for educa's turn-grouped shape (uses
        ``LangfuseGroupedTimelineExporter`` — pair with ``GroupBy`` +
        ``Aggregate`` processors).
        """
        from operonx.telemetry.exporters import (
            LangfuseGroupedTimelineExporter,
            LangfuseTreeExporter,
        )

        if trace_filter is None and resource is not None:
            trace_filter = cls._auto_load_trace_filter_from_resource(resource)

        processors = (
            trace_filter_to_processors(trace_filter) if trace_filter else []
        )

        exporter_cls = (
            LangfuseGroupedTimelineExporter
            if grouped_timeline else LangfuseTreeExporter
        )
        exporter = exporter_cls(config=config, resource=resource, tags=tags)
        return TracePipeline(processors=processors, exporters=[exporter])

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _auto_load_trace_filter_from_resource(
        resource: str,
    ) -> Optional["TraceFilter"]:
        """Load a ``trace_filter:`` block from the resource's YAML config.

        Quiet on any failure — returns ``None``. Mirrors the pre-T2
        ``ConfigurableTracer._load_trace_filter`` behavior so existing
        ``langfuse:edupia`` configs with embedded ``trace_filter`` keys
        keep working.
        """
        from operonx.core.tracing.trace_filter import TraceFilter

        try:
            from operonx.core.registry import ResourceHub
            config = ResourceHub.instance().get_config(resource)
            raw = config if isinstance(config, dict) else config.model_dump()
            tf_dict = raw.get("trace_filter")
            if tf_dict and isinstance(tf_dict, dict):
                return TraceFilter.from_dict(tf_dict)
        except Exception:
            pass
        return None
