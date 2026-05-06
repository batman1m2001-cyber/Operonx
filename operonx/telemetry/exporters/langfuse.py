"""LangfuseTreeExporter — event stream → Langfuse trace + observations.

Consumes the new event-stream format (``TraceEvent``) and renders into the
Langfuse public ingestion API: one ``trace-create`` plus one
``span-create`` / ``generation-create`` per op observed in the stream.

Tree shape is reconstructed from the scheduler's ``ctx`` tuple: ctx
``("main", "a", "[0]")`` is a child of ctx ``("main", "a")``.

Compared to the legacy ``LangfuseTracer`` (which consumed a pre-built
``TraceNode`` tree from ``TraceCollector``), this exporter:

  * works on a flat event stream — no separate collector pass
  * promotes ops to "generation" via observed ``LLM_USAGE`` events,
    not via a baked-in ``node_type``
  * folds annotations into observation metadata
  * folds ``OP_YIELD`` events into the parent observation's metadata
    (yield_count + last_yielded), matching legacy semantics where one
    generator op = one observation, not N item observations
  * uploads ``MediaRef`` blobs carried on OP_START / OP_END events via
    ``client.upload_media`` and substitutes the returned Langfuse token
    at each ref's ``field_path`` in the observation's I/O dict

See ``docs/TRACING_REDESIGN_PLAN.md`` §3.4 + §3.8.
"""

from __future__ import annotations

import base64
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from operonx.core.media import substitute_placeholder
from operonx.core.tracing.events import EventKind, TraceEvent

LOGGER = logging.getLogger("operonx.tracing")


class LangfuseTreeExporter:
    """Default Langfuse exporter — renders the event stream as a tree.

    Construct via either ``resource="langfuse:..."`` (preferred — config from
    ResourceHub) or ``config=LangfuseConfig(...)`` (direct).

    Args:
        resource: ResourceHub key (e.g. ``"langfuse:default"``).
        config:   Direct ``LangfuseConfig`` instance.
        tags:     Static tags attached to every trace this exporter creates.
        workflow_name: Trace name to use. Defaults to ``"operonx"``; a
            higher-priority value can be set via ``metadata["workflow_name"]``
            from the pipeline.
    """

    def __init__(
        self,
        resource: Optional[str] = None,
        config: Optional[Any] = None,
        tags: Optional[List[str]] = None,
        workflow_name: str = "operonx",
    ) -> None:
        if config is None and resource is None:
            raise ValueError("Must provide either 'config' or 'resource'")
        if config is not None and resource is not None:
            raise ValueError("Cannot provide both 'config' and 'resource'")
        self._config = config
        self._resource = resource
        self.tags = list(tags or [])
        self.workflow_name = workflow_name
        self._client_cache: Optional[Any] = None

    # ------------------------------------------------------------------
    # Backend client (lazy — same dispatch as ConfigurableTracer)
    # ------------------------------------------------------------------

    def _get_client(self):
        if self._client_cache is not None:
            return self._client_cache
        if self._config is not None:
            from operonx.telemetry.backends.langfuse import LangfuseClient

            self._client_cache = LangfuseClient(self._config)
        else:
            from operonx.core.registry import ResourceHub

            self._client_cache = ResourceHub.instance().get(self._resource)
        return self._client_cache

    def __repr__(self) -> str:
        if self._resource:
            return f"<LangfuseTreeExporter resource={self._resource}>"
        return f"<LangfuseTreeExporter host={self._config.host}>"

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def export(
        self,
        events: List[TraceEvent],
        request_id: str,
        metadata: Dict[str, Any],
    ) -> None:
        """Render the event stream into Langfuse ingestion events + POST."""
        if not events:
            return

        # 1. Walk events, gather per-op data
        op_data, llm_ops = _gather_op_data(events)
        if not op_data:
            LOGGER.debug("LangfuseTreeExporter: no op_start events; skipping")
            return

        # 2. Build the Langfuse ingestion batch
        trace_id = request_id
        workflow_name = metadata.get("workflow_name") or self.workflow_name
        merged_tags = self._merged_tags(metadata)

        batch = self._build_batch(
            op_data=op_data,
            llm_ops=llm_ops,
            trace_id=trace_id,
            workflow_name=workflow_name,
            metadata=metadata,
            tags=merged_tags,
        )

        # 3. POST
        client = self._get_client()
        result = client.ingest(batch)
        errors = result.get("errors") or []
        if errors:
            msg = (
                f"Langfuse ingestion had {len(errors)} error(s) for "
                f"workflow '{workflow_name}' / trace {trace_id}: {errors[:5]}"
            )
            LOGGER.error(msg)
            raise RuntimeError(msg)
        successes = result.get("successes") or []
        LOGGER.info(
            "Workflow: %s, Request ID: %s, Langfuse trace created (%d events). View: %s",
            workflow_name,
            trace_id,
            len(successes),
            client.trace_url(trace_id),
        )

    # ------------------------------------------------------------------
    # Build helpers
    # ------------------------------------------------------------------

    def _merged_tags(self, metadata: Dict[str, Any]) -> List[str]:
        """Merge static tracer tags + dynamic per-call tags from metadata."""
        out: List[str] = list(self.tags)
        for t in metadata.get("tags") or ():
            if t and t not in out:
                out.append(t)
        return out

    def _build_batch(
        self,
        *,
        op_data: Dict[Tuple[str, Tuple[str, ...]], Dict[str, Any]],
        llm_ops: set,
        trace_id: str,
        workflow_name: str,
        metadata: Dict[str, Any],
        tags: List[str],
    ) -> List[Dict[str, Any]]:
        """Assemble the trace-create + observation-create events."""
        now_iso = _utc_iso(datetime.now(timezone.utc))
        client = self._get_client()

        # Assign one observation UUID per op key. Need to do this BEFORE the
        # per-op loop so parent linking can resolve forward references.
        obs_ids: Dict[Tuple[str, Tuple[str, ...]], str] = {}
        for key in op_data:
            obs_ids[key] = str(uuid.uuid4())

        # Earliest start_time across all ops — used as the trace timestamp.
        first_start = min(
            (d["start"].timestamp for d in op_data.values() if d["start"]),
            default=None,
        )
        trace_ts = _utc_iso(first_start) if first_start else now_iso

        batch: List[Dict[str, Any]] = []

        # 1. trace-create
        trace_body: Dict[str, Any] = {
            "id": trace_id,
            "name": workflow_name,
            "timestamp": trace_ts,
            "environment": "default",
        }
        if metadata.get("user_id"):
            trace_body["userId"] = metadata["user_id"]
        if metadata.get("session_id"):
            trace_body["sessionId"] = metadata["session_id"]
        if tags:
            trace_body["tags"] = tags
        if metadata.get("workflow_inputs"):
            trace_body["input"] = metadata["workflow_inputs"]
        batch.append(
            {
                "id": str(uuid.uuid4()),
                "type": "trace-create",
                "timestamp": trace_ts,
                "body": trace_body,
            }
        )

        # 2. one observation per op
        for key, data in op_data.items():
            start_event: Optional[TraceEvent] = data["start"]
            end_event: Optional[TraceEvent] = data["end"]
            if start_event is None:
                continue  # cancel-before-start: nothing to render

            op_name, ctx = key
            short = _short_name(op_name)
            is_generation = key in llm_ops

            obs_id = obs_ids[key]
            parent_id = _resolve_parent(ctx, op_data, obs_ids)

            start_ts = _utc_iso(start_event.timestamp)
            end_ts = _utc_iso(end_event.timestamp) if end_event else None

            inputs = (start_event.payload or {}).get("inputs")
            outputs = (end_event.payload or {}).get("outputs") if end_event else None

            obs_metadata: Dict[str, Any] = {}
            yield_count = (end_event.payload or {}).get("yield_count") if end_event else 0
            if yield_count:
                obs_metadata["yield_count"] = yield_count
            if data["yields"]:
                obs_metadata["last_yielded"] = data["yields"][-1].payload.get("yielded")
            for ann in data["annotations"]:
                k = ann.payload.get("key")
                v = ann.payload.get("value")
                if k is not None:
                    obs_metadata[str(k)] = v
            status = (end_event.payload or {}).get("status") if end_event else None
            if status and status != "ok":
                obs_metadata["status"] = status

            body: Dict[str, Any] = {
                "id": obs_id,
                "traceId": trace_id,
                "name": short,
                "startTime": start_ts,
            }
            if end_ts:
                body["endTime"] = end_ts
            if inputs:
                body["input"] = inputs
            if outputs:
                body["output"] = outputs
            if obs_metadata:
                body["metadata"] = obs_metadata
            if parent_id:
                body["parentObservationId"] = parent_id
            if status == "error":
                body["level"] = "ERROR"
            elif status == "cancelled":
                body["level"] = "WARNING"

            # Media: upload blobs carried on start/end events, substitute
            # Langfuse tokens at each ref's field_path in body['input']/['output'].
            _apply_media_refs(
                body,
                [start_event, end_event],
                client=client,
                trace_id=trace_id,
                observation_id=obs_id,
            )

            event_type = "generation-create" if is_generation else "span-create"

            if is_generation:
                usage_event: Optional[TraceEvent] = data["usage"]
                if usage_event:
                    up = usage_event.payload or {}
                    if up.get("model"):
                        body["model"] = up["model"]
                    usage_details: Dict[str, Any] = {}
                    if "prompt_tokens" in up:
                        usage_details["input"] = up["prompt_tokens"]
                    if "completion_tokens" in up:
                        usage_details["output"] = up["completion_tokens"]
                    if "total_tokens" in up:
                        usage_details["total"] = up["total_tokens"]
                    if usage_details:
                        body["usageDetails"] = usage_details
                    if up.get("cost_usd"):
                        body["costDetails"] = {"total": up["cost_usd"]}

            batch.append(
                {
                    "id": str(uuid.uuid4()),
                    "type": event_type,
                    "timestamp": start_ts,
                    "body": body,
                }
            )

        return batch


# ---------------------------------------------------------------------------
# Module helpers (exported for the GroupedTimelineExporter to share)
# ---------------------------------------------------------------------------


def _gather_op_data(
    events: List[TraceEvent],
) -> Tuple[Dict[Tuple[str, Tuple[str, ...]], Dict[str, Any]], set]:
    """Walk events once, collect per-op lifecycle data + identify generations.

    Returns:
        (op_data, llm_ops) where op_data maps ``(op_name, ctx)`` to a dict
        with keys ``start``, ``end``, ``yields``, ``usage``, ``annotations``;
        and ``llm_ops`` is the set of keys that emitted at least one
        ``LLM_USAGE`` event (renders as a Langfuse generation).
    """
    op_data: Dict[Tuple[str, Tuple[str, ...]], Dict[str, Any]] = {}
    llm_ops: set = set()

    for e in events:
        if e.kind is EventKind.OP_START:
            key = (e.op_name, e.ctx)
            op_data.setdefault(key, _new_op_record())
            op_data[key]["start"] = e
        elif e.kind is EventKind.OP_END:
            key = (e.op_name, e.ctx)
            op_data.setdefault(key, _new_op_record())
            op_data[key]["end"] = e
        elif e.kind is EventKind.OP_YIELD:
            # Yield ctx is base + "[N]" — find the parent op (drops suffix)
            parent_ctx = e.ctx[:-1] if e.ctx and e.ctx[-1].startswith("[") else e.ctx
            key = (e.op_name, parent_ctx)
            op_data.setdefault(key, _new_op_record())
            op_data[key]["yields"].append(e)
        elif e.kind is EventKind.LLM_USAGE:
            key = (e.op_name, e.ctx)
            op_data.setdefault(key, _new_op_record())
            op_data[key]["usage"] = e
            llm_ops.add(key)
        elif e.kind is EventKind.ANNOTATION and e.op_name is not None:
            key = (e.op_name, e.ctx)
            op_data.setdefault(key, _new_op_record())
            op_data[key]["annotations"].append(e)

    return op_data, llm_ops


def _new_op_record() -> Dict[str, Any]:
    return {
        "start": None,
        "end": None,
        "yields": [],
        "usage": None,
        "annotations": [],
    }


def _resolve_parent(
    ctx: Tuple[str, ...],
    op_data: Dict[Tuple[str, Tuple[str, ...]], Dict[str, Any]],
    obs_ids: Dict[Tuple[str, Tuple[str, ...]], str],
) -> Optional[str]:
    """Walk ``ctx`` prefixes looking for an op whose ctx is a strict prefix.

    Returns the closest ancestor's observation id, or ``None`` if the op is
    a direct child of the trace (root).
    """
    for n in range(len(ctx) - 1, -1, -1):
        prefix = ctx[:n]
        for (op_name, c), _data in op_data.items():
            if c == prefix and (op_name, c) in obs_ids:
                return obs_ids[(op_name, c)]
    return None


def _short_name(op_name: Optional[str]) -> str:
    """``"g.add_one"`` → ``"add_one"`` for display in Langfuse UI."""
    if not op_name:
        return "?"
    return op_name.rsplit(".", 1)[-1]


def _utc_iso(dt: datetime) -> str:
    """ISO-8601 with trailing 'Z' (Langfuse expects UTC suffix)."""
    return dt.isoformat().replace("+00:00", "Z")


# ===========================================================================
# LangfuseGroupedTimelineExporter — the educa shape
# ===========================================================================


class LangfuseGroupedTimelineExporter(LangfuseTreeExporter):
    """Renders the event stream as a flat per-turn timeline.

    ``GROUP_START`` / ``GROUP_END`` events become synthetic parent spans
    (one per "turn" or whatever the user grouped on). Op observations inside
    a group attach to that synthetic parent instead of the trace root, so
    the Langfuse UI shows ``trace -> turn-0 -> [a, b, c]`` rather than a
    flat fan-out under the trace.

    Annotations whose ``op_name`` is ``None`` (synthesized by ``Aggregate``)
    land in the enclosing group's ``metadata``, where they're visible as
    e.g. ``summary:audio: {chunk_count: 1500, avg_ms: 20}`` in the UI.

    Used by educa Phase 2: ``GroupBy`` + ``Aggregate`` produce the markers
    + summary annotations; this exporter renders them as a tidy turn-grouped
    timeline.
    """

    def export(
        self,
        events: List[TraceEvent],
        request_id: str,
        metadata: Dict[str, Any],
    ) -> None:
        if not events:
            return

        # Walk events once, track which group is active per op_start +
        # collect group lifecycles + group-scope annotations.
        op_data, llm_ops = _gather_op_data(events)
        groups, op_to_group = _gather_groups(events)

        if not op_data and not groups:
            LOGGER.debug("LangfuseGroupedTimelineExporter: no ops or groups; skipping")
            return

        trace_id = request_id
        workflow_name = metadata.get("workflow_name") or self.workflow_name
        merged_tags = self._merged_tags(metadata)

        batch = self._build_batch_grouped(
            op_data=op_data,
            llm_ops=llm_ops,
            groups=groups,
            op_to_group=op_to_group,
            trace_id=trace_id,
            workflow_name=workflow_name,
            metadata=metadata,
            tags=merged_tags,
        )

        client = self._get_client()
        result = client.ingest(batch)
        errors = result.get("errors") or []
        if errors:
            msg = (
                f"Langfuse ingestion had {len(errors)} error(s) for "
                f"workflow '{workflow_name}' / trace {trace_id}: {errors[:5]}"
            )
            LOGGER.error(msg)
            raise RuntimeError(msg)
        successes = result.get("successes") or []
        LOGGER.info(
            "Workflow: %s, Request ID: %s, Langfuse grouped trace created (%d events). View: %s",
            workflow_name,
            trace_id,
            len(successes),
            client.trace_url(trace_id),
        )

    # ------------------------------------------------------------------

    def _build_batch_grouped(
        self,
        *,
        op_data: Dict[Tuple[str, Tuple[str, ...]], Dict[str, Any]],
        llm_ops: set,
        groups: List[Dict[str, Any]],
        op_to_group: Dict[Tuple[str, Tuple[str, ...]], str],
        trace_id: str,
        workflow_name: str,
        metadata: Dict[str, Any],
        tags: List[str],
    ) -> List[Dict[str, Any]]:
        now_iso = _utc_iso(datetime.now(timezone.utc))
        client = self._get_client()

        # UUIDs for each observation — assigned upfront so parent linking
        # can resolve forward references.
        obs_ids: Dict[Tuple[str, Tuple[str, ...]], str] = {
            key: str(uuid.uuid4()) for key in op_data
        }
        group_obs_ids: Dict[str, str] = {g["key"]: str(uuid.uuid4()) for g in groups}

        # Earliest start across both ops and groups → trace timestamp
        all_starts: List[datetime] = []
        for d in op_data.values():
            if d["start"]:
                all_starts.append(d["start"].timestamp)
        for g in groups:
            all_starts.append(g["start"].timestamp)
        first_start = min(all_starts) if all_starts else None
        trace_ts = _utc_iso(first_start) if first_start else now_iso

        batch: List[Dict[str, Any]] = []

        # 1. trace-create
        trace_body: Dict[str, Any] = {
            "id": trace_id,
            "name": workflow_name,
            "timestamp": trace_ts,
            "environment": "default",
        }
        if metadata.get("user_id"):
            trace_body["userId"] = metadata["user_id"]
        if metadata.get("session_id"):
            trace_body["sessionId"] = metadata["session_id"]
        if tags:
            trace_body["tags"] = tags
        batch.append(
            {
                "id": str(uuid.uuid4()),
                "type": "trace-create",
                "timestamp": trace_ts,
                "body": trace_body,
            }
        )

        # 2. one synthetic span per group
        for g in groups:
            group_id = group_obs_ids[g["key"]]
            start_ts = _utc_iso(g["start"].timestamp)
            end_ts = _utc_iso(g["end"].timestamp) if g["end"] else None
            body: Dict[str, Any] = {
                "id": group_id,
                "traceId": trace_id,
                "name": g["name"],
                "startTime": start_ts,
            }
            if end_ts:
                body["endTime"] = end_ts
            if g["annotations"]:
                # Annotations with op_name=None (Aggregate summaries etc.)
                # land on the group's metadata, visible in the UI.
                meta: Dict[str, Any] = {}
                for ann in g["annotations"]:
                    k = ann.payload.get("key")
                    v = ann.payload.get("value")
                    if k is not None:
                        meta[str(k)] = v
                if meta:
                    body["metadata"] = meta
            if g["end"] and (g["end"].payload or {}).get("status") == "truncated":
                body["level"] = "WARNING"
            batch.append(
                {
                    "id": str(uuid.uuid4()),
                    "type": "span-create",
                    "timestamp": start_ts,
                    "body": body,
                }
            )

        # 3. op observations — parent = active group OR trace
        for key, data in op_data.items():
            start_event = data["start"]
            end_event = data["end"]
            if start_event is None:
                continue

            op_name = key[0]
            short = _short_name(op_name)
            is_generation = key in llm_ops

            obs_id = obs_ids[key]
            # Parent: synthetic group if inside one; else trace root (None)
            parent_id = group_obs_ids.get(op_to_group.get(key) or "")

            start_ts = _utc_iso(start_event.timestamp)
            end_ts = _utc_iso(end_event.timestamp) if end_event else None
            inputs = (start_event.payload or {}).get("inputs")
            outputs = (end_event.payload or {}).get("outputs") if end_event else None

            obs_metadata: Dict[str, Any] = {}
            yield_count = (end_event.payload or {}).get("yield_count") if end_event else 0
            if yield_count:
                obs_metadata["yield_count"] = yield_count
            if data["yields"]:
                obs_metadata["last_yielded"] = data["yields"][-1].payload.get("yielded")
            for ann in data["annotations"]:
                k = ann.payload.get("key")
                v = ann.payload.get("value")
                if k is not None:
                    obs_metadata[str(k)] = v
            status = (end_event.payload or {}).get("status") if end_event else None
            if status and status != "ok":
                obs_metadata["status"] = status

            body = {
                "id": obs_id,
                "traceId": trace_id,
                "name": short,
                "startTime": start_ts,
            }
            if end_ts:
                body["endTime"] = end_ts
            if inputs:
                body["input"] = inputs
            if outputs:
                body["output"] = outputs
            if obs_metadata:
                body["metadata"] = obs_metadata
            if parent_id:
                body["parentObservationId"] = parent_id
            if status == "error":
                body["level"] = "ERROR"
            elif status == "cancelled":
                body["level"] = "WARNING"

            _apply_media_refs(
                body,
                [start_event, end_event],
                client=client,
                trace_id=trace_id,
                observation_id=obs_id,
            )

            event_type = "generation-create" if is_generation else "span-create"

            if is_generation:
                usage_event = data["usage"]
                if usage_event:
                    up = usage_event.payload or {}
                    if up.get("model"):
                        body["model"] = up["model"]
                    usage_details: Dict[str, Any] = {}
                    if "prompt_tokens" in up:
                        usage_details["input"] = up["prompt_tokens"]
                    if "completion_tokens" in up:
                        usage_details["output"] = up["completion_tokens"]
                    if "total_tokens" in up:
                        usage_details["total"] = up["total_tokens"]
                    if usage_details:
                        body["usageDetails"] = usage_details
                    if up.get("cost_usd"):
                        body["costDetails"] = {"total": up["cost_usd"]}

            batch.append(
                {
                    "id": str(uuid.uuid4()),
                    "type": event_type,
                    "timestamp": start_ts,
                    "body": body,
                }
            )

        return batch


def _gather_groups(
    events: List[TraceEvent],
) -> Tuple[List[Dict[str, Any]], Dict[Tuple[str, Tuple[str, ...]], str]]:
    """Walk the stream once, pair GROUP_START/GROUP_END markers + track which
    group is active when each op starts.

    Each group gets a synthetic key (str): the GROUP_START's event_id. Op
    keys map to that synthetic key for parent-id resolution.

    Returns:
        (groups, op_to_group) where ``groups`` is an ordered list of
        ``{key, name, start, end, annotations}`` records (annotations are
        ANNOTATION events with op_name=None — synthesized by Aggregate),
        and ``op_to_group`` maps ``(op_name, ctx) -> group_key`` for ops
        that started inside a group.
    """
    groups: List[Dict[str, Any]] = []
    op_to_group: Dict[Tuple[str, Tuple[str, ...]], str] = {}

    active_key: Optional[str] = None
    active_record: Optional[Dict[str, Any]] = None

    for e in events:
        if e.kind is EventKind.GROUP_START:
            active_key = e.event_id
            active_record = {
                "key": active_key,
                "name": (e.payload or {}).get("name") or active_key,
                "start": e,
                "end": None,
                "annotations": [],
            }
            groups.append(active_record)
        elif e.kind is EventKind.GROUP_END:
            if active_record is not None:
                active_record["end"] = e
            active_key = None
            active_record = None
        elif e.kind is EventKind.OP_START and active_key is not None:
            op_to_group[(e.op_name, e.ctx)] = active_key
        elif e.kind is EventKind.ANNOTATION and e.op_name is None and active_record is not None:
            # op_name=None means it was synthesized by a processor (Aggregate).
            # Belongs on the group, not on any specific op.
            active_record["annotations"].append(e)

    return groups, op_to_group


# ---------------------------------------------------------------------------
# Media handling — port of legacy ``_upload_node_media`` (§3.8)
# ---------------------------------------------------------------------------


def _apply_media_refs(
    body: Dict[str, Any],
    events: List[Optional[TraceEvent]],
    *,
    client,
    trace_id: str,
    observation_id: Optional[str],
) -> None:
    """Upload each MediaRef carried on the given events; substitute Langfuse
    tokens at each ref's ``field_path`` inside ``body['input']`` /
    ``body['output']`` (mutates in place).

    Mirrors the legacy ``LangfuseTracer._upload_node_media`` semantics: raw
    bytes upload via ``client.upload_media``; ``data:<mime>;base64,...`` URIs
    decode then upload; URLs / file paths / failed uploads leave a readable
    fallback string at the placeholder.
    """
    for event in events:
        if event is None:
            continue
        refs = (event.payload or {}).get("media_refs") or []
        for ref in refs:
            _apply_one_media_ref(
                body,
                ref,
                client=client,
                trace_id=trace_id,
                observation_id=observation_id,
            )


def _apply_one_media_ref(
    body: Dict[str, Any],
    ref,
    *,
    client,
    trace_id: str,
    observation_id: Optional[str],
) -> None:
    """Upload one ``MediaRef`` (or dict shaped like one) and substitute its
    placeholder in ``body``."""
    # Support both MediaRef dataclass + dict (in case events were JSON-roundtripped).
    if isinstance(ref, dict):
        field_path = ref.get("field_path")
        mime_type = ref.get("mime_type")
        data = ref.get("data")
        size_bytes = ref.get("size_bytes", 0)
    else:
        field_path = ref.field_path
        mime_type = ref.mime_type
        data = ref.data
        size_bytes = ref.size_bytes

    if not field_path:
        return

    content: Optional[bytes] = None
    if isinstance(data, (bytes, bytearray)):
        content = bytes(data)
    elif isinstance(data, str) and data.startswith("data:") and ";base64," in data:
        try:
            content = base64.b64decode(data.split(",", 1)[1])
        except Exception:
            content = None

    if content is None:
        # URL / file path / non-base64 string — leave a readable fallback.
        fallback = f"[media: {mime_type}, ref={str(data)[:80]!r}]"
        _substitute_into_body(body, field_path, fallback)
        return

    root, _, _ = field_path.partition(".")
    lf_field = "input" if root == "inputs" else "output"

    try:
        token = client.upload_media(
            trace_id=trace_id,
            field=lf_field,
            content_type=mime_type,
            content=content,
            observation_id=observation_id,
        )
    except Exception as e:
        LOGGER.warning("Langfuse media upload raised: %s", e)
        token = None

    if token is None:
        fallback = f"[media upload failed: {mime_type}, {size_bytes}B]"
        _substitute_into_body(body, field_path, fallback)
    else:
        _substitute_into_body(body, field_path, token)


def _substitute_into_body(
    body: Dict[str, Any],
    field_path: str,
    replacement: Any,
) -> None:
    """Walk ``body['input']`` or ``body['output']`` along ``field_path`` and
    swap the leaf value. ``field_path`` always begins with ``inputs.`` or
    ``outputs.``; that segment selects the side dict, the rest names the
    nested location (delegated to ``substitute_placeholder``)."""
    root, _, _ = field_path.partition(".")
    if root == "inputs":
        side = body.get("input")
    elif root == "outputs":
        side = body.get("output")
    else:
        return
    if side is None:
        return
    substitute_placeholder(side, field_path, replacement)
