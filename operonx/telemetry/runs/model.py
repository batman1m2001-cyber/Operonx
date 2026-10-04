"""What a run store holds and answers with — plain data, no I/O.

Two shapes, because a trace is read two ways:

* **one run, fully** — :class:`RunRecord`: the run's summary plus every
  execution with its values (the rows ``nodes.jsonl`` holds);
* **many runs, summarised** — :class:`RunSummary` per run and
  :class:`OpRollup` per op per run, small enough that a dashboard over a
  month of calls never opens a single trace.

:func:`summarize` derives both from a run's rows, so every backend — and
a reader indexing directories some other consumer wrote — agrees on
what a run's numbers are.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from operonx.core.workflow_trace import STATUS_OK, STATUS_RETRIED

__all__ = [
    "MAX_SAMPLES",
    "OpRollup",
    "OpStats",
    "Page",
    "RunFilter",
    "RunRecord",
    "RunSummary",
    "percentile",
    "meta_of_trace",
    "rows_of_trace",
    "summarize",
]

#: Durations kept per op per run for percentiles across runs. Every
#: execution up to this many; past it, an even spread (and the stats say
#: they are approximate).
MAX_SAMPLES = 64

#: Metadata keys that become columns — everything a filter or a list row
#: needs without parsing the metadata blob.
_NAMED = (
    "origin",
    "service",
    "transport",
    "variant",
    "job",
    "job_run",
    "key",
    "runbook",
    "runbook_run",
    "version",
    "project",
    "session_id",
    "user_id",
    "request_id",
)


@dataclass
class RunSummary:
    """One run, in a line: who made it, how it went, what it cost."""

    trace_id: str
    workflow: str = ""
    origin: str = "adhoc"
    #: The origin's own name: the service, the job, else the workflow.
    name: str = ""
    status: str = "ok"
    started_at: float = 0.0  # epoch seconds
    duration_ms: float = 0.0
    executions: int = 0
    ops: int = 0
    errors: int = 0
    first_error: Optional[str] = None
    #: Sum of every priced call's ``cost_usd``; ``None`` when nothing in
    #: the run reported a price at all.
    cost_usd: Optional[float] = None
    #: Calls that reported ``cost_usd = None``: a cost nobody measured.
    unpriced: int = 0
    llm_calls: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    tokens_cached: int = 0
    version: Optional[str] = None
    version_dirty: Optional[bool] = None
    service: Optional[str] = None
    transport: Optional[str] = None
    variant: Optional[str] = None
    job: Optional[str] = None
    job_run: Optional[str] = None
    key: Optional[str] = None
    runbook: Optional[str] = None
    runbook_run: Optional[str] = None
    project: Optional[str] = None
    session_id: Optional[str] = None
    user_id: Optional[str] = None
    request_id: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    #: Where the full record lives, when the backend has a place (a
    #: directory for the files store).
    location: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "RunSummary":
        known = {f for f in cls.__dataclass_fields__}  # type: ignore[attr-defined]
        return cls(**{k: v for k, v in d.items() if k in known})


@dataclass
class OpRollup:
    """One op in one run: how often, how long, how costly."""

    trace_id: str
    op: str
    op_type: str = ""
    count: int = 0
    total_ms: float = 0.0
    max_ms: float = 0.0
    errors: int = 0
    cost_usd: Optional[float] = None
    unpriced: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    #: Up to :data:`MAX_SAMPLES` durations of this op in this run.
    samples: List[float] = field(default_factory=list)
    #: True when ``samples`` holds every execution.
    exact: bool = True

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class OpStats:
    """One op across every run a filter matched — a Monitor table row."""

    op: str
    op_type: str = ""
    runs: int = 0
    count: int = 0
    total_ms: float = 0.0
    avg_ms: float = 0.0
    p50_ms: float = 0.0
    p95_ms: float = 0.0
    p99_ms: float = 0.0
    max_ms: float = 0.0
    errors: int = 0
    cost_usd: Optional[float] = None
    unpriced: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    #: False when any run's samples were thinned: the percentiles are
    #: then an estimate, and the UI says so.
    exact: bool = True

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class RunRecord:
    """One run in full: the summary, the trace-level metadata, and every
    execution as the row a consumer wrote (inputs and outputs included,
    media as references relative to ``media_root``)."""

    summary: RunSummary
    nodes: List[Dict[str, Any]] = field(default_factory=list)
    meta: Dict[str, Any] = field(default_factory=dict)
    media_root: Optional[str] = None


@dataclass
class RunFilter:
    """Which runs — a small data object, never a query language, so every
    backend implements it natively.

    ``metadata`` matches metadata keys exactly (``{"session_id": "0912"}``);
    ``search`` is a case-insensitive substring over the trace id, the key
    and the metadata. Times are epoch seconds, ``since`` inclusive.
    """

    origin: Optional[str] = None
    name: Optional[str] = None
    status: Optional[str] = None
    since: Optional[float] = None
    until: Optional[float] = None
    version: Optional[str] = None
    job_run: Optional[str] = None
    runbook_run: Optional[str] = None
    trace_ids: Optional[Sequence[str]] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    search: Optional[str] = None

    def matches(self, s: RunSummary) -> bool:
        """The filter in Python — for backends without a query engine."""
        if self.origin and s.origin != self.origin:
            return False
        if self.name and s.name != self.name:
            return False
        if self.status and s.status != self.status:
            return False
        if self.since is not None and s.started_at < self.since:
            return False
        if self.until is not None and s.started_at >= self.until:
            return False
        if self.version and s.version != self.version:
            return False
        if self.job_run and s.job_run != self.job_run:
            return False
        if self.runbook_run and s.runbook_run != self.runbook_run:
            return False
        if self.trace_ids is not None and s.trace_id not in set(self.trace_ids):
            return False
        for k, v in self.metadata.items():
            if str(s.metadata.get(k)) != str(v):
                return False
        if self.search:
            q = self.search.lower()
            hay = " ".join([s.trace_id, s.key or "", json.dumps(s.metadata, default=str)]).lower()
            if q not in hay:
                return False
        return True


@dataclass
class Page:
    """One page of runs, and the cursor for the next (``None``: the end)."""

    items: List[RunSummary]
    next_cursor: Optional[str] = None
    total: Optional[int] = None


# ── deriving the numbers ────────────────────────────────────────────────


def percentile(values: Sequence[float], q: float) -> float:
    """The q-th percentile (0–100), linear between closest ranks."""
    if not values:
        return 0.0
    xs = sorted(values)
    if len(xs) == 1:
        return float(xs[0])
    pos = (len(xs) - 1) * q / 100.0
    lo = int(pos)
    hi = min(lo + 1, len(xs) - 1)
    return float(xs[lo] + (xs[hi] - xs[lo]) * (pos - lo))


def _thin(values: List[float], cap: int) -> Tuple[List[float], bool]:
    """At most ``cap`` values, spread evenly over the sorted list."""
    if len(values) <= cap:
        return values, True
    xs = sorted(values)
    step = (len(xs) - 1) / (cap - 1)
    return [xs[round(i * step)] for i in range(cap)], False


def _usage(outputs: Dict[str, Any]) -> Tuple[int, int, int]:
    usage = outputs.get("usage") if isinstance(outputs, dict) else None
    if not isinstance(usage, dict):
        return 0, 0, 0

    def n(key: str) -> int:
        try:
            return int(usage.get(key) or 0)
        except (TypeError, ValueError):
            return 0

    return n("prompt_tokens"), n("completion_tokens"), n("cached_tokens")


def _first_line(text: Any, limit: int = 300) -> str:
    lines = [ln for ln in str(text).strip().splitlines() if ln.strip()]
    return (lines[-1] if lines else str(text))[:limit]


def summarize(
    trace_id: str,
    rows: Iterable[Dict[str, Any]],
    meta: Optional[Dict[str, Any]] = None,
    location: Optional[str] = None,
) -> Tuple[RunSummary, List[OpRollup]]:
    """A run's summary and per-op rollups from its rows (the shape
    ``nodes.jsonl`` holds) and its ``meta.json``.

    Cost follows operonx's own rule: an op that reports ``cost_usd`` is
    priced when the value is a number (zero included — a declared zero)
    and *unpriced* when it is ``None``; the run's cost is ``None`` only
    when nothing reported a price at all.
    """
    meta = dict(meta or {})
    md = dict(meta.get("metadata") or {})
    per_op: Dict[str, OpRollup] = {}
    durations: Dict[str, List[float]] = {}
    first_start: Optional[float] = None
    last_end: Optional[float] = None
    first_wall: Optional[float] = None
    s = RunSummary(
        trace_id=trace_id, workflow=str(meta.get("workflow_name") or ""), location=location
    )

    for row in rows:
        op = str(row.get("op_name") or row.get("op_full_name") or "?")
        dur = float(row.get("duration_ms") or 0.0)
        r = per_op.get(op)
        if r is None:
            r = per_op[op] = OpRollup(
                trace_id=trace_id, op=op, op_type=str(row.get("op_type") or "")
            )
            durations[op] = []
        r.count += 1
        r.total_ms += dur
        r.max_ms = max(r.max_ms, dur)
        durations[op].append(dur)
        s.executions += 1
        start = row.get("start_time")
        if isinstance(start, (int, float)):
            first_start = start if first_start is None else min(first_start, start)
            end = row.get("end_time")
            end = end if isinstance(end, (int, float)) else start + dur / 1000.0
            last_end = end if last_end is None else max(last_end, end)
        wall = row.get("wall_start")
        if isinstance(wall, (int, float)):
            first_wall = wall if first_wall is None else min(first_wall, wall)
        # A retried attempt is not a failure: the attempt after it decides.
        if row.get("status") not in (None, STATUS_OK, STATUS_RETRIED):
            r.errors += 1
            s.errors += 1
            if s.first_error is None:
                s.first_error = f"{op}: {_first_line(row.get('error') or row.get('status'))}"
        outputs = row.get("outputs")
        if isinstance(outputs, dict) and "cost_usd" in outputs:
            s.llm_calls += 1
            cost = outputs.get("cost_usd")
            if isinstance(cost, (int, float)):
                r.cost_usd = (r.cost_usd or 0.0) + float(cost)
                s.cost_usd = (s.cost_usd or 0.0) + float(cost)
            else:
                r.unpriced += 1
                s.unpriced += 1
            t_in, t_out, t_cached = _usage(outputs)
            r.tokens_in += t_in
            r.tokens_out += t_out
            s.tokens_in += t_in
            s.tokens_out += t_out
            s.tokens_cached += t_cached

    for op, r in per_op.items():
        r.samples, r.exact = _thin(durations[op], MAX_SAMPLES)

    s.ops = len(per_op)
    # The run's own records (`meta.json` "errors") know failures no row
    # shows: a structured LLM step that returned `error`, a subgraph that
    # failed around its children, a loop stopped at its cap. They are in
    # the order the ops failed, so the first is the likeliest cause — the
    # parse failure, not the `PromptError` it caused downstream.
    recorded = meta.get("errors") or {}
    if recorded:
        op_name, record = next(iter(recorded.items()))
        text = record.get("message") or record.get("type")
        s.first_error = f"{op_name.rsplit('.', 1)[-1]}: {_first_line(text)}"
    s.status = "error" if s.errors or recorded or meta.get("status") == "error" else "ok"
    if meta.get("duration_ms") is not None:
        s.duration_ms = float(meta["duration_ms"])
    elif first_start is not None and last_end is not None:
        s.duration_ms = (last_end - first_start) * 1000.0
    s.started_at = float(meta.get("wall_started_at") or first_wall or 0.0)
    s.metadata = md
    for key in _NAMED:
        value = md.get(key)
        if value is not None:
            setattr(s, key, value if key == "origin" else str(value))
    if md.get("version_dirty") is not None:
        s.version_dirty = bool(md["version_dirty"])
    s.origin = str(md.get("origin") or "adhoc")
    if s.origin in ("job", "eval"):
        s.name = str(md.get("job") or s.workflow)
    elif s.origin in ("service", "playground"):
        s.name = str(md.get("service") or s.workflow)
    else:
        s.name = s.workflow
    return s, list(per_op.values())


def rows_of_trace(
    trace: Any, consumer: Any, media_dir: Any = None, threshold: int = 1024
) -> List[Dict[str, Any]]:
    """A live ``WorkflowTrace`` as the rows a consumer writes — values
    sanitised to JSON by *consumer* (any :class:`Consumer`), and large
    payloads offloaded to *media_dir* when one is given."""

    def clean(values: Any) -> Any:
        out = consumer.sanitize(values)
        if media_dir is not None:
            out = consumer.offload_media(out, media_dir, threshold)
        return out

    rows = []
    for n in trace.nodes:
        rows.append(
            {
                "op_id": n.op_id,
                "op_name": n.op_name,
                "op_full_name": n.op_full_name,
                "ctx": list(n.ctx),
                "start_time": n.start_time,
                "end_time": n.end_time,
                "wall_start": trace.wall_of(n.start_time),
                "duration_ms": n.duration_ms,
                "op_type": n.op_type,
                "is_yield": n.is_yield,
                "status": n.status,
                "error": n.error,
                "inputs": clean(n.inputs),
                "outputs": clean(n.outputs),
                "upstreams": [
                    {
                        "from_op_id": u.from_op_id,
                        "from_op_name": u.from_op_name,
                        "from_op_full_name": u.from_op_full_name,
                        "from_key": u.from_key,
                        "to_key": u.to_key,
                    }
                    for u in n.upstreams
                ],
            }
        )
    return rows


def meta_of_trace(trace: Any) -> Dict[str, Any]:
    """A trace's ``meta.json``: what LocalConsumer writes, and what a live
    trace is read as. ``status`` is ``"error"`` when a node failed or the
    run recorded an error; ``errors`` is the run's ``"$errors"``."""
    return {
        "trace_id": trace.trace_id,
        "workflow_name": trace.workflow_name,
        "started_at": trace.started_at,
        "wall_started_at": trace.wall_started_at,
        "ended_at": trace.ended_at,
        "duration_ms": trace.duration_ms,
        "node_count": len(trace.nodes),
        "metadata": trace.metadata,
        "status": trace.status,
        "errors": dict(trace.errors),
    }
