"""`Runbook` — many jobs, one command, wired like a graph.

Stage 2 sometimes needs *all* of stage 1 first (embed everything, then
cluster), and stages run in sequence or side by side. A Runbook states
that the way a `@graph` body states its ops: with `>>`, one statement per
line, as many lines as the flow needs. A list on either side of `>>` is
one wire per element. No branches — a runbook that needs a condition is a
Python function calling ``run()`` twice.

::

    with Runbook("nightly") as nightly:
        fetch >> [score, audit]          # one line, several wires
        score >> [report, export]
        [report, audit] >> notify        # notify waits for both

    fetch ─┬─► score ─┬─► report ─┐
           │          └─► export  │
           └─► audit ─────────────┴─► notify

The runbook is the set of wires from all its lines — a DAG, not a tree:
a job is one node however many wires touch it, and a cycle is refused
when the block closes, naming its jobs. A job starts when every job wired
into it has finished; jobs with no incoming wire start first; jobs not
wired together run side by side. ``Runbook("nightly", a >> [b, c])`` is
the one-expression form of the same thing.

Composition sits **above** the engine — asyncio over job runs — never a
graph of jobs: a graph of jobs that contain graphs is two ideas wearing
one name, and its trace would nest a job inside a run inside a job.

Hand-off between jobs is by naming the same resource: one job's sink and
the next job's source point at the same file. Nothing is rewired at run
time.

**A runbook is a record, never a span.** ``jobs/<runbook>/<run>/run.json``
holds the wires and, for each job, a status, timing and the run id and
path of its own record. Traces stay what they are: one per graph run,
tagged with the job that minted it.
"""

from __future__ import annotations

import asyncio
import json
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union

from operonx.core.loggings import LOGGER

from .job import Job
from .record import RUN_FAILED, RUN_OK, RUN_STOPPED, new_run_id

__all__ = ["Flow", "Runbook", "RunbookRun", "Sequential", "Parallel", "NodeReport"]

#: Job outcomes. ``skipped`` is a job downstream of a failure under ``stop``.
NODE_OK = "ok"
NODE_FAILED = "failed"
NODE_SKIPPED = "skipped"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# -- wiring --------------------------------------------------------------------

#: The runbooks whose ``with`` block is open, innermost last.
_OPEN: ContextVar[Tuple["Runbook", ...]] = ContextVar("operonx_open_runbooks", default=())


def _unique(jobs: Sequence[Job]) -> List[Job]:
    out: List[Job] = []
    for j in jobs:
        if not any(j is k for k in out):
            out.append(j)
    return out


class Flow:
    """Jobs and the wires between them — what ``>>`` and lists build.

    ``heads`` are the jobs a wire *into* this flow reaches, ``tails`` the
    jobs a wire *out of* it leaves from. ``a >> b`` wires a's tails to b's
    heads and returns a flow whose heads are a's and tails are b's, so
    ``a >> [b, c] >> d`` chains as in a graph body.
    """

    __slots__ = ("jobs", "heads", "tails", "wires")

    def __init__(
        self,
        jobs: Sequence[Job],
        heads: Sequence[Job],
        tails: Sequence[Job],
        wires: Sequence[Tuple[Job, Job]] = (),
    ):
        self.jobs = _unique(jobs)
        self.heads = _unique(heads)
        self.tails = _unique(tails)
        self.wires: List[Tuple[Job, Job]] = []
        for w in wires:
            if not any(w[0] is x and w[1] is y for x, y in self.wires):
                self.wires.append(w)

    def __rshift__(self, other: Any) -> "Flow":
        return _then(self, _flow(other))

    def __rrshift__(self, other: Any) -> "Flow":
        return _then(_flow(other), self)

    @property
    def name(self) -> str:
        return " ; ".join(_wire_lines(self.jobs, [(a.name, b.name) for a, b in self.wires]))

    def __repr__(self) -> str:
        return f"Flow({self.name!r})"


def _flow(obj: Any) -> Flow:
    """A Job, a Flow, or a list of them — as a flow."""
    if isinstance(obj, Flow):
        return obj
    if isinstance(obj, Job):
        return Flow([obj], [obj], [obj])
    if isinstance(obj, (list, tuple)):
        if not obj:
            raise ValueError("an empty list wires nothing")
        parts = [_flow(o) for o in obj]
        return Flow(
            [j for p in parts for j in p.jobs],
            [j for p in parts for j in p.heads],
            [j for p in parts for j in p.tails],
            [w for p in parts for w in p.wires],
        )
    raise TypeError(f"a runbook is made of Jobs, `>>` and lists, not {type(obj).__name__}")


def _then(left: Flow, right: Flow) -> Flow:
    out = Flow(
        left.jobs + right.jobs,
        left.heads,
        right.tails,
        left.wires + right.wires + [(t, h) for t in left.tails for h in right.heads],
    )
    for book in _OPEN.get()[-1:]:
        book._add(out)
    return out


def Sequential(*steps: Any) -> Flow:  # noqa: N802 — reads like the class it replaced
    """``Sequential(a, b, c)`` is ``a >> b >> c``."""
    if not steps:
        raise ValueError("Sequential needs at least one step")
    flow = _flow(steps[0])
    for step in steps[1:]:
        flow = _then(flow, _flow(step))
    return flow


def Parallel(*branches: Any) -> Flow:  # noqa: N802
    """``Parallel(a, b)`` is ``[a, b]``: side by side, no wire between."""
    if not branches:
        raise ValueError("Parallel needs at least one branch")
    return _flow(list(branches))


def _wire_lines(order: Sequence[Any], wires: Sequence[Tuple[str, str]]) -> List[str]:
    """The wires as ``>>`` lines: one per source, sources that feed the
    same jobs share a line (``[a, b] >> c``), a job with no wire alone."""
    names = [getattr(j, "name", j) for j in order]
    rank = {n: i for i, n in enumerate(names)}
    targets: Dict[str, List[str]] = {}
    for a, b in wires:
        targets.setdefault(a, []).append(b)
    groups: Dict[Tuple[str, ...], List[str]] = {}
    for src in sorted(targets, key=rank.__getitem__):
        key = tuple(sorted(targets[src], key=rank.__getitem__))
        groups.setdefault(key, []).append(src)

    def side(xs: Sequence[str]) -> str:
        return xs[0] if len(xs) == 1 else f"[{', '.join(xs)}]"

    lines = [f"{side(srcs)} >> {side(list(dsts))}" for dsts, srcs in groups.items()]
    wired = {n for w in wires for n in w}
    lines += [n for n in names if n not in wired]
    return lines


# -- the record ----------------------------------------------------------------


@dataclass
class NodeReport:
    """What one job did in a runbook run (``kind="job"``), or the runbook
    itself with its jobs as children (``kind="runbook"``)."""

    name: str
    kind: str  # job | runbook
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
        """Every job node, in run order."""
        if self.kind == "job":
            return [self]
        return [j for c in self.children for j in c.jobs()]


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
    wires: List[Tuple[str, str]] = field(default_factory=list)
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
        keys = ("runbook", "run_id", "status", "started", "ended", "ms", "tree", "wires")
        return cls(
            name=d["runbook"],
            run_id=d["run_id"],
            path=path,
            status=d["status"],
            started=d["started"],
            ended=d.get("ended"),
            ms=float(d.get("ms", 0.0)),
            report=NodeReport.from_dict(d["tree"]),
            wires=[tuple(w) for w in d.get("wires", [])],
            meta={k: v for k, v in d.items() if k not in keys},
        )

    def __repr__(self) -> str:
        return f"RunbookRun({self.summary()})"


# -- the runbook ---------------------------------------------------------------


class Runbook:
    """See the module docstring.

    Args:
        name: The runbook's name; its record directory is named after it.
        root: The one-expression form — a Job, ``a >> b``, ``a >> [b, c]``.
            Leave it out and wire the runbook in a ``with`` block instead.
        on_error: ``"stop"`` skips every job downstream of a failed one —
            the rest still runs; ``"continue"`` runs downstream anyway.
            A running job is never cancelled.
        record_dir: Where runs are recorded: ``<record_dir>/<name>/<run>``.
        description: One line, for ``--list`` and the studio.
        schedule: When the deployment's cron should run it (a cron line);
            declared, not acted on — ``operonx-run`` is what cron calls.
    """

    def __init__(
        self,
        name: str,
        root: Any = None,
        *,
        on_error: str = "stop",
        record_dir: Union[str, Path] = "jobs",
        description: str = "",
        schedule: Optional[str] = None,
    ):
        if not name or not isinstance(name, str):
            raise ValueError("a runbook needs a name")
        if on_error not in ("stop", "continue"):
            raise ValueError(
                f"runbook {name!r}: on_error must be 'stop' or 'continue', not {on_error!r}"
            )
        self.name = name
        self.on_error = on_error
        self.record_dir = Path(record_dir)
        self.description = description
        self.schedule = schedule
        self._jobs: List[Job] = []
        self._wires: List[Tuple[str, str]] = []
        self._token = None
        if root is not None:
            self._add(_flow(root))
            self._check()

    # -- wiring ----------------------------------------------------------------

    def __enter__(self) -> "Runbook":
        self._token = _OPEN.set(_OPEN.get() + (self,))
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        _OPEN.reset(self._token)
        self._token = None
        if exc_type is None:
            if not self._jobs:
                raise ValueError(f"runbook {self.name!r}: the block wires no jobs")
            self._check()

    def _add(self, flow: Flow) -> None:
        for job in flow.jobs:
            same = next((j for j in self._jobs if j.name == job.name), None)
            if same is None:
                self._jobs.append(job)
            elif same is not job:
                raise ValueError(
                    f"runbook {self.name!r}: a job appears twice: {job.name} "
                    "(two Job objects share the name; a record is named after it)"
                )
        for a, b in flow.wires:
            w = (a.name, b.name)
            if w not in self._wires:
                self._wires.append(w)

    def _check(self) -> None:
        """Refuse a cycle, naming its jobs."""
        after: Dict[str, List[str]] = {j.name: [] for j in self._jobs}
        for a, b in self._wires:
            after[a].append(b)
        state: Dict[str, int] = {}  # 1 = on the path, 2 = done
        path: List[str] = []

        def visit(n: str) -> None:
            state[n] = 1
            path.append(n)
            for m in after[n]:
                if state.get(m) == 1:
                    loop = path[path.index(m) :] + [m]
                    raise ValueError(
                        f"runbook {self.name!r}: the wires make a cycle: {' >> '.join(loop)}"
                    )
                if m not in state:
                    visit(m)
            path.pop()
            state[n] = 2

        for j in self._jobs:
            if j.name not in state:
                visit(j.name)

    # -- what it is --------------------------------------------------------------

    @property
    def jobs(self) -> List[Job]:
        """Every job, in the order a run starts them: wired order, and
        the order they were written in where the wires allow either."""
        rank = {j.name: i for i, j in enumerate(self._jobs)}
        before = {j.name: {a for a, b in self._wires if b == j.name} for j in self._jobs}
        done: List[str] = []
        while len(done) < len(self._jobs):
            ready = [n for n in rank if n not in done and before[n] <= set(done)]
            done.append(min(ready, key=rank.__getitem__))
        by = {j.name: j for j in self._jobs}
        return [by[n] for n in done]

    @property
    def wires(self) -> List[Tuple[str, str]]:
        """Every wire, in the order the jobs run."""
        rank = {j.name: i for i, j in enumerate(self.jobs)}
        return sorted(self._wires, key=lambda w: (rank[w[0]], rank[w[1]]))

    def tree(self) -> str:
        """The runbook as its wires, one ``>>`` line per source."""
        return "\n".join(_wire_lines(self.jobs, self.wires))

    def describe(self) -> Dict[str, Any]:
        return {
            "jobs": [j.name for j in self.jobs],
            "wires": [list(w) for w in self.wires],
            "on_error": self.on_error,
            "description": self.description,
            "schedule": self.schedule,
        }

    def main(self, argv: Optional[Sequence[str]] = None, *, doc: Optional[str] = None) -> int:
        """This runbook as a command line; returns the exit status (see
        ``Job.main``). ``--set key=value`` reaches every job's inputs."""
        from operonx.cli.run import main_for

        return main_for(self, argv, doc=doc)

    # -- running -------------------------------------------------------------------

    async def run(self, *, resume: bool = False) -> RunbookRun:
        """Run every job once, each when its inbound wires have finished,
        and return the record."""
        if not self._jobs:
            raise ValueError(f"runbook {self.name!r} has no jobs")
        order = self.jobs
        before = {j.name: [a for a, b in self._wires if b == j.name] for j in order}
        done: Dict[str, asyncio.Future] = {
            j.name: asyncio.get_running_loop().create_future() for j in order
        }

        run_id = new_run_id()
        path = self.record_dir / self.name / run_id
        path.mkdir(parents=True, exist_ok=False)
        started = _now()
        t0 = perf_counter()
        LOGGER.info(
            f"[runbook:{self.name}] {len(order)} job(s), {len(self._wires)} wire(s), "
            f"on_error={self.on_error}" + (", resume" if resume else "")
        )
        self._write(path, run_id, "running", started, None, 0.0, None)

        async def one(job: Job) -> NodeReport:
            upstream = [await done[n] for n in before[job.name]]
            if self.on_error == "stop" and any(r.status != NODE_OK for r in upstream):
                report = NodeReport(job.name, "job", NODE_SKIPPED)
            else:
                report = await self._run_job(job, resume)
            done[job.name].set_result(report)
            return report

        reports = await asyncio.gather(*(one(j) for j in order))

        statuses = {r.status for r in reports}
        if NODE_SKIPPED in statuses:
            status = RUN_STOPPED
        elif NODE_FAILED in statuses:
            status = RUN_FAILED
        else:
            status = RUN_OK
        ms = (perf_counter() - t0) * 1000
        root = NodeReport(
            self.name,
            "runbook",
            NODE_OK if status == RUN_OK else NODE_FAILED,
            started=started,
            ended=_now(),
            ms=ms,
            children=list(reports),
        )
        self._write(path, run_id, status, started, root.ended, ms, root)
        run = RunbookRun.load(path)
        LOGGER.info(f"[runbook:{self.name}] {run.summary()}  {path}")
        return run

    async def _run_job(self, job: Job, resume: bool) -> NodeReport:
        report = NodeReport(job.name, "job", NODE_OK, started=_now())
        t0 = perf_counter()
        try:
            run = await job.run(resume=resume and job.session == "per_item")
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
            LOGGER.error(f"[runbook:{self.name}] job {job.name!r} raised: {report.error}")
        report.ended = _now()
        report.ms = (perf_counter() - t0) * 1000
        return report

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
        root: Optional[NodeReport],
    ) -> None:
        if root is None:  # before the run: every job, not yet started
            root = NodeReport(
                self.name,
                "runbook",
                NODE_SKIPPED,
                children=[NodeReport(j.name, "job", NODE_SKIPPED) for j in self.jobs],
            )
        payload = {
            "runbook": self.name,
            "run_id": run_id,
            "status": status,
            "started": started,
            "ended": ended,
            "ms": round(ms, 3),
            "on_error": self.on_error,
            "description": self.description,
            "schedule": self.schedule,
            "wires": [list(w) for w in self.wires],
            "tree": root.as_dict(),
        }
        tmp = path / "run.json.tmp"
        tmp.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
        tmp.replace(path / "run.json")

    def __repr__(self) -> str:
        wiring = " ; ".join(_wire_lines(self.jobs, self.wires))
        return f"Runbook({self.name!r}, {wiring!r}, on_error={self.on_error!r})"


def runs_of(root: Union[str, Path], name: str) -> List[Path]:
    base = Path(root) / name
    if not base.is_dir():
        return []
    return sorted(p for p in base.iterdir() if p.is_dir() and (p / "run.json").exists())


def last_run(root: Union[str, Path], name: str) -> Optional[RunbookRun]:
    runs = runs_of(root, name)
    return RunbookRun.load(runs[-1]) if runs else None
