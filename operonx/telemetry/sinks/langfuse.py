"""LangfuseSink — V2 tracing sink for Langfuse.

Renders the ``TraceEvent`` stream into Langfuse's ingestion API. Two
distinct renderings depending on emit shape:

- ``span(path, input=..., output=...)`` — paired atomic call →
  Langfuse **SPAN** with both ``input`` and ``output`` fields set. Best
  fit for sync-shaped ops (``a+b→c``).
- ``event(path, data, kind=...)`` — individual timeline point → Langfuse
  **EVENT** under the path's container span. ``kind="input"`` fills the
  event's ``input`` field; ``kind="output"`` fills its ``output``;
  ``kind="log"`` fills ``metadata``; ``kind="error"`` fills ``output`` +
  ``level=ERROR``. Best fit for streaming, M-in / N-out, mid-op notes.

Path segments always create nested container spans (``speech/stt`` →
``speech`` > ``stt``). The leaf span is either upgraded to a call span
(``span``) or holds a stream of events (``event``).

Media policy: auto-detect ``bytes`` (> 4 KB) and numpy arrays; upload via
``client.upload_media()`` and substitute the returned reference token.
"""

from __future__ import annotations

import logging
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from operonx.core.trace import TraceEvent
from operonx.telemetry.backends.langfuse.client import LangfuseClient
from operonx.telemetry.backends.langfuse.config import LangfuseConfig

LOGGER = logging.getLogger("operonx.tracing.langfuse_sink")

# Auto-detect binary size threshold for media upload
_MEDIA_BYTE_THRESHOLD = 4096

# Reasonable default MIME for opaque bytes when we can't sniff
_DEFAULT_MIME = "application/octet-stream"


def _event_name(kind: str, data: Dict[str, Any]) -> str:
    """Build a distinguishing name for a Langfuse timeline event.

    Includes the primary data key so M events at the same path
    (e.g. many ``"partial"`` outputs) can be told apart in the UI.
    Falls back to the kind alone when data is empty.
    """
    if not data:
        return kind
    first_key = next(iter(data))
    return f"{kind}:{first_key}"


class LangfuseSink:
    """Sink that converts TraceEvent stream to Langfuse ingestion batches.

    Usage::

        from operonx.telemetry.sinks import LangfuseSink
        from operonx.core.registry import ResourceHub

        sink = LangfuseSink(client=ResourceHub.instance().get("langfuse:edupia"))

        await engine.run(inputs=..., sink=sink, trace_id="call_c1")
        sink.flush("call_c1")   # flush at end of trace

    Or without ResourceHub::

        from operonx.telemetry.backends.langfuse import LangfuseClient, LangfuseConfig

        client = LangfuseClient(LangfuseConfig(
            public_key="pk-...",
            secret_key="sk-...",
            host="https://cloud.langfuse.com",
        ))
        sink = LangfuseSink(client=client)

    Args:
        client:      A ``LangfuseClient`` instance (raw HTTP wrapper).
        workflow_name: Default trace name if not set per-trace.
        upload_media: If True, auto-upload binary values as media refs.
    """

    def __init__(
        self,
        client: LangfuseClient,
        *,
        workflow_name: str = "operonx",
        upload_media: bool = True,
    ) -> None:
        self._client = client
        self._workflow_name = workflow_name
        self._upload_media_enabled = upload_media

        # State per trace_id. Keys are (trace_id, path, ctx) where ctx
        # is None for intermediate container spans (shared across ctx)
        # and set to the emit's ctx string on the leaf span (distinct
        # per ctx → different Langfuse span for each invocation).
        self._traces_created: set[str] = set()
        # (trace_id, path, ctx) → langfuse observation_id
        self._obs_ids: Dict[Tuple[str, str, Optional[str]], str] = {}
        # (trace_id, path, ctx) → last known end_time (perf_counter)
        self._last_time: Dict[Tuple[str, str, Optional[str]], float] = {}
        # trace_id → wallclock start reference (used to convert perf_counter → wallclock ISO)
        self._trace_epoch: Dict[str, Tuple[float, float]] = {}  # trace_id → (perf_start, wall_start)
        # (trace_id, path, runtime_ctx) → count of span() calls seen so far.
        # Used to auto-index repeated span() emits at the same key: the
        # first uses runtime_ctx as-is (clean name); subsequent get
        # ``[1]``, ``[2]`` suffixes so they render as distinct Langfuse
        # spans instead of overwriting each other.
        self._span_counter: Dict[Tuple[str, str, Optional[str]], int] = {}
        # Batched ingestion events, flushed on demand or on flush()
        self._batch: List[Dict[str, Any]] = []
        # Batch size threshold — flush automatically when reached
        self._batch_max = 100

    # ------------------------------------------------------------------
    # Sink protocol — call as sink(ev)
    # ------------------------------------------------------------------

    def __call__(self, ev: TraceEvent) -> None:
        """Route a single TraceEvent into the Langfuse batch."""
        try:
            self._handle(ev)
        except Exception:
            LOGGER.exception("LangfuseSink failed to handle event %r", ev.path)

        # Auto-flush on threshold
        if len(self._batch) >= self._batch_max:
            self._send()

    def flush(self, trace_id: str) -> None:
        """Flush the batch and finalize any spans still open for a trace."""
        # Any observation for this trace whose end_time is unset gets a
        # WARNING closure at the current wallclock — indicates the op didn't
        # emit an explicit output/error before the trace ended.
        now = time.perf_counter()
        for (tid, path, ctx), obs_id in list(self._obs_ids.items()):
            if tid != trace_id:
                continue
            if (tid, path, ctx) not in self._last_time:
                self._append_span_update(
                    tid, path, ctx=ctx, end_ts=self._to_iso(tid, now),
                    level="WARNING", metadata={"note": "flushed without close"},
                )
        # Drop per-trace state to prevent unbounded growth across calls
        self._span_counter = {
            k: v for k, v in self._span_counter.items() if k[0] != trace_id
        }
        self._send()

    # ------------------------------------------------------------------
    # Internal — one TraceEvent → 1..N ingestion events
    # ------------------------------------------------------------------

    def _handle(self, ev: TraceEvent) -> None:
        self._ensure_trace(ev.trace_id, ev.time)
        # For span() (kind="call"): auto-index repeated emits at the same
        # (path, runtime_ctx) — first uses runtime_ctx as-is, subsequent
        # get "[1]", "[2]" suffixes so they render as distinct spans.
        # For event(): use ctx as-is (multiple events at same ctx aggregate
        # on one container span as timeline entries).
        if ev.kind == "call":
            ctx = self._auto_index_span_ctx(ev.trace_id, ev.path, ev.ctx)
        else:
            ctx = ev.ctx
        obs_id = self._ensure_observation(ev.trace_id, ev.path, ctx, ev.time)
        ts_iso = self._to_iso(ev.trace_id, ev.time)

        if ev.kind == "call":
            # Paired atomic call → upgrade the leaf path's span with
            # input + output + timing. No timeline events.
            raw = dict(ev.data)
            inputs = dict(raw.get("inputs", {}))
            outputs = dict(raw.get("outputs", {}))
            self._apply_media_dict(ev.trace_id, obs_id, inputs)
            self._apply_media_dict(ev.trace_id, obs_id, outputs)
            self._append_span_update(
                ev.trace_id, ev.path, ctx=ctx,
                start_ts=ts_iso, end_ts=ts_iso,
                input_=inputs, output=outputs,
            )
            self._last_time[(ev.trace_id, ev.path, ctx)] = ev.time
            return

        # kind in {"input", "output", "log", "error"} — one Langfuse event
        # under the leaf path's container span.
        data = self._process_media(ev.trace_id, obs_id, dict(ev.data))
        self._append_event(
            ev.trace_id, obs_id, ts_iso=ts_iso, kind=ev.kind, data=data,
        )
        self._last_time[(ev.trace_id, ev.path, ctx)] = ev.time
        if ev.kind == "error":
            self._append_span_update(
                ev.trace_id, ev.path, ctx=ctx, end_ts=ts_iso, level="ERROR",
            )

    # ------------------------------------------------------------------
    # Trace + observation lifecycle
    # ------------------------------------------------------------------

    def _ensure_trace(self, trace_id: str, first_perf: float) -> None:
        if trace_id in self._traces_created:
            return
        self._trace_epoch[trace_id] = (first_perf, time.time())
        wall_iso = self._to_iso(trace_id, first_perf)
        self._batch.append({
            "id": str(uuid.uuid4()),
            "type": "trace-create",
            "timestamp": wall_iso,
            "body": {
                "id": trace_id,
                "name": self._workflow_name,
                "timestamp": wall_iso,
            },
        })
        self._traces_created.add(trace_id)

    def _auto_index_span_ctx(
        self, trace_id: str, path: str, runtime_ctx: Optional[str],
    ) -> Optional[str]:
        """Auto-index repeated ``span()`` emits at the same (path, ctx).

        First span() at a given (trace_id, path, runtime_ctx) uses
        runtime_ctx as-is → clean leaf name (no counter). Subsequent
        calls at the SAME key get ``[1]``, ``[2]``, … suffixes so they
        render as distinct Langfuse spans instead of overwriting.

        Default case (one span per invocation, no loop/stream): first
        call returns ``runtime_ctx`` unchanged; counter is armed but no
        suffix appears.
        """
        key = (trace_id, path, runtime_ctx)
        n = self._span_counter.get(key)
        if n is None:
            # First span at this key — clean, no suffix
            self._span_counter[key] = 0
            return runtime_ctx
        # Second+ call — collide → distinct span via counter
        n += 1
        self._span_counter[key] = n
        if runtime_ctx is None:
            return str(n)
        return f"{runtime_ctx}.{n}"

    def _ensure_observation(
        self,
        trace_id: str,
        path: str,
        ctx: Optional[str],
        ev_time: float,
    ) -> str:
        """Create nested container spans + the leaf span. Returns leaf id.

        Intermediate segments (all but the last) are keyed by
        ``(trace_id, path_prefix, None)`` — shared across ctx values.

        The leaf segment is keyed by ``(trace_id, path, ctx)`` — distinct
        per ctx so multiple invocations at the same op path become
        multiple sibling spans. The leaf's display name is suffixed with
        ``[ctx]`` when ctx is not None.
        """
        segments = path.split("/")
        current_path = ""
        parent_id: Optional[str] = None
        leaf_id: Optional[str] = None
        for i, segment in enumerate(segments):
            is_leaf = (i == len(segments) - 1)
            current_path = f"{current_path}/{segment}" if current_path else segment
            # Only the leaf differentiates by ctx; intermediates are shared.
            key_ctx = ctx if is_leaf else None
            key = (trace_id, current_path, key_ctx)
            if key not in self._obs_ids:
                obs_id = str(uuid.uuid4())
                self._obs_ids[key] = obs_id
                ts_iso = self._to_iso(trace_id, ev_time)
                name = segment if key_ctx is None else f"{segment}[{key_ctx}]"
                body: Dict[str, Any] = {
                    "id": obs_id,
                    "traceId": trace_id,
                    "name": name,
                    "startTime": ts_iso,
                }
                if parent_id is not None:
                    body["parentObservationId"] = parent_id
                self._batch.append({
                    "id": str(uuid.uuid4()),
                    "type": "span-create",
                    "timestamp": ts_iso,
                    "body": body,
                })
            parent_id = self._obs_ids[key]
            leaf_id = parent_id
        assert leaf_id is not None
        return leaf_id

    def _append_span_update(
        self,
        trace_id: str,
        path: str,
        *,
        ctx: Optional[str] = None,
        start_ts: Optional[str] = None,
        end_ts: Optional[str] = None,
        input_: Optional[Dict[str, Any]] = None,
        output: Optional[Dict[str, Any]] = None,
        level: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        obs_id = self._obs_ids.get((trace_id, path, ctx))
        if not obs_id:
            return
        body: Dict[str, Any] = {"id": obs_id, "traceId": trace_id}
        if start_ts is not None:
            body["startTime"] = start_ts
        if end_ts is not None:
            body["endTime"] = end_ts
        if input_ is not None:
            body["input"] = input_
        if output is not None:
            body["output"] = output
        if level is not None:
            body["level"] = level
        if metadata is not None:
            body["metadata"] = metadata
        self._batch.append({
            "id": str(uuid.uuid4()),
            "type": "span-update",
            "timestamp": end_ts or start_ts or self._now_iso(),
            "body": body,
        })

    def _append_event(
        self,
        trace_id: str,
        parent_obs_id: str,
        *,
        ts_iso: str,
        kind: str,
        data: Dict[str, Any],
    ) -> None:
        body: Dict[str, Any] = {
            "id": str(uuid.uuid4()),
            "traceId": trace_id,
            "parentObservationId": parent_obs_id,
            "name": _event_name(kind, data),
            "startTime": ts_iso,
        }
        if kind == "input":
            body["input"] = data
        elif kind in ("output", "error"):
            body["output"] = data
            if kind == "error":
                body["level"] = "ERROR"
        else:  # kind == "log"
            body["metadata"] = data
        self._batch.append({
            "id": str(uuid.uuid4()),
            "type": "event-create",
            "timestamp": ts_iso,
            "body": body,
        })

    def _apply_media_dict(
        self,
        trace_id: str,
        observation_id: str,
        data: Dict[str, Any],
    ) -> None:
        """Apply media policy in-place on a dict."""
        if not self._upload_media_enabled:
            return
        for key, value in list(data.items()):
            if self._is_media(value):
                content_type, content = self._to_content(value)
                token = self._client.upload_media(
                    trace_id=trace_id,
                    field="input",
                    content_type=content_type,
                    content=content,
                    observation_id=observation_id,
                )
                if token:
                    data[key] = token

    # ------------------------------------------------------------------
    # Media — auto-detect and upload
    # ------------------------------------------------------------------

    def _process_media(
        self,
        trace_id: str,
        observation_id: str,
        data: Dict[str, Any],
    ) -> Dict[str, Any]:
        if not self._upload_media_enabled:
            return data
        for key, value in list(data.items()):
            if self._is_media(value):
                content_type, content = self._to_content(value)
                token = self._client.upload_media(
                    trace_id=trace_id,
                    field="input",  # Langfuse tokens work everywhere; field is metadata
                    content_type=content_type,
                    content=content,
                    observation_id=observation_id,
                )
                if token:
                    data[key] = token
        return data

    @staticmethod
    def _is_media(value: Any) -> bool:
        if isinstance(value, (bytes, bytearray, memoryview)):
            return len(value) > _MEDIA_BYTE_THRESHOLD
        # numpy-like
        if hasattr(value, "shape") and hasattr(value, "dtype") and hasattr(value, "tobytes"):
            return True
        return False

    @staticmethod
    def _to_content(value: Any) -> Tuple[str, bytes]:
        if isinstance(value, (bytes, bytearray, memoryview)):
            return _DEFAULT_MIME, bytes(value)
        if hasattr(value, "tobytes"):
            return _DEFAULT_MIME, value.tobytes()
        return _DEFAULT_MIME, bytes(value)  # best effort

    # ------------------------------------------------------------------
    # Timestamp conversion — perf_counter → wallclock ISO
    # ------------------------------------------------------------------

    def _to_iso(self, trace_id: str, perf_ts: float) -> str:
        epoch = self._trace_epoch.get(trace_id)
        if epoch is None:
            # Trace hasn't been created yet — use current wallclock
            return self._now_iso()
        perf_start, wall_start = epoch
        wall = wall_start + (perf_ts - perf_start)
        return datetime.fromtimestamp(wall, tz=timezone.utc).isoformat().replace("+00:00", "Z")

    @staticmethod
    def _now_iso() -> str:
        return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

    # ------------------------------------------------------------------
    # Ingestion — batched HTTP send
    # ------------------------------------------------------------------

    def _send(self) -> None:
        if not self._batch:
            return
        batch = self._batch
        self._batch = []
        try:
            self._client.ingest(batch)
        except Exception:
            LOGGER.exception("LangfuseSink batch ingest failed (%d events)", len(batch))

    # ------------------------------------------------------------------
    # Convenience — build from resource
    # ------------------------------------------------------------------

    @classmethod
    def from_resource(cls, resource: str, **kwargs) -> "LangfuseSink":
        """Build a sink from a ResourceHub-registered langfuse client."""
        from operonx.core.registry import ResourceHub

        client = ResourceHub.instance().get(resource)
        if not isinstance(client, LangfuseClient):
            raise TypeError(
                f"Resource {resource!r} resolved to {type(client).__name__}, "
                "expected LangfuseClient"
            )
        return cls(client=client, **kwargs)

    @classmethod
    def from_config(cls, config: LangfuseConfig, **kwargs) -> "LangfuseSink":
        """Build a sink from a LangfuseConfig directly (no ResourceHub)."""
        return cls(client=LangfuseClient(config), **kwargs)

    # ------------------------------------------------------------------
    # Debug / testing
    # ------------------------------------------------------------------

    def trace_url(self, trace_id: str) -> str:
        """Return the Langfuse UI URL for a trace."""
        return self._client.trace_url(trace_id)

    def __repr__(self) -> str:
        return f"<LangfuseSink client={self._client!r} pending={len(self._batch)}>"
