"""Langfuse tracer — sends workflow traces via Langfuse public REST API.

No SDK dependency. Builds a batch of ingestion events from the pre-computed
TraceNode tree and POSTs to /api/public/ingestion.

Inherits from hush.core.tracing.Tracer. Flush runs in FlushWorker's
thread pool, never blocking the main async thread.
"""

import uuid
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from hush.telemetry.tracers._base import ConfigurableTracer

if TYPE_CHECKING:
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
    ):
        super().__init__(config=config, resource=resource, tags=tags)

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
            # Assign all children monotonic start_times: parent_start + idx*1ms
            # so Langfuse preserves the execution order from collect_tree
            base_iso = node_start.get(pk) or node.get("start_time") or now_iso
            try:
                base = datetime.fromisoformat(base_iso.replace("Z", "+00:00"))
                bumped = base + timedelta(milliseconds=idx)
                node["start_time"] = bumped.isoformat().replace("+00:00", "Z")
            except (ValueError, TypeError):
                pass

        for node in nodes:
            key = node["trace_key"]
            parent_key = node.get("parent_trace_key")
            node_type = node.get("node_type", "span")
            metadata = dict(node.get("metadata") or {})
            event_id = str(uuid.uuid4())

            if node_type == "trace":
                # Root trace event
                body: Dict[str, Any] = {
                    "id": trace_id,
                    "name": node["display_name"],
                    "input": node.get("inputs") or None,
                    "output": node.get("outputs") or None,
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

            elif node_type == "generation":
                obs_id = str(uuid.uuid4())
                obs_ids[key] = obs_id

                body = {
                    "id": obs_id,
                    "traceId": trace_id,
                    "name": node["display_name"],
                    "startTime": node.get("start_time"),
                    "endTime": node.get("end_time"),
                    "input": node.get("inputs") or None,
                    "output": node.get("outputs") or None,
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
                obs_id = str(uuid.uuid4())
                obs_ids[key] = obs_id

                body = {
                    "id": obs_id,
                    "traceId": trace_id,
                    "name": node["display_name"],
                    "startTime": node.get("start_time"),
                    "endTime": node.get("end_time"),
                    "input": node.get("inputs") or None,
                    "output": node.get("outputs") or None,
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
