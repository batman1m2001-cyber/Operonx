"""OpenTelemetry tracer — vendor-neutral trace export.

Inherits from hush.core.tracing.Tracer. Flush runs in FlushWorker's
thread pool, never blocking the main async thread.

Exports traces to any OTLP-compatible backend (Jaeger, Zipkin,
Datadog, New Relic, Grafana Tempo, etc.).
"""

from typing import TYPE_CHECKING, Any, Dict, List, Optional

from hush.core.tracing import Tracer

if TYPE_CHECKING:
    from hush.telemetry.backends.otel import OTELConfig


class OTELTracer(Tracer):
    """Tracer that sends workflow traces via OpenTelemetry.

    Example:
        ```python
        from hush.telemetry import OTELTracer, OTELConfig

        # Simple: Direct config
        tracer = OTELTracer(config=OTELConfig.jaeger())

        # Production: Use ResourceHub
        tracer = OTELTracer(resource="otel:jaeger", tags=["prod"])

        # Use with workflow engine
        result = await engine.run(inputs={...}, tracers=[tracer])
        ```
    """

    def __init__(
        self,
        config: Optional["OTELConfig"] = None,
        resource: Optional[str] = None,
        tags: Optional[List[str]] = None,
    ):
        super().__init__(tags=tags)
        if config is None and resource is None:
            raise ValueError("Must provide either 'config' or 'resource'")
        if config is not None and resource is not None:
            raise ValueError("Cannot provide both 'config' and 'resource'")
        self._config = config
        self._resource = resource

    @property
    def resource(self) -> Optional[str]:
        return self._resource

    def _get_client(self):
        """Get OTELClient from config or ResourceHub."""
        if self._config is not None:
            from hush.telemetry.backends.otel import OTELClient

            return OTELClient(self._config)

        from hush.core.registry import get_hub

        return get_hub().otel(self._resource)

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

    def flush(self, trace_data: Dict[str, Any]) -> None:
        """Send trace data via OpenTelemetry.

        Called by FlushWorker in a background thread.

        Args:
            trace_data: {request_id, workflow_name, user_id, session_id,
                        tags, graph_structure, records}
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

            # Build structure lookup: op_name → {parent_name, contain_generation, ...}
            structure_map = {s["op_name"]: s for s in trace_data.get("graph_structure", [])}

            LOGGER.info(
                "Workflow: %s, Request ID: %s, Creating OpenTelemetry trace hierarchy...",
                workflow_name,
                req_id,
            )

            otel_tracer = client.tracer

            # Track created spans for parent-child linking
            spans: Dict[str, Any] = {}
            span_end_times: Dict[str, Optional[int]] = {}

            for record in trace_data.get("records", []):
                op_name = record["op_name"]
                context_id = record.get("context_id")
                structure = structure_map.get(op_name, {})
                parent_name = structure.get("parent_name")
                contain_generation = structure.get("contain_generation", False)

                # Build unique key for context-aware nodes
                trace_key = f"{op_name}:{context_id}" if context_id else op_name

                # Resolve parent key with context awareness
                parent_key = parent_name
                if parent_name and context_id:
                    context_parent_key = f"{parent_name}:{context_id}"
                    if context_parent_key in spans:
                        parent_key = context_parent_key
                    else:
                        last_dot = context_id.rfind(".")
                        if last_dot > 0:
                            parent_context = context_id[:last_dot]
                            parent_with_parent_ctx = f"{parent_name}:{parent_context}"
                            if parent_with_parent_ctx in spans:
                                parent_key = parent_with_parent_ctx

                # Timing
                start_time_ns = self._datetime_to_ns(record.get("start_time"))
                end_time_ns = self._datetime_to_ns(record.get("end_time"))

                # Short name for display
                short_name = self._get_short_name(op_name)

                # Build span attributes
                attributes = {
                    "workflow.name": workflow_name,
                    "workflow.request_id": req_id,
                    "op.name": op_name,
                }

                if user_id:
                    attributes["user.id"] = user_id
                    attributes["langfuse.user.id"] = user_id
                if session_id:
                    attributes["session.id"] = session_id
                    attributes["langfuse.session.id"] = session_id
                if tags:
                    clean_tags = [t for t in tags if t is not None]
                    if clean_tags:
                        attributes["langfuse.tags"] = clean_tags

                # LLM-specific attributes
                if contain_generation:
                    attributes["llm.request.type"] = "generation"
                    if record.get("model"):
                        attributes["llm.model"] = record["model"]
                    usage = record.get("usage")
                    if usage:
                        if "prompt_tokens" in usage:
                            attributes["llm.usage.prompt_tokens"] = usage["prompt_tokens"]
                        if "completion_tokens" in usage:
                            attributes["llm.usage.completion_tokens"] = usage["completion_tokens"]
                        if "total_tokens" in usage:
                            attributes["llm.usage.total_tokens"] = usage["total_tokens"]

                # Serialize input/output as attributes
                if record.get("inputs"):
                    try:
                        import json

                        input_str = json.dumps(record["inputs"])
                        if len(input_str) < 10000:
                            attributes["input"] = input_str
                    except (TypeError, ValueError):
                        pass

                if record.get("outputs"):
                    try:
                        import json

                        output_str = json.dumps(record["outputs"])
                        if len(output_str) < 10000:
                            attributes["output"] = output_str
                    except (TypeError, ValueError):
                        pass

                # Metadata as attributes
                if record.get("metadata"):
                    for key, value in record["metadata"].items():
                        if isinstance(value, (str, int, float, bool)):
                            attributes[f"metadata.{key}"] = value

                if parent_name is None:
                    # Root span
                    span = otel_tracer.start_span(
                        name=workflow_name,
                        attributes=attributes,
                        start_time=start_time_ns,
                    )
                    spans[trace_key] = span
                    span_end_times[trace_key] = end_time_ns
                else:
                    # Child span with parent context
                    parent_span = spans.get(parent_key)
                    if parent_span is None:
                        LOGGER.warning(
                            "Parent '%s' not found for op '%s'", parent_key, op_name
                        )
                        continue

                    ctx = trace.set_span_in_context(parent_span)
                    span = otel_tracer.start_span(
                        name=short_name,
                        context=ctx,
                        attributes=attributes,
                        start_time=start_time_ns,
                    )
                    spans[trace_key] = span
                    span_end_times[trace_key] = end_time_ns

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
        return f"<OTELTracer endpoint={self._config.endpoint}>"
