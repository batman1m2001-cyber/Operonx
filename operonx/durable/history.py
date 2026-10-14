"""A stopped run's trace, kept in its journal so its resume is the whole run.

The run id is the trace id (interrupt ids and idempotency keys come from
it), so a resume records under the same id as the run it continues — and
every trace store replaces a run it gets again. Without this, answering a
parked run left a trace of what ran after the answer only.

When a journalled run stops — parked, drained or failed — the engine keeps
its trace here (:func:`record_of`); a run that ends pays nothing. A resume
loads it into its own trace before it runs (:func:`restore`): the nodes
with their wall times, the run's first start and its metadata (where it is
filed). A loaded node that did not end ok — the parked interrupt, the
failed op — is renamed ``<op_id>@stopped`` (a failed one is ``retried``,
as an op's own retry is): its op runs again under the plain id. Every
store then gets one trace holding the whole run.
"""

from __future__ import annotations

from dataclasses import fields
from typing import Any, Dict, List, Optional

from operonx.core.workflow_trace import (
    STATUS_ERROR,
    STATUS_OK,
    STATUS_RETRIED,
    STOPPED_SUFFIX,
    OpExecution,
    UpstreamRef,
    WorkflowTrace,
)

from . import codec

__all__ = ["STOPPED_SUFFIX", "record_of", "restore"]

#: Keys of the stopped trace's metadata a resume does not carry on: the
#: questions it stopped on are answered or asked again.
_STALE = ("interrupted",)
_NODE_FIELDS = [
    f.name
    for f in fields(OpExecution)
    if f.name not in ("redact", "inputs", "outputs", "upstreams", "start_time", "end_time")
]


def _value(value: Any) -> Any:
    try:
        return codec.encode(value)
    except Exception:  # noqa: BLE001 — shown, never run: its repr will do
        return repr(value)


def _back(value: Any) -> Any:
    try:
        return codec.decode(value)
    except Exception:  # noqa: BLE001 — a class this process cannot import
        return value


def record_of(trace: WorkflowTrace) -> Dict[str, Any]:
    """*trace* as plain data: its nodes (wall-clock times, inputs and
    outputs as exported — redacted where the op redacts), its first start
    and its metadata."""
    nodes: List[Dict[str, Any]] = []
    for node in trace.nodes:
        inputs, outputs = node.exported()
        item = {name: getattr(node, name) for name in _NODE_FIELDS}
        item["ctx"] = list(node.ctx)
        item["attrs"] = _value(node.attrs)
        item["inputs"] = {k: _value(v) for k, v in (inputs or {}).items()}
        item["outputs"] = {k: _value(v) for k, v in (outputs or {}).items()}
        item["upstreams"] = [vars(u).copy() for u in node.upstreams]
        item["start"] = trace.wall_of(node.start_time)
        item["end"] = trace.wall_of(node.end_time)
        nodes.append(item)
    return {
        "wall_started_at": trace.wall_started_at,
        "metadata": _value(trace.metadata),
        "nodes": nodes,
    }


def restore(trace: WorkflowTrace, record: Optional[Dict[str, Any]]) -> None:
    """Put the stopped run's *record* into *trace*, a resume's, before it
    runs: its start moves back to the run's first, its metadata is carried
    on (where the run is filed stays the stopped run's), and its nodes are
    recorded in order."""
    if not record:
        return
    first = record.get("wall_started_at") or trace.wall_started_at
    if first and trace.wall_started_at:
        trace.started_at -= trace.wall_started_at - first
        trace.wall_started_at = first
    kept = _back(record.get("metadata") or {})
    if isinstance(kept, dict):
        for key in _STALE:
            kept.pop(key, None)
        merged = {**kept, **trace.metadata, **_filing(kept)}
        trace.metadata.clear()
        trace.metadata.update(merged)
    for item in record.get("nodes") or ():
        node = OpExecution(
            **{name: item[name] for name in _NODE_FIELDS if name in item},
            start_time=_perf(trace, item.get("start")),
            end_time=_perf(trace, item.get("end")),
            inputs={k: _back(v) for k, v in (item.get("inputs") or {}).items()},
            outputs={k: _back(v) for k, v in (item.get("outputs") or {}).items()},
            upstreams=[UpstreamRef(**u) for u in item.get("upstreams") or ()],
        )
        node.ctx = tuple(node.ctx)
        node.attrs = _back(node.attrs) or {}
        if node.status != STATUS_OK:
            node.op_id += STOPPED_SUFFIX
            if node.status == STATUS_ERROR:  # run again since: not this run's failure
                node.status = STATUS_RETRIED
        trace.record(node)


def _filing(metadata: Dict[str, Any]) -> Dict[str, Any]:
    """What files the run (its origin and owner, its caller's ids): the
    stopped run's, so the resume lands where the run is."""
    keys = ("origin", "service", "transport", "variant", "job", "job_run", "item")
    keys += ("request_id", "user_id", "session_id")
    return {k: metadata[k] for k in keys if k in metadata}


def _perf(trace: WorkflowTrace, wall: Any) -> float:
    if not isinstance(wall, (int, float)):
        return trace.started_at
    return trace.started_at + (wall - trace.wall_started_at)
