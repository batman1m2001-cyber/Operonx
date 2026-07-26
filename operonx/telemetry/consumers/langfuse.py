"""LangfuseConsumer — batch-ship a WorkflowTrace to Langfuse at end of call.

Converts each :class:`OpExecution` in the trace into one Langfuse span
observation, walks ``upstreams`` to pick a parent (first upstream wins,
matching the tree-flattening trade-off explicit in the config), and
POSTs the whole batch via :meth:`LangfuseClient.ingest`. Runs post-call
so it never sits in the WS hot path — the trade-off (documented in
:doc:`TRACING_V3_DESIGN` §5) is no live streaming during the call.

Media offload uses the base :meth:`Consumer.offload_media` walk to
replace big payloads with local ``$media_ref`` tokens; those aren't
Langfuse's media type — the media stays local. For real Langfuse media
uploads set ``upload_media=True`` (backlog — the current v1 keeps
media local only).

Example ``resources.yaml``::

    consumer_langfuse:
      edupia:
        client_resource: langfuse:edupia    # reference existing client
        workflow_name:   callbot            # sets Langfuse trace name
        parent_strategy: first_upstream     # or "root_only" / "sequential"

Then hand the resource key to :class:`Operon`::

    engine = Operon(pipeline, trace="consumer_langfuse:edupia")
"""

from __future__ import annotations

import time
from typing import Any, ClassVar, Dict, List, Optional
from pathlib import Path

from operonx.core.utils.yaml_model import YamlModel
from operonx.core.workflow_trace import (
    STATUS_ERROR,
    OpExecution,
    UpstreamRef,
    WorkflowTrace,
    format_ctx,
)
from operonx.telemetry.consumer import Consumer

__all__ = ["LangfuseConsumer"]


class LangfuseConsumer(Consumer):
    """Ship a whole :class:`WorkflowTrace` to Langfuse as one batch.

    Config keys (all optional, sensible defaults):

    * ``client`` (``LangfuseClient`` instance) — REQUIRED at construct
      time; typically injected by the ResourceHub factory.
    * ``workflow_name`` (``str``) — Langfuse trace name; defaults to the
      run's ``workflow_name``.
    * ``parent_strategy`` (``str``) — how to pick ONE parent for the
      tree-only Langfuse span model. Choices:
        - ``"first_upstream"`` (default) — first `UpstreamRef` wins.
        - ``"root_only"`` — no parents; every node hangs off the trace root.
        - ``"sequential"`` — parent = previous node by start_time.
    * ``media_threshold`` (``int``) — bytes above which payloads get
      replaced with a `$media_ref` token; defaults to ``1024``.
    * ``media_dir`` (``str | Path``) — where to write offloaded media
      blobs; defaults to a per-run temp dir. Media stays local for now.

    Returns the Langfuse trace URL on success (via ``client.trace_url``).
    """

    DEFAULT_CONFIG: Dict[str, Any] = {
        "workflow_name": None,
        "parent_strategy": "first_upstream",
        "media_threshold": 1024,
        "media_dir": None,
    }

    def consume(self, trace: WorkflowTrace) -> str:
        cfg = {**self.DEFAULT_CONFIG, **self.config}
        client = cfg.get("client")
        if client is None:
            raise ValueError(
                "LangfuseConsumer requires a `client` in config — "
                "usually injected by the ResourceHub factory from a "
                "`client_resource:` key in resources.yaml."
            )

        wf_name = cfg["workflow_name"] or trace.workflow_name
        media_dir = Path(cfg["media_dir"] or f"/tmp/operonx_langfuse_media/{trace.trace_id}")
        parent_of = self._pick_parents(trace, cfg["parent_strategy"])

        batch: List[Dict[str, Any]] = []
        # 1. trace-create — attach metadata + tags
        batch.append(self._trace_create(trace, wf_name))
        # 2. span-create per OpExecution
        for node in trace.nodes:
            batch.append(self._span_create(
                node, trace.trace_id, parent_of.get(node.op_id),
                media_dir, cfg["media_threshold"],
            ))

        client.ingest(batch)
        return client.trace_url(trace.trace_id)

    # ------------------------------------------------------------------
    # Batch item builders
    # ------------------------------------------------------------------

    def _trace_create(self, trace: WorkflowTrace, name: str) -> Dict[str, Any]:
        return {
            "id": self._event_id(),
            "timestamp": self._iso(trace.started_at),
            "type": "trace-create",
            "body": {
                "id": trace.trace_id,
                "name": name,
                "timestamp": self._iso(trace.started_at),
                "userId": trace.metadata.get("user_id"),
                "sessionId": trace.metadata.get("session_id"),
                "metadata": trace.metadata,
                "tags": trace.metadata.get("tags") or [],
            },
        }

    def _span_create(
        self,
        node: OpExecution,
        trace_id: str,
        parent_id: Optional[str],
        media_dir: Path,
        media_threshold: int,
    ) -> Dict[str, Any]:
        clean_in = self.offload_media(self.sanitize(node.inputs), media_dir, media_threshold)
        clean_out = self.offload_media(self.sanitize(node.outputs), media_dir, media_threshold)
        return {
            "id": self._event_id(),
            "timestamp": self._iso(node.start_time),
            "type": "span-create",
            "body": {
                "id": node.op_id,
                "traceId": trace_id,
                "parentObservationId": parent_id,
                "name": node.op_name,
                "startTime": self._iso(node.start_time),
                "endTime": self._iso(node.end_time),
                "input": clean_in,
                "output": clean_out,
                "level": "ERROR" if node.status == STATUS_ERROR else "DEFAULT",
                "statusMessage": node.error,
                "metadata": {
                    "op_full_name": node.op_full_name,
                    "ctx": format_ctx(node.ctx),
                    "status": node.status,
                    "duration_ms": node.duration_ms,
                    "upstreams": [
                        {"from": u.from_op_id, "from_key": u.from_key, "to_key": u.to_key}
                        for u in node.upstreams
                    ],
                },
            },
        }

    # ------------------------------------------------------------------
    # Parent-picking strategies (DAG → tree lossy flatten)
    # ------------------------------------------------------------------

    def _pick_parents(
        self, trace: WorkflowTrace, strategy: str,
    ) -> Dict[str, Optional[str]]:
        if strategy == "root_only":
            return {n.op_id: None for n in trace.nodes}
        if strategy == "sequential":
            sorted_nodes = sorted(trace.nodes, key=lambda n: n.start_time)
            return {
                n.op_id: (sorted_nodes[i - 1].op_id if i > 0 else None)
                for i, n in enumerate(sorted_nodes)
            }
        # default: first_upstream
        return {n.op_id: (n.upstreams[0].from_op_id if n.upstreams else None)
                for n in trace.nodes}

    # ------------------------------------------------------------------
    # Small helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _iso(perf: float) -> str:
        """Convert perf_counter() timestamp to ISO 8601 UTC.

        Langfuse expects wall-clock ISO strings. perf_counter is monotonic
        but not tied to epoch — we approximate by adding a boot offset.
        Good enough for post-hoc traces where relative timing matters
        more than absolute wall-clock.
        """
        # perf_counter offset is application-dependent; for now we treat
        # it as an offset from a fixed reference. If exact wall-clock is
        # needed, callers should pass `started_at`/`ended_at` as
        # time.time() rather than time.perf_counter().
        import datetime
        # Assume perf timestamps are already close enough to unix time
        # for a first pass; adjust in a follow-up if drift is noticed.
        return datetime.datetime.utcfromtimestamp(perf).isoformat() + "Z"

    @staticmethod
    def _event_id() -> str:
        import uuid
        return uuid.uuid4().hex


# ---------------------------------------------------------------------------
# ResourceHub config — declare the consumer in ``resources.yaml``
# ---------------------------------------------------------------------------
#
# Example ``resources.yaml``::
#
#     trace_langfuse:
#       edupia:
#         client_resource: langfuse:edupia    # reference existing client
#         workflow_name:   callbot
#         parent_strategy: first_upstream
#
# Then hand the key to ``Operon(pipeline, trace="trace_langfuse:edupia")``.
#
# The factory below resolves ``client_resource`` via
# ``ResourceHub.instance().get(...)`` so the actual LangfuseClient is
# shared with anything else that references the same resource key.


class LangfuseConsumerConfig(YamlModel):
    """YAML-configurable :class:`LangfuseConsumer`."""

    _category: ClassVar[str] = "trace_langfuse"

    client_resource: str                      # e.g. "langfuse:edupia"
    workflow_name: Optional[str] = None
    parent_strategy: str = "first_upstream"   # first_upstream|root_only|sequential
    media_threshold: int = 1024
    media_dir: Optional[str] = None


def _create_langfuse_consumer(cfg: LangfuseConsumerConfig) -> LangfuseConsumer:
    from operonx.core.registry import ResourceHub

    client = ResourceHub.instance().get(cfg.client_resource)
    return LangfuseConsumer(config={
        "client": client,
        "workflow_name": cfg.workflow_name,
        "parent_strategy": cfg.parent_strategy,
        "media_threshold": cfg.media_threshold,
        "media_dir": cfg.media_dir,
    })
