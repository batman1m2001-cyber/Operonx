"""`Runbook` — many jobs, one command.

Stage 2 sometimes needs *all* of stage 1 first (embed everything, then
cluster), and stages run in sequence or side by side. A Runbook composes
jobs **above** the engine — a tree of `Sequential` and `Parallel`, walked
by asyncio — never as a graph of jobs: a graph of jobs that contain
graphs is two ideas wearing one name, and its trace would nest a job
inside a run inside a job.

::

    nightly = Runbook("nightly", extract >> [embed >> cluster, score])
    #                             >> = Sequential      [ ] = Parallel

      extract ──► ┬─► embed ──► cluster ─┐
                  └─► score ─────────────┴─► done

Hand-off between stages is by naming the same resource: ``extract``'s
sink and ``embed``'s source both point at the same file. Nothing is
rewired at run time. A runbook that needs a condition is a Python
function calling ``run()`` twice.

**A runbook is a record, never a span.** ``jobs/<runbook>/<run>/run.json``
holds the tree with a status, timing and — for each job — the run id and
path of its own record. Traces stay what they are: one per graph run,
tagged with the job that minted it.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter
from typing import Any, Dict, List, Optional, Union

from operonx.core.loggings import LOGGER

from .job import Job
from .record import RUN_FAILED, RUN_OK, RUN_STOPPED, new_run_id

__all__ = ["Runbook", "RunbookRun", "Sequential", "Parallel", "Node", "NodeReport"]

#: Node outcomes. ``skipped`` is a node a stopped sequence never reached.
NODE_OK = "ok"
NODE_FAILED = "failed"
NODE_SKIPPED = "skipped"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class NodeReport:
    """What one node of the tree did."""

    name: str
    kind: str  # job | sequential | parallel
    status: str  # ok | failed | skipped
    started: Optional[str] = None
    ended: Optional[str] = None
    ms: float = 0.0
    error: Optional[str] = None
    run_id: Optional[str] = None  # a job's own record
    path: Optional[str] = None
    children: List["NodeReport"] = field(default_factory=list)

    def as_dict(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {"name": self.name, "kind": self.kind, "status": self.status}
        for k in ("started", "ended", "error", "run_id", "path"):
            v = getattr(self, k)
            if v is not None:
                out[k] = v
        out["ms"] = round(self.ms, 3)
        if self.children:
            out["children"] = [c.as_dict() for c in self.children]
        return out

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "NodeReport":
        return cls(
            name=d["name"],
            kind=d["kind"],
            status=d["status"],
            started=d.get("started"),
            ended=d.get("ended"),
            ms=float(d.get("ms", 0.0)),
            error=d.get("error"),
            run_id=d.get("run_id"),
            path=d.get("path"),
            children=[cls.from_dict(c) for c in d.get("children", [])],
        )

    def jobs(self) -> List["NodeReport"]:
        """Every job node, in tree order."""
        if self.kind == "job":
            return [self]
        return [j for c in self.children for j in c.jobs()]


class _Ctx:
    """What a run of the tree shares: the policy, and whether it stopped."""

    def __init__(self, resume: bool, on_error: str):
        self.resume = resume
        self.on_error = on_error
        self.stopped = asyncio.Event()


class Node:
    """A place in the tree. `>>` chains, a list beside `>>` fans out."""

    name: str
    kind: str

    async def _run(self, ctx: _Ctx) -> NodeReport:  # pragma: no cover - abstract
        raise NotImplementedError

    def _skipped(self) -> NodeReport:
        return NodeReport(
            self.name,
            self.kind,
            NODE_SKIPPED,
            children=[c._skipped() for c in getattr(self, "children", ())],
        )

    def jobs(self) -> List[Job]:  # pragma: no cover - abstract
        raise NotImplementedError

    def tree(self, indent: int = 0) -> List[str]:  # pragma: no cover - abstract
        raise NotImplementedError

    # -- composition -------------------------------------------------------

    def __rshift__(self, other: Any) -> "Sequential":
        return Sequential(self, _node(other))

    def __rrshift__(self, other: Any) -> "Sequential":
        return Sequential(_node(other), self)


def _node(obj: Any) -> Node:
    """A Job, a Node, or a list (Parallel) — as a node."""
    if isinstance(obj, Node):
        return obj
    if isinstance(obj, Job):
        return _JobNode(obj)
    if isinstance(obj, (list, tuple)):
        return Parallel(*obj)
    raise TypeError(f"a runbook is made of Jobs, Sequential and Parallel, not {type(obj).__name__}")


class _JobNode(Node):
    """A Job, as a leaf. Runs it; reports its record."""

    kind = "job"

    def __init__(self, job: Job):
        self.job = job
        self.name = job.name

    async def _run(self, ctx: _Ctx) -> NodeReport:
        report = NodeReport(self.name, self.kind, NODE_OK, started=_now())
        t0 = perf_counter()
        try:
            resume = ctx.resume and self.job.session == "per_item"
            run = await self.job.run(resume=resume)
            report.run_id, report.path = run.run_id, str(run.path)
            if run.status != RUN_OK:
                report.status = NODE_FAILED
                c = run.counts
                report.error = run.meta.get("error") or (
                    f"{run.status}: {c.get('failed', 0)} failed"
                    + (f", {c['timeout']} timed out" if c.get("timeout") else "")
                )
        except Exception as exc:  # noqa: BLE001
            report.status = NODE_FAILED
            report.error = f"{type(exc).__name__}: {exc}"
            LOGGER.error(f"[runbook] job {self.name!r} raised: {report.error}")
        report.ended = _now()
        report.ms = (perf_counter() - t0) * 1000
        return report

    def jobs(self) -> List[Job]:
        return [self.job]

    def tree(self, indent: int = 0) -> List[str]:
        return [f"{'  ' * indent}{self.name}  ({self.job.session})"]


class Sequential(Node):
    """Each child after the previous one *succeeds*. A failed child ends
    the sequence unless the runbook's ``on_error`` is ``continue``; the
    children it never reached are reported ``skipped``."""

    kind = "sequential"

    def __init__(self, *children: Any):
        flat: List[Node] = []
        for c in children:
            n = _node(c)
            flat.extend(n.children if isinstance(n, Sequential) else [n])
        if not flat:
            raise ValueError("Sequential needs at least one step")
        self.children: List[Node] = flat
        self.name = " >> ".join(c.name for c in flat)

    async def _run(self, ctx: _Ctx) -> NodeReport:
        report = NodeReport(self.name, self.kind, NODE_OK, started=_now())
        t0 = perf_counter()
        remaining = list(self.children)
        while remaining:
            child = remaining.pop(0)
            if ctx.stopped.is_set():
                report.children.append(child._skipped())
                continue
            r = await child._run(ctx)
            report.children.append(r)
            if r.status == NODE_FAILED:
                report.status = NODE_FAILED
                if ctx.on_error == "stop":
                    ctx.stopped.set()
        report.ended = _now()
        report.ms = (perf_counter() - t0) * 1000
        return report

    def jobs(self) -> List[Job]:
        return [j for c in self.children for j in c.jobs()]

    def tree(self, indent: int = 0) -> List[str]:
        lines = [f"{'  ' * indent}sequential"]
        for c in self.children:
            lines.extend(c.tree(indent + 1))
        return lines


class Parallel(Node):
    """All children at once; done when the last one is. A failure fails
    the node but never cancels a sibling: what is running finishes and
    leaves its record."""

    kind = "parallel"

    def __init__(self, *children: Any):
        nodes = [_node(c) for c in children]
        if not nodes:
            raise ValueError("Parallel needs at least one branch")
        self.children: List[Node] = nodes
        self.name = " | ".join(c.name for c in nodes)

    async def _run(self, ctx: _Ctx) -> NodeReport:
        report = NodeReport(self.name, self.kind, NODE_OK, started=_now())
        t0 = perf_counter()
        results = await asyncio.gather(*(c._run(ctx) for c in self.children))
        report.children = list(results)
        if any(r.status == NODE_FAILED for r in results):
            report.status = NODE_FAILED
            if ctx.on_error == "stop":
                ctx.stopped.set()
        report.ended = _now()
        report.ms = (perf_counter() - t0) * 1000
        return report

    def jobs(self) -> List[Job]:
        return [j for c in self.children for j in c.jobs()]

    def tree(self, indent: int = 0) -> List[str]:
        lines = [f"{'  ' * indent}parallel"]
        for c in self.children:
            lines.extend(c.tree(indent + 1))
        return lines


# -- the record --------------------------------------------------------------


@dataclass
class RunbookRun:
    """A finished runbook run, read back from its run.json."""

    name: str
    run_id: str
    path: Path
    status: str  # ok | failed | stopped
    started: str
    ended: Optional[str]
    ms: float
    report: NodeReport
    meta: Dict[str, Any] = field(default_factory=dict)

    @property
    def jobs(self) -> List[NodeReport]:
        return self.report.jobs()

    def counts(self) -> Dict[str, int]:
        out = {NODE_OK: 0, NODE_FAILED: 0, NODE_SKIPPED: 0}
        for j in self.jobs:
            out[j.status] = out.get(j.status, 0) + 1
        return out

    def summary(self) -> str:
        c = self.counts()
        return (
            f"{self.name} {self.run_id} {self.status}  "
            f"jobs ok={c[NODE_OK]} failed={c[NODE_FAILED]} skipped={c[NODE_SKIPPED]}"
        )

    @classmethod
    def load(cls, path: Union[str, Path]) -> "RunbookRun":
        path = Path(path)
        d = json.loads((path / "run.json").read_text(encoding="utf-8"))
        return cls(
            name=d["runbook"],
            run_id=d["run_id"],
            path=path,
            status=d["status"],
            started=d["started"],
            ended=d.get("ended"),
            ms=float(d.get("ms", 0.0)),
            report=NodeReport.from_dict(d["tree"]),
            meta={
                k: v
                for k, v in d.items()
                if k not in ("runbook", "run_id", "status", "started", "ended", "ms", "tree")
            },
        )

    def __repr__(self) -> str:
        return f"RunbookRun({self.summary()})"


class Runbook:
    """See the module docstring.

    Args:
        name: The runbook's name; its record directory is named after it.
        root: The tree — a Job, ``a >> b``, ``a >> [b, c]``, or an explicit
            ``Sequential`` / ``Parallel``.
        on_error: ``"stop"`` ends a sequence at its first failed step;
            ``"continue"`` runs every step regardless. Parallel branches
            always run to completion.
        record_dir: Where runs are recorded: ``<record_dir>/<name>/<run>``.
        description: One line, for ``--list`` and the studio.
    """

    def __init__(
        self,
        name: str,
        root: Any,
        *,
        on_error: str = "stop",
        record_dir: Union[str, Path] = "jobs",
        description: str = "",
    ):
        if not name or not isinstance(name, str):
            raise ValueError("a runbook needs a name")
        if on_error not in ("stop", "continue"):
            raise ValueError(
                f"runbook {name!r}: on_error must be 'stop' or 'continue', not {on_error!r}"
            )
        self.name = name
        self.root: Node = _node(root)
        self.on_error = on_error
        self.record_dir = Path(record_dir)
        self.description = description
        names = [j.name for j in self.root.jobs()]
        dupes = sorted({n for n in names if names.count(n) > 1})
        if dupes:
            raise ValueError(f"runbook {name!r}: a job appears twice: {', '.join(dupes)}")

    @property
    def jobs(self) -> List[Job]:
        return self.root.jobs()

    def tree(self) -> str:
        return "\n".join(self.root.tree())

    async def run(self, *, resume: bool = False) -> RunbookRun:
        """Walk the tree once and return the record."""
        ctx = _Ctx(resume=resume, on_error=self.on_error)
        run_id = new_run_id()
        path = self.record_dir / self.name / run_id
        path.mkdir(parents=True, exist_ok=False)
        started = _now()
        t0 = perf_counter()
        LOGGER.info(
            f"[runbook:{self.name}] {len(self.jobs)} job(s), on_error={self.on_error}"
            + (", resume" if resume else "")
        )
        self._write(path, run_id, "running", started, None, 0.0, None)

        report = await self.root._run(ctx)

        if ctx.stopped.is_set():
            status = RUN_STOPPED
        elif report.status == NODE_FAILED:
            status = RUN_FAILED
        else:
            status = RUN_OK
        ms = (perf_counter() - t0) * 1000
        self._write(path, run_id, status, started, _now(), ms, report)
        run = RunbookRun.load(path)
        LOGGER.info(f"[runbook:{self.name}] {run.summary()}  {path}")
        return run

    def run_sync(self, *, resume: bool = False) -> RunbookRun:
        return asyncio.run(self.run(resume=resume))

    def _write(
        self,
        path: Path,
        run_id: str,
        status: str,
        started: str,
        ended: Optional[str],
        ms: float,
        report: Optional[NodeReport],
    ) -> None:
        payload = {
            "runbook": self.name,
            "run_id": run_id,
            "status": status,
            "started": started,
            "ended": ended,
            "ms": round(ms, 3),
            "on_error": self.on_error,
            "description": self.description,
            "tree": (report or self.root._skipped()).as_dict(),
        }
        tmp = path / "run.json.tmp"
        tmp.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
        tmp.replace(path / "run.json")

    def describe(self) -> Dict[str, Any]:
        return {
            "jobs": [j.name for j in self.jobs],
            "on_error": self.on_error,
            "description": self.description,
        }

    def __repr__(self) -> str:
        return f"Runbook({self.name!r}, {self.root.name!r}, on_error={self.on_error!r})"


def runs_of(root: Union[str, Path], name: str) -> List[Path]:
    base = Path(root) / name
    if not base.is_dir():
        return []
    return sorted(p for p in base.iterdir() if p.is_dir() and (p / "run.json").exists())


def last_run(root: Union[str, Path], name: str) -> Optional[RunbookRun]:
    runs = runs_of(root, name)
    return RunbookRun.load(runs[-1]) if runs else None
