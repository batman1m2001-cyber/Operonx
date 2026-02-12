"""Langfuse tracer implementation using subprocess-based flushing.

This tracer inherits from hush.core.tracers.BaseTracer and uses
ResourceHub to get the LangfuseClient in the subprocess.
"""

import base64
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from hush.core.tracers import BaseTracer, register_tracer

if TYPE_CHECKING:
    from hush.ops.backends.langfuse import LangfuseConfig


@register_tracer
class LangfuseTracer(BaseTracer):
    """Tracer that sends workflow traces to Langfuse.

    This tracer uses subprocess-based flushing to avoid blocking
    the main workflow execution.

    Example:
        ```python
        from hush.ops import LangfuseTracer, LangfuseConfig

        # Simple: Direct config (no ResourceHub needed)
        tracer = LangfuseTracer(config=LangfuseConfig.from_env())

        # Production: Use ResourceHub for centralized config
        tracer = LangfuseTracer(resource_key="langfuse:default", tags=["prod"])

        # Use with workflow engine
        result = await engine.run(inputs={...}, tracer=tracer)
        ```
    """

    def __init__(
        self,
        config: Optional["LangfuseConfig"] = None,
        resource_key: Optional[str] = None,
        tags: Optional[List[str]] = None,
    ):
        """Initialize the Langfuse tracer.

        Args:
            config: Direct LangfuseConfig (simple usage, no ResourceHub needed)
            resource_key: ResourceHub key for LangfuseClient (e.g., "langfuse:default")
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
    def _resolve_media(
        trace_data: Dict[str, Any],
        media_attachments: Optional[Dict[str, Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        """Resolve media attachments to LangfuseMedia objects.

        Args:
            trace_data: Trace data dict (input, output, metadata)
            media_attachments: Serialized media attachments

        Returns:
            Modified trace_data with LangfuseMedia objects injected
        """
        if not media_attachments:
            return trace_data

        try:
            from langfuse.media import LangfuseMedia
        except ImportError:
            return trace_data

        # Group attachments by target location
        by_location: Dict[str, Dict[str, Any]] = {
            "input": {},
            "output": {},
            "metadata": {},
        }

        for key, attachment in media_attachments.items():
            attach_to = attachment.get("attach_to", "metadata")
            content_type = attachment["content_type"]

            # Get content bytes
            if "base64" in attachment:
                content_bytes = base64.b64decode(attachment["base64"])
            elif "path" in attachment:
                with open(attachment["path"], "rb") as f:
                    content_bytes = f.read()
            else:
                continue

            # Create LangfuseMedia object
            media_obj = LangfuseMedia(content_bytes=content_bytes, content_type=content_type)
            by_location[attach_to][key] = media_obj

        # Inject into trace_data
        result = trace_data.copy()

        if by_location["input"]:
            if isinstance(result.get("input"), dict):
                result["input"] = {**result["input"], **by_location["input"]}
            else:
                result["input"] = {
                    "_original": result.get("input"),
                    **by_location["input"],
                }

        if by_location["output"]:
            if isinstance(result.get("output"), dict):
                result["output"] = {**result["output"], **by_location["output"]}
            else:
                result["output"] = {
                    "_original": result.get("output"),
                    **by_location["output"],
                }

        if by_location["metadata"]:
            if isinstance(result.get("metadata"), dict):
                result["metadata"] = {**result["metadata"], **by_location["metadata"]}
            else:
                result["metadata"] = by_location["metadata"]

        return result

    @staticmethod
    def flush(flush_data: Dict[str, Any]) -> None:
        """Execute flush logic in a separate process.

        This method runs in a subprocess, so it must:
        - Re-import all dependencies
        - Use only the data provided in flush_data
        - Load LangfuseClient from ResourceHub

        Args:
            flush_data: Dictionary containing all data needed for flushing
        """
        from hush.core.loggings import LOGGER

        try:
            tracer_config = flush_data["tracer_config"]

            # Get client: direct config or ResourceHub
            if "config" in tracer_config:
                from hush.ops.backends.langfuse import LangfuseClient, LangfuseConfig

                config = LangfuseConfig(**tracer_config["config"])
                client = LangfuseClient(config)
            else:
                from hush.core.registry import get_hub

                client = get_hub().langfuse(tracer_config["resource_key"])

            workflow_name = flush_data["workflow_name"]
            req_id = flush_data["request_id"]
            user_id = flush_data.get("user_id")
            session_id = flush_data.get("session_id")
            tags = flush_data.get("tags", [])
            execution_order = flush_data["execution_order"]
            nodes_trace_data = flush_data["nodes_trace_data"]

            LOGGER.info(
                "Workflow: %s, Request ID: %s, Creating Langfuse trace hierarchy...",
                workflow_name,
                req_id,
            )

            # Track created Langfuse objects for parent-child linking
            langfuse_objects = {}
            root_trace = None

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

                # Resolve media attachments to LangfuseMedia objects
                media_attachments = trace_data.pop("media_attachments", None)
                trace_data = LangfuseTracer._resolve_media(trace_data, media_attachments)

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
                    if context_parent_id in langfuse_objects:
                        parent_id = context_parent_id
                    else:
                        # Try parent context (strip last .[N] or [N])
                        # e.g., [0].[1] -> [0], [0] -> None
                        last_dot = context_id.rfind(".")
                        if last_dot > 0:
                            parent_context = context_id[:last_dot]
                            parent_with_parent_ctx = f"{parent_id}:{parent_context}"
                            if parent_with_parent_ctx in langfuse_objects:
                                parent_id = parent_with_parent_ctx

                if parent_id is None:
                    # Root node - create trace
                    # Filter out None values from tags
                    clean_tags = [t for t in tags if t is not None] if tags else None
                    root_trace = client.trace(
                        id=req_id,
                        name=workflow_name,
                        user_id=user_id,
                        session_id=session_id,
                        tags=clean_tags if clean_tags else None,
                        start_time=trace_data.get("start_time"),
                        end_time=trace_data.get("end_time"),
                        input=trace_data.get("input"),
                        output=trace_data.get("output"),
                        metadata=trace_data.get("metadata"),
                    )
                    langfuse_objects[node_id] = root_trace
                else:
                    # Child node - create span or generation
                    parent = langfuse_objects.get(parent_id)
                    if parent is None:
                        LOGGER.warning(
                            "Parent '%s' not found for node '%s'",
                            parent_id,
                            node_id,
                        )
                        continue

                        # Use short name (last part after .) for display
                    full_name = trace_data.get("name", "")
                    short_name = full_name.rsplit(".", 1)[-1] if "." in full_name else full_name
                    trace_data["name"] = short_name

                    if contain_generation:
                        # Transform usage to Langfuse format
                        # OpenAI format: prompt_tokens, completion_tokens, total_tokens
                        # Langfuse format: input, output, total
                        usage = trace_data.pop("usage", None)
                        if usage:
                            langfuse_usage = {}
                            if "prompt_tokens" in usage:
                                langfuse_usage["input"] = usage["prompt_tokens"]
                            if "completion_tokens" in usage:
                                langfuse_usage["output"] = usage["completion_tokens"]
                            if "total_tokens" in usage:
                                langfuse_usage["total"] = usage["total_tokens"]
                            if langfuse_usage:
                                trace_data["usage"] = langfuse_usage

                        # Extract cost - pass as metadata since SDK v2 doesn't support cost_details
                        cost = trace_data.pop("cost", None)
                        if cost:
                            # Add cost to metadata for visibility
                            if "metadata" not in trace_data or trace_data["metadata"] is None:
                                trace_data["metadata"] = {}
                            trace_data["metadata"]["cost_usd"] = cost

                        langfuse_objects[node_id] = parent.generation(**trace_data)
                    else:
                        # Spans don't use cost or usage, remove them
                        trace_data.pop("cost", None)
                        trace_data.pop("usage", None)
                        langfuse_objects[node_id] = parent.span(**trace_data)

            # Ensure all traces are sent to Langfuse
            client.flush()

            # Generate and log trace URL
            trace_url = None
            if root_trace:
                trace_url = root_trace.get_trace_url()

            if trace_url:
                LOGGER.info(
                    "Workflow: %s, Request ID: %s, Langfuse trace created. View: %s",
                    workflow_name,
                    req_id,
                    trace_url,
                )
            else:
                LOGGER.warning(
                    "Workflow: %s, Request ID: %s, Failed to generate Langfuse trace URL.",
                    workflow_name,
                    req_id,
                )

        except ImportError as e:
            from hush.core.loggings import LOGGER

            LOGGER.error(
                "langfuse package is required for LangfuseTracer. "
                "Install it with: pip install langfuse. Error: %s",
                str(e),
            )

        except Exception as e:
            import traceback

            from hush.core.loggings import LOGGER

            LOGGER.error(
                "Langfuse flush failed: %s\nTraceback:\n%s",
                str(e),
                traceback.format_exc(),
            )

    def __repr__(self) -> str:
        """String representation."""
        if self._resource_key:
            return f"<LangfuseTracer resource_key={self._resource_key}>"
        return f"<LangfuseTracer host={self._config.host}>"
