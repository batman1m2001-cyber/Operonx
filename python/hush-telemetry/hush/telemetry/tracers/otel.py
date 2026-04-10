"""OpenTelemetry tracer — vendor-neutral trace export.

Inherits from hush.core.tracing.Tracer. Flush runs in FlushWorker's
thread pool, never blocking the main async thread.

Exports traces to any OTLP-compatible backend (Jaeger, Zipkin,
Datadog, New Relic, Grafana Tempo, etc.).
"""

from typing import TYPE_CHECKING, Any, Dict, List, Optional

from hush.telemetry.tracers._base import ConfigurableTracer

if TYPE_CHECKING:
    from hush.core.tracing.trace_filter import TraceFilter
    from hush.telemetry.backends.otel import OTELConfig


class OTELTracer(ConfigurableTracer):
    """Tracer that sends workflow traces via OpenTelemetry.

    Example:
        ```python
        from hush.telemetry import OTELTracer, OTELConfig

        # Simple: Direct config
        tracer = OTELTracer(config=OTELConfig.jaeger())

        # Production: Use ResourceHub
        tracer = OTELTracer(resource="otel:jaeger", tags=["prod"])

        # Use with workflow engine
        engine = Hush(graph, tracer=tracer)
        result = await engine.run(inputs={...})
        ```
    """

    def __init__(
        self,
        config: Optional["OTELConfig"] = None,
        resource: Optional[str] = None,
        tags: Optional[List[str]] = None,
        trace_filter: Optional["TraceFilter"] = None,
    ):
        super().__init__(config=config, resource=resource, tags=tags, trace_filter=trace_filter)

    def _make_client(self, config):
        from hush.telemetry.backends.otel import OTELClient

        return OTELClient(config)

    @staticmethod
    def _datetime_to_ns(dt) -> Optional[int]:
        """Convert datetime or ISO string to nanoseconds since epoch."""
        if dt is None:
            return None
        from datetime import datetime

        if isinstance(dt, str):
            dt = datetime.fromisoformat(dt.replace("Z", "+00:00"))
        if isinstance(dt, datetime):
            return int(dt.timestamp() * 1_000_000_000)
        return None

    @staticmethod
    def _get_short_name(full_name: str) -> str:
        """Extract short name from full path (last part after '.')."""
        if not full_name:
            return full_name
        return full_name.rsplit(".", 1)[-1]

    @staticmethod
    def _context_to_str(ctx) -> str | None:
        """Convert a context tuple to a dot-separated string for trace keys."""
        if not ctx:
            return None
        if isinstance(ctx, (list, tuple)):
            return ".".join(str(s) for s in ctx)
        return str(ctx)

    def flush(self, trace_data: Dict[str, Any]) -> None:
        """Send trace data via OpenTelemetry.

        Called by FlushWorker in a background thread. Uses pre-computed
        TraceNode tree — no parent-resolution heuristics needed.

        Args:
            trace_data: {request_id, workflow_name, user_id, session_id,
                        tags, nodes}
        """
        from hush.core.loggings import LOGGER

        try:
            from opentelemetry import trace
            from opentelemetry.trace import Status, StatusCode

            client = self._get_client()

            workflow_name = trace_data["workflow_name"]
            req_id = trace_data["request_id"]
            user_id = trace_data.get("user_id")
            session_id = trace_data.get("session_id")
            tags = trace_data.get("tags") or []

            LOGGER.info(
                "Workflow: %s, Request ID: %s, Creating OpenTelemetry trace hierarchy...",
                workflow_name,
                req_id,
            )

            otel_tracer = client.tracer
            spans: Dict[str, Any] = {}
            span_end_times: Dict[str, Optional[int]] = {}

            for node in trace_data.get("nodes", []):
                key = node["trace_key"]
                parent_key = node.get("parent_trace_key")
                node_type = node.get("node_type", "span")
                kind = node.get("kind", "batch")

                start_time_ns = self._datetime_to_ns(node.get("start_time"))
                end_time_ns = self._datetime_to_ns(node.get("end_time"))

                # Build span attributes
                attributes: Dict[str, Any] = {
                    "workflow.name": workflow_name,
                    "workflow.request_id": req_id,
                }
                if node.get("op_name"):
                    attributes["op.name"] = node["op_name"]
                if user_id:
                    attributes["user.id"] = user_id
                if session_id:
                    attributes["session.id"] = session_id
                if tags:
                    clean_tags = [t for t in tags if t is not None]
                    if clean_tags:
                        attributes["workflow.tags"] = clean_tags

                # Kind and streaming metadata
                if kind and kind != "batch":
                    attributes["op.kind"] = kind
                metadata = node.get("metadata") or {}
                if metadata.get("yield_count") is not None:
                    attributes["op.yield_count"] = metadata["yield_count"]
                # LLM-specific
                if node_type == "generation":
                    attributes["llm.request.type"] = "generation"
                    if node.get("model"):
                        attributes["llm.model"] = node["model"]
                    usage = node.get("usage")
                    if usage:
                        if "prompt_tokens" in usage:
                            attributes["llm.usage.prompt_tokens"] = usage["prompt_tokens"]
                        if "completion_tokens" in usage:
                            attributes["llm.usage.completion_tokens"] = usage["completion_tokens"]
                        if "total_tokens" in usage:
                            attributes["llm.usage.total_tokens"] = usage["total_tokens"]

                # Serialize I/O as attributes
                if node.get("inputs"):
                    try:
                        import json

                        input_str = json.dumps(node["inputs"])
                        if len(input_str) < 10000:
                            attributes["input"] = input_str
                    except (TypeError, ValueError):
                        pass
                if node.get("outputs"):
                    try:
                        import json

                        output_str = json.dumps(node["outputs"])
                        if len(output_str) < 10000:
                            attributes["output"] = output_str
                    except (TypeError, ValueError):
                        pass

                # Metadata as attributes
                for mk, mv in metadata.items():
                    if mk not in ("kind", "yield_count", "spawned_by", "depth"):
                        if isinstance(mv, (str, int, float, bool)):
                            attributes[f"metadata.{mk}"] = mv

                if parent_key is None:
                    # Root span
                    span = otel_tracer.start_span(
                        name=node["display_name"],
                        attributes=attributes,
                        start_time=start_time_ns,
                    )
                    spans[key] = span
                    span_end_times[key] = end_time_ns
                else:
                    parent_span = spans.get(parent_key)
                    if parent_span is None:
                        LOGGER.warning("Parent '%s' not found for node '%s'", parent_key, key)
                        continue

                    ctx = trace.set_span_in_context(parent_span)
                    span = otel_tracer.start_span(
                        name=node["display_name"],
                        context=ctx,
                        attributes=attributes,
                        start_time=start_time_ns,
                    )
                    spans[key] = span
                    span_end_times[key] = end_time_ns

            # End all spans in reverse order (children first)
            for key in reversed(list(spans.keys())):
                span = spans[key]
                end_time = span_end_times.get(key)
                span.set_status(Status(StatusCode.OK))
                span.end(end_time=end_time)

            client.flush()

            LOGGER.info(
                "Workflow: %s, Request ID: %s, OpenTelemetry trace created successfully.",
                workflow_name,
                req_id,
            )

        except ImportError as e:
            from hush.core.loggings import LOGGER

            LOGGER.error(
                "opentelemetry packages are required for OTELTracer. "
                "Install them with: pip install opentelemetry-api opentelemetry-sdk "
                "opentelemetry-exporter-otlp-proto-grpc. Error: %s",
                str(e),
            )

        except Exception as e:
            import traceback

            from hush.core.loggings import LOGGER

            LOGGER.error(
                "OpenTelemetry flush failed: %s\nTraceback:\n%s",
                str(e),
                traceback.format_exc(),
            )

    def __repr__(self) -> str:
        if self._resource:
            return f"<OTELTracer resource={self._resource}>"
        endpoint = self._config.endpoint if self._config else "?"
        return f"<OTELTracer endpoint={endpoint}>"
