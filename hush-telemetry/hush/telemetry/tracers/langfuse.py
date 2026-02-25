"""Langfuse tracer — sends workflow traces to Langfuse.

Inherits from hush.core.tracing.Tracer. Flush runs in FlushWorker's
thread pool, never blocking the main async thread.
"""

import base64
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from hush.core.tracing import Tracer

if TYPE_CHECKING:
    from hush.telemetry.backends.langfuse import LangfuseConfig


class LangfuseTracer(Tracer):
    """Tracer that sends workflow traces to Langfuse.

    Example:
        ```python
        from hush.telemetry import LangfuseTracer, LangfuseConfig

        # Simple: Direct config
        tracer = LangfuseTracer(config=LangfuseConfig.from_env())

        # Production: Use ResourceHub
        tracer = LangfuseTracer(resource="langfuse:default", tags=["prod"])

        # Use with workflow engine
        result = await engine.run(inputs={...}, tracer=tracer)
        ```
    """

    def __init__(
        self,
        config: Optional["LangfuseConfig"] = None,
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
        """Get LangfuseClient from config or ResourceHub."""
        if self._config is not None:
            from hush.telemetry.backends.langfuse import LangfuseClient

            return LangfuseClient(self._config)

        from hush.core.registry import get_hub

        return get_hub().langfuse(self._resource)

    @staticmethod
    def _resolve_media(
        data: Dict[str, Any],
        media_attachments: Optional[Dict[str, Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        """Resolve media attachments to LangfuseMedia objects."""
        if not media_attachments:
            return data

        try:
            from langfuse.media import LangfuseMedia
        except ImportError:
            return data

        by_location: Dict[str, Dict[str, Any]] = {"input": {}, "output": {}, "metadata": {}}

        for key, attachment in media_attachments.items():
            attach_to = attachment.get("attach_to", "metadata")
            content_type = attachment["content_type"]

            if "base64" in attachment:
                content_bytes = base64.b64decode(attachment["base64"])
            elif "path" in attachment:
                with open(attachment["path"], "rb") as f:
                    content_bytes = f.read()
            else:
                continue

            media_obj = LangfuseMedia(content_bytes=content_bytes, content_type=content_type)
            by_location[attach_to][key] = media_obj

        result = data.copy()

        if by_location["input"]:
            if isinstance(result.get("input"), dict):
                result["input"] = {**result["input"], **by_location["input"]}
            else:
                result["input"] = {"_original": result.get("input"), **by_location["input"]}

        if by_location["output"]:
            if isinstance(result.get("output"), dict):
                result["output"] = {**result["output"], **by_location["output"]}
            else:
                result["output"] = {"_original": result.get("output"), **by_location["output"]}

        if by_location["metadata"]:
            if isinstance(result.get("metadata"), dict):
                result["metadata"] = {**result["metadata"], **by_location["metadata"]}
            else:
                result["metadata"] = by_location["metadata"]

        return result

    def flush(self, trace_data: Dict[str, Any]) -> None:
        """Send trace data to Langfuse.

        Called by FlushWorker in a background thread.

        Args:
            trace_data: {request_id, workflow_name, user_id, session_id,
                        tags, graph_structure, records}
        """
        from hush.core.loggings import LOGGER

        try:
            client = self._get_client()

            workflow_name = trace_data["workflow_name"]
            req_id = trace_data["request_id"]
            user_id = trace_data.get("user_id")
            session_id = trace_data.get("session_id")
            tags = trace_data.get("tags") or []

            # Build structure lookup: op_name → {parent_name, contain_generation, ...}
            structure_map = {s["op_name"]: s for s in trace_data.get("graph_structure", [])}

            LOGGER.info(
                "Workflow: %s, Request ID: %s, Creating Langfuse trace hierarchy...",
                workflow_name,
                req_id,
            )

            langfuse_objects = {}
            root_trace = None

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
                    if context_parent_key in langfuse_objects:
                        parent_key = context_parent_key
                    else:
                        last_dot = context_id.rfind(".")
                        if last_dot > 0:
                            parent_context = context_id[:last_dot]
                            parent_with_parent_ctx = f"{parent_name}:{parent_context}"
                            if parent_with_parent_ctx in langfuse_objects:
                                parent_key = parent_with_parent_ctx

                # Short name for display
                short_name = op_name.rsplit(".", 1)[-1] if "." in op_name else op_name

                if parent_name is None:
                    # Root — create trace
                    clean_tags = [t for t in tags if t is not None] if tags else None
                    root_trace = client.trace(
                        id=req_id,
                        name=workflow_name,
                        user_id=user_id,
                        session_id=session_id,
                        tags=clean_tags if clean_tags else None,
                        start_time=record.get("start_time"),
                        end_time=record.get("end_time"),
                        input=record.get("inputs"),
                        output=record.get("outputs"),
                        metadata=record.get("metadata"),
                    )
                    langfuse_objects[trace_key] = root_trace
                else:
                    # Child — span or generation
                    parent = langfuse_objects.get(parent_key)
                    if parent is None:
                        LOGGER.warning("Parent '%s' not found for op '%s'", parent_key, op_name)
                        continue

                    kwargs = {
                        "name": short_name,
                        "start_time": record.get("start_time"),
                        "end_time": record.get("end_time"),
                        "input": record.get("inputs"),
                        "output": record.get("outputs"),
                        "metadata": record.get("metadata"),
                    }

                    if contain_generation:
                        usage = record.get("usage")
                        if usage:
                            langfuse_usage = {}
                            if "prompt_tokens" in usage:
                                langfuse_usage["input"] = usage["prompt_tokens"]
                            if "completion_tokens" in usage:
                                langfuse_usage["output"] = usage["completion_tokens"]
                            if "total_tokens" in usage:
                                langfuse_usage["total"] = usage["total_tokens"]
                            if langfuse_usage:
                                kwargs["usage"] = langfuse_usage

                        if record.get("model"):
                            kwargs["model"] = record["model"]

                        cost = record.get("cost")
                        if cost and "usage" in kwargs and kwargs["usage"]:
                            kwargs["usage"]["total_cost"] = cost

                        langfuse_objects[trace_key] = parent.generation(**kwargs)
                    else:
                        langfuse_objects[trace_key] = parent.span(**kwargs)

            client.flush()

            trace_url = root_trace.get_trace_url() if root_trace else None
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
        if self._resource:
            return f"<LangfuseTracer resource={self._resource}>"
        return f"<LangfuseTracer host={self._config.host}>"
