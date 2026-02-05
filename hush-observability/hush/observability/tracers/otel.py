"""OpenTelemetry tracer implementation using subprocess-based flushing.

This tracer inherits from hush.core.tracers.BaseTracer and uses
ResourceHub to get the OTELClient in the subprocess.
"""

from typing import TYPE_CHECKING, Any, Dict, List, Optional, Union

from hush.core.tracers import BaseTracer, register_tracer

if TYPE_CHECKING:
    from hush.observability.backends.otel import OTELConfig


@register_tracer
class OTELTracer(BaseTracer):
    """Tracer that sends workflow traces via OpenTelemetry.

    This tracer uses subprocess-based flushing to avoid blocking
    the main workflow execution.

    OpenTelemetry is a vendor-neutral observability framework that
    can export traces to any OTLP-compatible backend (Jaeger, Zipkin,
    Datadog, New Relic, Grafana Tempo, etc.).

    Example:
        ```python
        from hush.observability import OTELTracer, OTELConfig

        # Simple: Direct config (no ResourceHub needed)
        tracer = OTELTracer(config=OTELConfig.jaeger())

        # Production: Use ResourceHub for centralized config
        tracer = OTELTracer(resource_key="otel:jaeger", tags=["prod"])

        # Use with workflow engine
        result = await engine.run(inputs={...}, tracer=tracer)
        ```

    References:
        - Documentation: https://opentelemetry.io/docs/languages/python/
        - GitHub: https://github.com/open-telemetry/opentelemetry-python
    """

    def __init__(
        self,
        config: Optional["OTELConfig"] = None,
        resource_key: Optional[str] = None,
        tags: Optional[List[str]] = None,
    ):
        """Initialize the OpenTelemetry tracer.

        Args:
            config: Direct OTELConfig (simple usage, no ResourceHub needed)
            resource_key: ResourceHub key for OTELClient (e.g., "otel:jaeger")
            tags: Optional list of static tags for filtering/grouping traces

        Raises:
            ValueError: If neither config nor resource_key is provided, or both are provided
        """
        super().__init__(tags=tags)
        if config is None and resource_key is None:
            raise ValueError("Must provide either 'config' or 'resource_key'")
        if config is not None and resource_key is not None:
            raise ValueError("Cannot provide both 'config' and 'resource_key'")
        self._config = config
        self._resource_key = resource_key

    @property
    def resource_key(self) -> Optional[str]:
        """Get the resource key (for backward compatibility)."""
        return self._resource_key

    def _get_tracer_config(self) -> Dict[str, Any]:
        """Return configuration for subprocess."""
        if self._config is not None:
            return {"config": self._config.model_dump()}
        return {"resource_key": self._resource_key}

    @staticmethod
    def _datetime_to_ns(dt) -> Optional[int]:
        """Convert datetime to nanoseconds since epoch."""
        if dt is None:
            return None
        from datetime import datetime

        if isinstance(dt, str):
            # Parse ISO format string
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
    def flush(flush_data: Dict[str, Any]) -> None:
        """Execute flush logic in a separate process.

        This method runs in a subprocess, so it must:
        - Re-import all dependencies
        - Use only the data provided in flush_data
        - Load OTELClient from ResourceHub

        Args:
            flush_data: Dictionary containing all data needed for flushing
        """
        from hush.core.loggings import LOGGER

        try:
            from opentelemetry import trace
            from opentelemetry.trace import Status, StatusCode

            tracer_config = flush_data["tracer_config"]

            # Get client: direct config or ResourceHub
            if "config" in tracer_config:
                from hush.observability.backends.otel import OTELClient, OTELConfig
                config = OTELConfig(**tracer_config["config"])
                client = OTELClient(config)
            else:
                from hush.core.registry import get_hub
                client = get_hub().otel(tracer_config["resource_key"])

            workflow_name = flush_data["workflow_name"]
            req_id = flush_data["request_id"]
            user_id = flush_data.get("user_id")
            session_id = flush_data.get("session_id")
            tags = flush_data.get("tags", [])
            execution_order = flush_data["execution_order"]
            nodes_trace_data = flush_data["nodes_trace_data"]

            LOGGER.info(
                "Workflow: %s, Request ID: %s, Creating OpenTelemetry trace hierarchy...",
                workflow_name,
                req_id,
            )

            # Get the tracer from client
            otel_tracer = client.tracer

            # Track created spans for parent-child linking
            # Store tuples of (span, end_time_ns) for proper ending
            spans: Dict[str, Any] = {}
            span_end_times: Dict[str, Optional[int]] = {}

            for execution in execution_order:
                node_id = execution["node"]
                parent_id = execution["parent"]
                context_id = execution.get("context_id")
                contain_generation = execution.get("contain_generation", False)

                # Get trace data for this node
                trace_key = f"{node_id}:{context_id}" if context_id else node_id
                trace_data = nodes_trace_data.get(trace_key)
                if trace_data is None:
                    continue

                # Remove media_attachments (OTEL doesn't support media like Langfuse)
                trace_data.pop("media_attachments", None)

                # Build unique key for context-aware nodes
                if context_id:
                    node_id = f"{node_id}:{context_id}"

                # Check for context-aware parent
                # For nested contexts like [0].[1], we need to find the right parent:
                # - First try exact context match: parent:[0].[1]
                # - Then try parent context (strip last .[N]): parent:[0]
                # - Finally fall back to parent without context
                if parent_id and context_id:
                    # Try exact context match first
                    context_parent_id = f"{parent_id}:{context_id}"
                    if context_parent_id in spans:
                        parent_id = context_parent_id
                    else:
                        # Try parent context (strip last .[N] or [N])
                        # e.g., [0].[1] -> [0], [0] -> None
                        last_dot = context_id.rfind(".")
                        if last_dot > 0:
                            parent_context = context_id[:last_dot]
                            parent_with_parent_ctx = f"{parent_id}:{parent_context}"
                            if parent_with_parent_ctx in spans:
                                parent_id = parent_with_parent_ctx

                # Extract timing
                start_time_ns = OTELTracer._datetime_to_ns(trace_data.get("start_time"))
                end_time_ns = OTELTracer._datetime_to_ns(trace_data.get("end_time"))

                # Get short name (last part after '.')
                full_name = trace_data.get("name", node_id)
                short_name = OTELTracer._get_short_name(full_name)

                # Build span attributes
                attributes = {
                    "workflow.name": workflow_name,
                    "workflow.request_id": req_id,
                    "node.name": full_name,  # Keep full name in attributes
                }

                if user_id:
                    attributes["user.id"] = user_id
                    attributes["langfuse.user.id"] = user_id  # Langfuse-specific
                if session_id:
                    attributes["session.id"] = session_id
                    attributes["langfuse.session.id"] = session_id  # Langfuse-specific
                if tags:
                    # Filter out None values - use langfuse.* namespace for Langfuse compatibility
                    clean_tags = [t for t in tags if t is not None]
                    if clean_tags:
                        # Langfuse expects langfuse.tags as array
                        attributes["langfuse.tags"] = clean_tags

                # Add LLM-specific attributes for generations
                if contain_generation:
                    attributes["llm.request.type"] = "generation"
                    if "model" in trace_data:
                        attributes["llm.model"] = trace_data["model"]
                    if "usage" in trace_data:
                        usage = trace_data["usage"]
                        if "prompt_tokens" in usage:
                            attributes["llm.usage.prompt_tokens"] = usage["prompt_tokens"]
                        if "completion_tokens" in usage:
                            attributes["llm.usage.completion_tokens"] = usage["completion_tokens"]
                        if "total_tokens" in usage:
                            attributes["llm.usage.total_tokens"] = usage["total_tokens"]

                # Add input/output as attributes (serialized)
                if trace_data.get("input"):
                    try:
                        import json

                        input_str = json.dumps(trace_data["input"])
                        if len(input_str) < 10000:  # Limit attribute size
                            attributes["input"] = input_str
                    except (TypeError, ValueError):
                        pass

                if trace_data.get("output"):
                    try:
                        import json

                        output_str = json.dumps(trace_data["output"])
                        if len(output_str) < 10000:  # Limit attribute size
                            attributes["output"] = output_str
                    except (TypeError, ValueError):
                        pass

                # Add metadata as attributes
                if trace_data.get("metadata"):
                    for key, value in trace_data["metadata"].items():
                        if isinstance(value, (str, int, float, bool)):
                            attributes[f"metadata.{key}"] = value

                # Create span with proper timing
                if parent_id is None:
                    # Root span - use workflow name
                    span = otel_tracer.start_span(
                        name=workflow_name,
                        attributes=attributes,
                        start_time=start_time_ns,
                    )
                    spans[node_id] = span
                    span_end_times[node_id] = end_time_ns
                else:
                    # Child span - need to use parent context
                    parent_span = spans.get(parent_id)
                    if parent_span is None:
                        LOGGER.warning(
                            "Parent '%s' not found for node '%s'",
                            parent_id,
                            node_id,
                        )
                        continue

                    # Create child span with parent context and timing
                    ctx = trace.set_span_in_context(parent_span)
                    span = otel_tracer.start_span(
                        name=short_name,  # Use short name for display
                        context=ctx,
                        attributes=attributes,
                        start_time=start_time_ns,
                    )
                    spans[node_id] = span
                    span_end_times[node_id] = end_time_ns

            # End all spans with proper end times (in reverse order - children first)
            for node_id in reversed(list(spans.keys())):
                span = spans[node_id]
                end_time = span_end_times.get(node_id)
                span.set_status(Status(StatusCode.OK))
                span.end(end_time=end_time)

            # Flush to ensure traces are sent
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
        """String representation."""
        if self._resource_key:
            return f"<OTELTracer resource_key={self._resource_key}>"
        return f"<OTELTracer endpoint={self._config.endpoint}>"
