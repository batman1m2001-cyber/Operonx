"""Langfuse tracer — sends workflow traces via Langfuse public REST API.

No SDK dependency. Builds a batch of ingestion events from the pre-computed
TraceNode tree and POSTs to /api/public/ingestion.

Inherits from hush.core.tracing.Tracer. Flush runs in FlushWorker's
thread pool, never blocking the main async thread.
"""

import uuid
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from hush.core.media import substitute_placeholder
from hush.telemetry.tracers._base import ConfigurableTracer


def _substitute_for_field(
    inputs: Optional[Dict[str, Any]],
    outputs: Optional[Dict[str, Any]],
    field_path: str,
    replacement: Any,
) -> None:
    """Route a ``substitute_placeholder`` call to inputs or outputs by root segment."""
    root, _, _ = field_path.partition(".")
    target = inputs if root == "inputs" else outputs
    if target is not None:
        substitute_placeholder(target, field_path, replacement)


if TYPE_CHECKING:
    from hush.core.tracing.trace_filter import TraceFilter
    from hush.telemetry.backends.langfuse import LangfuseConfig


class LangfuseTracer(ConfigurableTracer):
    """Tracer that sends workflow traces to Langfuse via public REST API.

    Example:
        ```python
        from hush.telemetry import LangfuseTracer, LangfuseConfig

        # Simple: Direct config
        tracer = LangfuseTracer(config=LangfuseConfig.from_env())

        # Production: Use ResourceHub
        tracer = LangfuseTracer(resource="langfuse:default", tags=["prod"])

        # Use with workflow engine
        engine = Hush(graph, tracer=tracer)
        result = await engine.run(inputs={...})
        ```
    """

    def __init__(
        self,
        config: Optional["LangfuseConfig"] = None,
        resource: Optional[str] = None,
        tags: Optional[List[str]] = None,
        trace_filter: Optional["TraceFilter"] = None,
    ):
        super().__init__(config=config, resource=resource, tags=tags, trace_filter=trace_filter)

    def _make_client(self, config):
        from hush.telemetry.backends.langfuse import LangfuseClient

        return LangfuseClient(config)

    def to_config_dict(self) -> Optional[Dict[str, Any]]:
        """Return Langfuse config for the Rust backend. None if resource-based."""
        if self._config is None:
            return None
        d: Dict[str, Any] = {
            "public_key": self._config.public_key,
            "secret_key": self._config.secret_key,
            "host": self._config.host,
        }
        if self._stream_trace_limit is not None:
            d["stream_trace_limit"] = self._stream_trace_limit
        return d

    @staticmethod
    def _set_parent(
        body: Dict[str, Any],
        parent_key: Optional[str],
        obs_ids: Dict[str, str],
        trace_id: str,
    ) -> None:
        """Set parentObservationId on *body* when this node is not a direct trace child."""
        if parent_key and parent_key in obs_ids and obs_ids[parent_key] != trace_id:
            body["parentObservationId"] = obs_ids[parent_key]

    def _upload_node_media(
        self,
        client,
        node: Dict[str, Any],
        trace_id: str,
        observation_id: Optional[str],
    ) -> tuple:
        """Upload every ``MediaRef`` on a node and substitute placeholders.

        Mutates ``node["inputs"]`` / ``node["outputs"]`` in place, replacing
        each ``<media:N>`` placeholder at the ``field_path`` with the returned
        Langfuse reference token. Failures are logged and leave a human-readable
        fallback string so the trace is still interpretable.
        """
        media_list = node.get("media") or []
        if not media_list:
            return node.get("inputs"), node.get("outputs")

        inputs = node.get("inputs") or {}
        outputs = node.get("outputs") or {}

        for ref in media_list:
            # ref is a MediaRef dataclass at this point (pre-asdict) OR a
            # plain dict (post-asdict). Support both for robustness.
            field_path = ref.get("field_path") if isinstance(ref, dict) else ref.field_path
            mime_type = ref.get("mime_type") if isinstance(ref, dict) else ref.mime_type
            data = ref.get("data") if isinstance(ref, dict) else ref.data
            size_bytes = ref.get("size_bytes") if isinstance(ref, dict) else ref.size_bytes

            content: Optional[bytes] = None
            if isinstance(data, bytes):
                content = data
            elif isinstance(data, str) and data.startswith("data:") and ";base64," in data:
                # data:<mime>;base64,<payload> — decode and upload so Langfuse
                # renders a native preview instead of a wall of base64.
                import base64

                try:
                    content = base64.b64decode(data.split(",", 1)[1])
                except Exception:
                    content = None
            if content is None:
                # URL, file path, or non-base64 string — can't upload raw
                # bytes, so leave a descriptive placeholder.
                fallback = f"[media: {mime_type}, ref={str(data)[:80]!r}]"
                _substitute_for_field(inputs, outputs, field_path, fallback)
                continue

            root, _, _ = field_path.partition(".")
            lf_field = "input" if root == "inputs" else "output"

            token = client.upload_media(
                trace_id=trace_id,
                field=lf_field,
                content_type=mime_type,
                content=content,
                observation_id=observation_id,
            )
            if token is None:
                fallback = f"[media upload failed: {mime_type}, {size_bytes}B]"
                _substitute_for_field(inputs, outputs, field_path, fallback)
            else:
                _substitute_for_field(inputs, outputs, field_path, token)

        return inputs or None, outputs or None

    def flush(self, trace_data: Dict[str, Any]) -> None:
        """Send trace data to Langfuse via batch ingestion API.

        Builds a list of trace-create/span-create/generation-create events
        from the pre-computed TraceNode tree and sends them in one batch.

        Args:
            trace_data: {request_id, workflow_name, user_id, session_id,
                        tags, nodes}
        """
        from hush.core.loggings import LOGGER

        client = self._get_client()

        workflow_name = trace_data["workflow_name"]
        trace_id = trace_data["request_id"]
        user_id = trace_data.get("user_id")
        session_id = trace_data.get("session_id")
        tags = trace_data.get("tags") or []
        clean_tags = [t for t in tags if t is not None] if tags else None

        LOGGER.info(
            "Workflow: %s, Request ID: %s, Creating Langfuse trace hierarchy...",
            workflow_name,
            trace_id,
        )

        # Build batch events from nodes
        batch: List[Dict[str, Any]] = []
        # Map trace_key -> observation UUID (for parentObservationId linking)
        obs_ids: Dict[str, str] = {}
        # Fallback timestamp for nodes without start_time (e.g. synthetic context nodes)
        # Langfuse expects UTC timestamps ending with "Z", not "+00:00"
        now_iso = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

        # Pre-process: assign monotonically increasing start times per parent.
        # Langfuse sorts children by startTime. The nodes list is already in
        # execution order from collect_tree(), so we just assign each sibling
        # start_time = parent_start + (child_index * 1ms) to preserve ordering.
        nodes = list(trace_data.get("nodes", []))
        # Collect parent start times first
        node_start: Dict[str, str] = {}
        for node in nodes:
            st = node.get("start_time")
            if st:
                node_start[node["trace_key"]] = st

        parent_child_count: Dict[Optional[str], int] = {}
        for node in nodes:
            pk = node.get("parent_trace_key")
            if pk is None:
                continue
            idx = parent_child_count.get(pk, 0)
            parent_child_count[pk] = idx + 1
            # Add a small monotonic offset (0.1ms per sibling) to preserve
            # execution order in Langfuse UI. Uses the node's own start_time
            # as base (preserving real timing), or falls back to parent's.
            base_iso = node.get("start_time") or node_start.get(pk) or now_iso
            try:
                base = datetime.fromisoformat(base_iso.replace("Z", "+00:00"))
                bumped = base + timedelta(milliseconds=idx * 0.1)
                node["start_time"] = bumped.isoformat().replace("+00:00", "Z")
            except (ValueError, TypeError):
                pass

        for node in nodes:
            key = node["trace_key"]
            parent_key = node.get("parent_trace_key")
            node_type = node.get("node_type", "span")
            metadata = dict(node.get("metadata") or {})
            event_id = str(uuid.uuid4())

            # Pre-assign observation ID so media upload can reference it.
            if node_type == "trace":
                node_obs_id = trace_id
            else:
                node_obs_id = str(uuid.uuid4())
                obs_ids[key] = node_obs_id

            # Upload media blobs (if any) and substitute reference tokens
            # back into the node's inputs/outputs before building the body.
            body_inputs, body_outputs = self._upload_node_media(
                client,
                node,
                trace_id=trace_id,
                observation_id=None if node_type == "trace" else node_obs_id,
            )

            if node_type == "trace":
                # Root trace event
                body: Dict[str, Any] = {
                    "id": trace_id,
                    "name": node["display_name"],
                    "input": body_inputs,
                    "output": body_outputs,
                    "metadata": metadata or None,
                    "tags": clean_tags if clean_tags else None,
                    "environment": "default",
                }
                if user_id:
                    body["userId"] = user_id
                if session_id:
                    body["sessionId"] = session_id
                if node.get("start_time"):
                    body["timestamp"] = node["start_time"]

                batch.append(
                    {
                        "id": event_id,
                        "type": "trace-create",
                        "timestamp": node.get("start_time") or now_iso,
                        "body": body,
                    }
                )
                obs_ids[key] = trace_id  # trace's "id" is used as traceId
                # (already set via node_obs_id above; kept for clarity)

            elif node_type == "generation":
                body = {
                    "id": node_obs_id,
                    "traceId": trace_id,
                    "name": node["display_name"],
                    "startTime": node.get("start_time"),
                    "endTime": node.get("end_time"),
                    "input": body_inputs,
                    "output": body_outputs,
                    "metadata": metadata or None,
                }

                # Parent observation (if not direct child of trace)
                self._set_parent(body, parent_key, obs_ids, trace_id)

                # LLM-specific fields
                if node.get("model"):
                    body["model"] = node["model"]
                usage = node.get("usage")
                if usage:
                    usage_details = {}
                    if "prompt_tokens" in usage:
                        usage_details["input"] = usage["prompt_tokens"]
                    if "completion_tokens" in usage:
                        usage_details["output"] = usage["completion_tokens"]
                    if "total_tokens" in usage:
                        usage_details["total"] = usage["total_tokens"]
                    if usage_details:
                        body["usageDetails"] = usage_details
                cost = node.get("cost")
                if cost is not None:
                    body["costDetails"] = {"total": cost}

                batch.append(
                    {
                        "id": event_id,
                        "type": "generation-create",
                        "timestamp": node.get("start_time") or now_iso,
                        "body": body,
                    }
                )

            else:
                # Span (batch, generator, loop_iter, graph)
                body = {
                    "id": node_obs_id,
                    "traceId": trace_id,
                    "name": node["display_name"],
                    "startTime": node.get("start_time"),
                    "endTime": node.get("end_time"),
                    "input": body_inputs,
                    "output": body_outputs,
                    "metadata": metadata or None,
                }

                self._set_parent(body, parent_key, obs_ids, trace_id)

                batch.append(
                    {
                        "id": event_id,
                        "type": "span-create",
                        "timestamp": node.get("start_time") or now_iso,
                        "body": body,
                    }
                )

        # Send batch
        result = client.ingest(batch)

        # Check for errors — raise so FlushWorker surfaces them
        errors = result.get("errors", [])
        if errors:
            msg = (
                f"Langfuse ingestion had {len(errors)} error(s) for "
                f"workflow '{workflow_name}': {errors[:5]}"
            )
            LOGGER.error(msg)
            raise RuntimeError(msg)

        successes = result.get("successes", [])
        trace_url = client.trace_url(trace_id)
        LOGGER.info(
            "Workflow: %s, Request ID: %s, Langfuse trace created (%d events). View: %s",
            workflow_name,
            trace_id,
            len(successes),
            trace_url,
        )

    def __repr__(self) -> str:
        if self._resource:
            return f"<LangfuseTracer resource={self._resource}>"
        host = self._config.host if self._config else "?"
        return f"<LangfuseTracer host={host}>"
