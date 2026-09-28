"""The run store contract — where finished runs live, and how they are asked for.

Deliberately narrow, in the spirit of the doc-store contract next door:
write one run, list runs by a :class:`~.model.RunFilter`, read one run
fully, roll ops up across runs, delete by filter. No query language, no
joins, no updates — a backend implements five methods natively, and
anything past that line is the caller's own code over a backend's client.

A store **is** a trace consumer: its :meth:`consume` writes the finished
trace, so ``Operon(graph, trace="run_store:default")`` needs no adapter,
and there is exactly one capture path — the engine's — into every store.

The methods are synchronous. Both callers are: the engine runs consumers
in a worker thread, and the studio's routes are plain functions. Async
code wraps a call in ``asyncio.to_thread``.
"""

from __future__ import annotations

from abc import abstractmethod
from typing import Any, Dict, List, Optional, Sequence, Tuple

from operonx.telemetry.consumer import Consumer

from .model import OpRollup, OpStats, Page, RunFilter, RunRecord, RunSummary, percentile

__all__ = ["GROUP_FIELDS", "ORDERS", "RunStore", "combine_rollups"]

#: How :meth:`RunStore.list_runs` can order.
ORDERS = ("started_desc", "started_asc", "duration_desc", "cost_desc", "errors_desc")
#: What :meth:`RunStore.groups` can group by.
GROUP_FIELDS = (
    "origin",
    "name",
    "status",
    "version",
    "job_run",
    "runbook",
    "runbook_run",
    "service",
    "job",
    "workflow",
)


def _check_by(by: Sequence[str]) -> Tuple[str, ...]:
    by = tuple(by)
    bad = [f for f in by if f not in GROUP_FIELDS]
    if not by or bad:
        raise ValueError(f"group by {bad or 'nothing'}; one or more of {', '.join(GROUP_FIELDS)}")
    return by


class RunStore(Consumer):
    """See the module docstring."""

    #: False for a store that only reads (a remote tracer's API).
    writable: bool = True

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        super().__init__(config=config or {})

    # -- write -------------------------------------------------------------

    def consume(self, trace: Any) -> Any:
        """A finished run arrives from the engine: store it."""
        return self.put_trace(trace)

    @abstractmethod
    def put_trace(self, trace: Any) -> RunSummary:
        """Store one finished ``WorkflowTrace``; return its summary."""

    # -- read --------------------------------------------------------------

    @abstractmethod
    def list_runs(
        self,
        where: Optional[RunFilter] = None,
        order: str = "started_desc",
        limit: int = 50,
        cursor: Optional[str] = None,
    ) -> Page:
        """Runs matching *where*, one page at a time."""

    @abstractmethod
    def get_run(self, trace_id: str) -> Optional[RunRecord]:
        """One run fully, or ``None`` when the store has no such run."""

    @abstractmethod
    def rollups(self, where: Optional[RunFilter] = None) -> List[OpRollup]:
        """Every per-op, per-run rollup of the runs *where* matches."""

    def op_stats(self, where: Optional[RunFilter] = None) -> List[OpStats]:
        """Each op across the runs *where* matches — slowest total first.
        Backends with a query engine may override; the default combines
        :meth:`rollups` in Python."""
        return combine_rollups(self.rollups(where))

    def groups(
        self, where: Optional[RunFilter] = None, by: Sequence[str] = ("origin", "name")
    ) -> List[Dict[str, Any]]:
        """Runs *where* matches, counted per group of *by* (columns from
        :data:`GROUP_FIELDS`): ``runs``, ``errors``, ``first_started``,
        ``last_started``, ``cost_usd`` (None when nothing was priced) and
        ``duration_ms`` (summed). Newest group first. SQL backends
        override; this default pages through :meth:`list_runs`."""
        by = _check_by(by)
        acc: Dict[tuple, Dict[str, Any]] = {}
        cursor = None
        while True:
            page = self.list_runs(where, limit=500, cursor=cursor)
            for s in page.items:
                key = tuple(getattr(s, f) for f in by)
                g = acc.get(key)
                if g is None:
                    g = acc[key] = {
                        **dict(zip(by, key)),
                        "runs": 0,
                        "errors": 0,
                        "first_started": s.started_at,
                        "last_started": s.started_at,
                        "cost_usd": None,
                        "duration_ms": 0.0,
                    }
                g["runs"] += 1
                g["errors"] += 1 if s.status == "error" else 0
                g["first_started"] = min(g["first_started"], s.started_at)
                g["last_started"] = max(g["last_started"], s.started_at)
                g["duration_ms"] += s.duration_ms or 0.0
                if s.cost_usd is not None:
                    g["cost_usd"] = (g["cost_usd"] or 0.0) + s.cost_usd
            cursor = page.next_cursor
            if not cursor:
                break
        return sorted(acc.values(), key=lambda g: -g["last_started"])

    # -- housekeeping ------------------------------------------------------

    @abstractmethod
    def delete_runs(self, where: RunFilter) -> int:
        """Remove the runs *where* matches; return how many."""

    def count(self, where: Optional[RunFilter] = None) -> int:
        """How many runs *where* matches."""
        page = self.list_runs(where, limit=1)
        if page.total is not None:
            return page.total
        n, cursor = 0, None
        while True:
            page = self.list_runs(where, limit=500, cursor=cursor)
            n += len(page.items)
            cursor = page.next_cursor
            if not cursor:
                return n

    def refresh(self) -> int:
        """Pick up runs written by something other than this store (the
        files backend indexes directories a plain LocalConsumer wrote).
        Returns how many were added; most backends have nothing to do."""
        return 0

    def close(self) -> None:
        """Release connections. Idempotent."""


def combine_rollups(rollups: List[OpRollup]) -> List[OpStats]:
    """Per-run rollups → one row per op across runs."""
    by: Dict[str, OpStats] = {}
    samples: Dict[str, List[float]] = {}
    runs: Dict[str, set] = {}
    for r in rollups:
        st = by.get(r.op)
        if st is None:
            st = by[r.op] = OpStats(op=r.op, op_type=r.op_type)
            samples[r.op] = []
            runs[r.op] = set()
        runs[r.op].add(r.trace_id)
        st.count += r.count
        st.total_ms += r.total_ms
        st.max_ms = max(st.max_ms, r.max_ms)
        st.errors += r.errors
        st.unpriced += r.unpriced
        st.tokens_in += r.tokens_in
        st.tokens_out += r.tokens_out
        if r.cost_usd is not None:
            st.cost_usd = (st.cost_usd or 0.0) + r.cost_usd
        samples[r.op].extend(r.samples)
        st.exact = st.exact and r.exact
        if not st.op_type and r.op_type:
            st.op_type = r.op_type
    out = []
    for op, st in by.items():
        st.runs = len(runs[op])
        st.avg_ms = st.total_ms / st.count if st.count else 0.0
        xs = samples[op]
        st.p50_ms = percentile(xs, 50)
        st.p95_ms = percentile(xs, 95)
        st.p99_ms = percentile(xs, 99)
        out.append(st)
    out.sort(key=lambda s: -s.total_ms)
    return out
