"""LangfuseConsumer — ship a WorkflowTrace to Langfuse as the tree the run
actually had.

The record is flat: one :class:`OpExecution` per execution, with the
scheduler's ``ctx`` and every upstream edge. Langfuse is a tree viewer,
so this consumer builds the tree that already exists in ``ctx`` — a
generator's yield record carries the ctx it dispatched, which makes it
the container of everything that ran for that item — and never guesses
one from data edges (see ``docs/TRACING_CTX_TREE_PLAN.md`` §2).

Parent of a record, first hit wins:

1. ctx ``main`` (session-long ops): the trace itself.
2. a level-1 yield (``main.[i]``) with no upstream that resolves to a
   record: the trace itself. (Refs to the root graph's inputs point at
   ``engine#main``, which is never a record.)
3. the upstream producer whose ctx is a prefix of, or equal to, this
   ctx and that started first.
4. the yield record at this ctx, then at each shorter prefix down to
   depth 2 — this is what catches an op whose edges stop at a GraphOp
   boundary.
5. else a stand-in span for the unrecorded yield ``main.[i]``, named
   after the root-level transient stream (``audio_in [357]``). Transient
   streams write no per-item record, so this is the only synthetic node
   a run needs besides graph containers.

Members of a GraphOp (``engine.agent_turn.*``) nest under one container
span per (graph, ctx). Names are op names; a yield is ``synthesize [2]``.
Observation ids are ``f"{run_id}/{op_id}"`` — ``op_id`` alone is the same
string in every run of the same graph, and Langfuse ids are unique per
project, so a bare ``op_id`` let each call overwrite the spans of the
call before it. Times come from the trace's wall-clock anchor. An
``LLMOp`` record is a ``generation`` (model, usage); everything else a
``span``. Runs post-call, one batch; per-event errors in Langfuse's 207
reply are logged, not dropped.

Example ``resources.yaml``::

    trace_langfuse:
      edupia:
        client_resource: langfuse:edupia
        workflow_name:   callbot
"""

from __future__ import annotations

import datetime
import uuid
from collections import defaultdict
from pathlib import Path
from typing import Any, ClassVar, Dict, List, Optional, Tuple

from operonx.core.loggings import LOGGER
from operonx.core.nested import NESTED_RUN, nested_owner
from operonx.core.utils.yaml_model import YamlModel
from operonx.core.workflow_trace import (
    STATUS_ERROR,
    STATUS_RETRIED,
    OpExecution,
    WorkflowTrace,
    child_parent_id,
    format_ctx,
)
from operonx.telemetry.consumer import Consumer

# A node's status as a Langfuse observation level. An attempt `retry=` ran
# again is a warning, not an error: the run did not fail because of it.
_LEVELS = {STATUS_ERROR: "ERROR", STATUS_RETRIED: "WARNING"}

__all__ = ["LangfuseConsumer", "build_tree"]


# ── the tree ────────────────────────────────────────────────────────────
#
# A node is a dict so the builder stays a pure function over the trace
# and the same tree can be tested without a client:
#   id        observation id inside the run (op_id, or a synthetic key)
#   kind      "record" | "container" | "stand-in"
#   name      op name, "op [idx]" for a yield, graph name, "stream [i]"
#   parent    id of the parent node, None at the root
#   start/end perf-counter times (synthetic nodes span their children)
#   record    the OpExecution for a record node, else None
#   rule      which rule placed it (for metadata / tests)


def _idx(ctx: Tuple[str, ...]) -> str:
    return ctx[-1].strip("[]") if len(ctx) > 1 else ""


def build_tree(trace: WorkflowTrace) -> Dict[str, Dict[str, Any]]:
    """Every observation of the run, keyed by id, with its parent set."""
    rows = sorted(trace.nodes, key=lambda n: n.start_time)
    by_id = {n.op_id: n for n in rows}
    by_ctx: Dict[Tuple[str, ...], List[OpExecution]] = defaultdict(list)
    for n in rows:
        by_ctx[tuple(n.ctx)].append(n)
    stream = next(
        (n.op_name for n in rows if len(n.ctx) == 1 and (n.outputs or {}).get("_transient_stream")),
        None,
    )
    # A run started inside an op body records under that op's step: its own
    # record (op_type "graph") is the root its ops hang under.
    roots: Dict[str, List[OpExecution]] = defaultdict(list)
    for n in rows:
        if n.op_type == NESTED_RUN:
            roots[n.op_full_name].append(n)

    def nested_of(r: OpExecution) -> Optional[Tuple[OpExecution, int]]:
        if not roots:
            return None
        return nested_owner(r.op_full_name, tuple(r.ctx), roots)

    def owner_of(r: OpExecution) -> Optional[str]:
        """The record a child execution was recorded under, walking up past
        any ancestor not in the trace (a run still being written)."""
        full, ctx = r.op_full_name, tuple(r.ctx)
        while True:
            owner = child_parent_id(full, ctx)
            if owner is None:
                return None
            # a child of retried attempt n hangs under that attempt's record
            for cand in (f"{owner}@{r.attempt}", owner):
                if cand in by_id:
                    return cand
            full, ctx = full.rsplit(".", 1)[0], ctx[:-1]

    def parent_of(r: OpExecution) -> Tuple[Optional[str], str]:
        ctx = tuple(r.ctx)
        owner = owner_of(r)
        if owner is not None:
            return owner, "child"
        # Inside a nested run its record is the root: the same rules, with
        # its ctx as the floor instead of ("main",).
        nested = nested_of(r)
        top = nested[0].op_id if nested is not None else None
        floor = len(nested[0].ctx) if nested is not None else 1
        if len(ctx) == floor:
            return (top, "nested run") if nested is not None else (None, "root")
        resolved = [by_id[u.from_op_id] for u in r.upstreams if u.from_op_id in by_id]
        if len(ctx) == floor + 1 and r.is_yield and not resolved:
            return top, "level-1 yield"
        for c in resolved:
            cc = tuple(c.ctx)
            if (
                floor < len(cc) <= len(ctx)
                and ctx[: len(cc)] == cc
                and c.start_time <= r.start_time
            ):
                return c.op_id, "fed by it"
        for k in range(len(ctx), floor, -1):
            cands = [
                c
                for c in by_ctx.get(ctx[:k], [])
                if c is not r and c.start_time <= r.start_time and (k < len(ctx) or c.is_yield)
            ]
            if cands:
                return cands[-1].op_id, "same-ctx yield" if k == len(ctx) else "ctx prefix"
        if nested is not None:
            return top, "nested run"
        return f"stand-in:{format_ctx(ctx[:2])}", "stand-in"

    nodes: Dict[str, Dict[str, Any]] = {}
    for r in rows:
        parent, rule = parent_of(r)
        ctx = tuple(r.ctx)
        if rule == "stand-in" and parent not in nodes:
            nodes[parent] = {
                "id": parent,
                "kind": "stand-in",
                "parent": None,
                "rule": "stand-in",
                "name": f"{stream or 'stream'} [{ctx[1].strip('[]')}]",
                "start": r.start_time,
                "end": r.end_time,
                "record": None,
                "ctx": format_ctx(ctx[:2]),
            }
        # GraphOp members nest under one container per (graph path, ctx),
        # placed where the first member's parent is; a member fed by a
        # sibling member keeps that sibling as its parent.
        parts = r.op_full_name.split(".")
        # the graphs between the run's root (a nested run's record, for its
        # ops) and the op; a nested run's are named by full path
        nested = nested_of(r) if child_parent_id(r.op_full_name, ctx) is None else None
        base = nested[1] if nested is not None else 1
        graphs = parts[base:-1]
        # A child execution sits under the op that ran it, never in a
        # container named after that op.
        if graphs and child_parent_id(r.op_full_name, ctx) is None:
            parent_rec = by_id.get(parent) if parent else None
            sibling = (
                parent_rec is not None and parent_rec.op_full_name.split(".")[base:-1] == graphs
            )
            if not sibling:
                above = parent
                for depth in range(1, len(graphs) + 1):
                    named = parts[: base + depth] if nested is not None else graphs[:depth]
                    cid = f"graph:{'.'.join(named)}#{format_ctx(ctx)}"
                    if cid not in nodes:
                        nodes[cid] = {
                            "id": cid,
                            "kind": "container",
                            "parent": above,
                            "rule": "graph container",
                            "name": graphs[depth - 1],
                            "start": r.start_time,
                            "end": r.end_time,
                            "record": None,
                            "ctx": format_ctx(ctx),
                        }
                    above = cid
                parent = above
        nodes[r.op_id] = {
            "id": r.op_id,
            "kind": "record",
            "parent": parent,
            "rule": rule,
            "name": f"{r.op_name} [{_idx(ctx)}]" if r.is_yield else r.op_name,
            "start": r.start_time,
            "end": r.end_time,
            "record": r,
            "ctx": format_ctx(ctx),
        }
    # synthetic nodes span their descendants
    children: Dict[Optional[str], List[str]] = defaultdict(list)
    for n in nodes.values():
        children[n["parent"]].append(n["id"])

    def extent(nid: str) -> Tuple[float, float]:
        n = nodes[nid]
        s, e = n["start"], n["end"]
        for k in children.get(nid, []):
            ks, ke = extent(k)
            s, e = min(s, ks), max(e, ke)
        if n["kind"] != "record":
            n["start"], n["end"] = s, e
        return s, e

    for nid in children[None]:
        extent(nid)
    return nodes


# ── the consumer ────────────────────────────────────────────────────────


class LangfuseConsumer(Consumer):
    """Ship a whole :class:`WorkflowTrace` to Langfuse as one batch.

    Config keys:

    * ``client`` (``LangfuseClient``) — REQUIRED; injected by the
      ResourceHub factory from ``client_resource``.
    * ``workflow_name`` — the Langfuse trace name; defaults to the run's.
    * ``media_threshold`` — bytes above which a payload becomes a local
      ``$media_ref`` token (default 1024). ``media_dir`` — where those go.

    Returns the trace URL.
    """

    DEFAULT_CONFIG: Dict[str, Any] = {
        "workflow_name": None,
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
        run = trace.run_id
        ext = lambda nid: f"{run}/{nid}"  # noqa: E731 — one-line id scoping

        batch: List[Dict[str, Any]] = [self._trace_create(trace, wf_name)]
        for node in build_tree(trace).values():
            body: Dict[str, Any] = {
                "id": ext(node["id"]),
                "traceId": trace.trace_id,
                "parentObservationId": ext(node["parent"]) if node["parent"] else None,
                "name": node["name"],
                "startTime": self._iso(trace.wall_of(node["start"])),
                "endTime": self._iso(trace.wall_of(node["end"])),
                "metadata": {"kind": node["kind"], "ctx": node["ctx"], "rule": node["rule"]},
            }
            rec: Optional[OpExecution] = node["record"]
            kind = "span"
            if rec is not None:
                inputs, outputs = rec.exported()
                if rec.inputs_from is None:
                    body["input"] = self.offload_media(
                        self.sanitize(inputs), media_dir, cfg["media_threshold"]
                    )
                else:
                    # a generator's later record: its inputs are the first one's
                    body["metadata"]["inputs_from"] = ext(rec.inputs_from)
                if rec.attempt != 1:
                    body["metadata"]["attempt"] = rec.attempt
                if rec.attrs:
                    body["metadata"]["attrs"] = self.sanitize(rec.attrs)
                body["output"] = self.offload_media(
                    self.sanitize(outputs), media_dir, cfg["media_threshold"]
                )
                body["level"] = _LEVELS.get(rec.status, "DEFAULT")
                body["statusMessage"] = rec.error
                body["metadata"].update(
                    {
                        "op_full_name": rec.op_full_name,
                        "op_type": rec.op_type,
                        "is_yield": rec.is_yield,
                        "status": rec.status,
                        "duration_ms": rec.duration_ms,
                        "upstreams": [
                            {"from": ext(u.from_op_id), "from_key": u.from_key, "to_key": u.to_key}
                            for u in rec.upstreams
                        ],
                    }
                )
                if rec.op_type == "llm":
                    kind = "generation"
                    out = outputs if isinstance(outputs, dict) else {}
                    body["model"] = out.get("model_used")
                    usage = out.get("usage") or {}
                    if isinstance(usage, dict) and usage:
                        body["usage"] = {
                            "input": usage.get("prompt_tokens"),
                            "output": usage.get("completion_tokens"),
                            "total": usage.get("total_tokens"),
                            "unit": "TOKENS",
                        }
            batch.append(
                {
                    "id": uuid.uuid4().hex,
                    "timestamp": body["startTime"],
                    "type": f"{kind}-create",
                    "body": body,
                }
            )

        reply = client.ingest(batch) or {}
        errors = reply.get("errors") or []
        if errors:
            LOGGER.warning(
                "langfuse ingestion: %d of %d events rejected on trace %s; first: %s",
                len(errors),
                len(batch),
                trace.trace_id,
                errors[0],
            )
        return client.trace_url(trace.trace_id)

    def _trace_create(self, trace: WorkflowTrace, name: str) -> Dict[str, Any]:
        stamp = self._iso(trace.wall_of(trace.started_at))
        return {
            "id": uuid.uuid4().hex,
            "timestamp": stamp,
            "type": "trace-create",
            "body": {
                "id": trace.trace_id,
                "name": name,
                "timestamp": stamp,
                "userId": trace.metadata.get("user_id"),
                "sessionId": trace.metadata.get("session_id"),
                "metadata": trace.metadata,
                "tags": trace.metadata.get("tags") or [],
            },
        }

    @staticmethod
    def _iso(epoch: float) -> str:
        """Epoch seconds → ISO 8601 UTC, the form Langfuse's API takes."""
        return (
            datetime.datetime.fromtimestamp(epoch, tz=datetime.timezone.utc)
            .isoformat(timespec="milliseconds")
            .replace("+00:00", "Z")
        )


# ── ResourceHub config ──────────────────────────────────────────────────


class LangfuseConsumerConfig(YamlModel):
    """YAML-configurable :class:`LangfuseConsumer`.

    ``parent_strategy`` is accepted and ignored: the tree comes from ctx
    now. It stays in the schema so an existing ``resources.yaml`` keeps
    loading; remove it at leisure.
    """

    _category: ClassVar[str] = "trace_langfuse"

    client_resource: str  # e.g. "langfuse:edupia"
    workflow_name: Optional[str] = None
    parent_strategy: Optional[str] = None  # deprecated, ignored
    media_threshold: int = 1024
    media_dir: Optional[str] = None


def _create_langfuse_consumer(cfg: LangfuseConsumerConfig) -> LangfuseConsumer:
    from operonx.core.registry import ResourceHub

    client = ResourceHub.instance().get(cfg.client_resource)
    if cfg.parent_strategy:
        LOGGER.warning(
            "trace_langfuse: parent_strategy is ignored since the ctx-tree consumer; remove it"
        )
    return LangfuseConsumer(
        config={
            "client": client,
            "workflow_name": cfg.workflow_name,
            "media_threshold": cfg.media_threshold,
            "media_dir": cfg.media_dir,
        }
    )
