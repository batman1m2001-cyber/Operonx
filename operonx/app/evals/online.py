"""Online evaluation — judge production runs after the fact, from the run store.

An online eval is a :class:`~operonx.app.jobs.Job` like an :class:`Eval`, but
its items are runs that already happened: a service's calls, a job's items,
whatever a :class:`~operonx.telemetry.runs.RunFilter` selects. Each pass
reads the runs stored since the last one, keeps a stable sample, and judges
each kept run with reference-free evaluators — there is no expected answer
for production traffic — writing one score per check to a score store::

    online = OnlineEval(
        "call_quality",
        runs={"origin": "service", "name": "call"},
        store="run_store:default",
        evaluators=[no_dead_air, judge("llm:judge", "Was the agent polite?")],
        scores="score_store:default",
        sample=0.05,
        budget_usd_per_day=2.0,
        queue={"to": "call_failures", "when": "any_failed"},
    )
    await online.run()      # one pass; cron calls `operonx run call_quality`

**Never inline.** Nothing here runs on a service's path: the service writes
its runs to the store as it always does, and this reads them later. A
service's latency cannot change because of an online eval.

**Which runs.** A pass pages the store from its cursor (oldest first), skips
eval runs (its own judges' among them), and stops at the first run still
running — that run holds the cursor, so it is judged once it has ended. The
cursor is the last ``started_at`` passed and the trace ids at it, kept in
``<record_dir>/<name>/cursor.json``; a crash re-reads from the last saved
cursor, and scores' ids (by trace, check and evaluator version) make the
overlap write the same rows again. :meth:`OnlineEval.backfill` judges a past
window without touching the cursor — a new judge tried on last week's
traffic.

**Sampling** is stable: :func:`sampled` keeps a trace when ``sha1(trace_id)``
falls under the rate, so a second worker, a re-run and a backfill keep the
same runs.

**Budget.** ``budget_usd_per_day`` caps what the judges spend in a UTC day,
across hosts: the pass starts from the day's spend the score store already
holds for this rule and counts as it goes — a judge still running counting
at the day's cost per judged run, so items judged at once cannot all see
the same total. A run is judged only while its estimated cost still fits:
spend can pass the budget by the estimate's error, not by a pass's
concurrency. Past it, judges are not asked; code checks still run, and the
item says ``budget_exhausted``.

**What an evaluator gets:** ``input`` and ``output`` (the run's request and
answer, :attr:`TraceView.input` / :attr:`TraceView.output`), ``trace`` (the
:class:`TraceView`), ``trace_summary``, ``run`` (the run's summary) and
``judging``. One that cannot judge without ``expected``, ``case`` or ``row``
(a judge with ``reference=True``, a function whose ``expected`` has no
default) is refused when the eval is made; a judge's default
``reference="auto"`` judges without one.
"""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple, Union

from operonx.core import END, START, Operon, graph, op
from operonx.telemetry.runs.model import RunFilter, RunSummary
from operonx.telemetry.scores import ScoreFilter, ScoreStore

from ..jobs import Job
from ..jobs.record import ITEM_EMPTY, ITEM_OK
from .evaluators import _name, judge_all, prepare
from .fingerprint import evaluator_version
from .job import _check_scores, _clip, _open_scores
from .judges import Judging, evaluator_of
from .publish import ScoreWriter, check_score, warn_lost
from .rescoring import is_judge
from .traceview import TraceView

__all__ = ["Cursor", "OnlineEval", "RunStoreSource", "sampled"]

#: What an online evaluator may take. ``expected``, ``case`` and ``row`` are
#: an experiment's: production traffic has no expected answer.
OFFLINE_ONLY = ("expected", "case", "row", "outputs")
#: Targets an online score can have.
TARGETS = ("trace", "session")
#: When a judged run goes to the queue.
QUEUE_WHEN = ("any_failed", "all")
#: How much of a run's input and output a score keeps.
SNAPSHOT_CHARS = 2000
#: An item judges were not asked for, because the day's budget was spent.
BUDGET_EXHAUSTED = "budget_exhausted"


def sampled(trace_id: str, rate: float, salt: str = "") -> bool:
    """Whether *trace_id* is in a *rate* sample — the same answer on every
    host and every pass. *salt* draws an independent sample (a queue's
    sample of an online eval's failures)."""
    if rate >= 1:
        return True
    if rate <= 0:
        return False
    digest = hashlib.sha1(f"{salt}{trace_id}".encode()).hexdigest()
    return int(digest[:8], 16) / 2**32 < rate


@dataclass
class Cursor:
    """Where the last pass stopped: the ``started_at`` it got to, and the
    trace ids at exactly that time it already passed (runs can share a
    timestamp; a page boundary between them must lose neither)."""

    started_at: float = 0.0
    trace_ids: List[str] = field(default_factory=list)

    def passed(self, s: RunSummary) -> bool:
        return s.started_at < self.started_at or (
            s.started_at == self.started_at and s.trace_id in self.trace_ids
        )

    def advance(self, s: RunSummary) -> None:
        if s.started_at > self.started_at:
            self.started_at, self.trace_ids = s.started_at, [s.trace_id]
        elif s.trace_id not in self.trace_ids:
            self.trace_ids.append(s.trace_id)

    @classmethod
    def load(cls, path: Path) -> "Cursor":
        if not path.is_file():
            return cls()
        data = json.loads(path.read_text(encoding="utf-8"))
        return cls(float(data.get("started_at") or 0.0), list(data.get("trace_ids") or []))

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(asdict(self)), encoding="utf-8")
        tmp.replace(path)


class RunStoreSource:
    """Runs from a run store, oldest first, from a cursor — a job source.

    Yields each sampled run's :class:`RunSummary`. Runs of ``skip_origins``
    (eval runs: experiments and judges) are passed over; the first run
    still running ends the pass without moving past it. :attr:`cursor` is
    where the pass got to, every passed run counted, sampled or not.
    """

    def __init__(
        self,
        store: Any,
        where: Optional[RunFilter] = None,
        *,
        cursor: Optional[Cursor] = None,
        until: Optional[float] = None,
        sample: float = 1.0,
        page: int = 500,
        skip_origins: Sequence[str] = ("eval",),
    ):
        self.store = store
        self.where = where or RunFilter()
        self.cursor = cursor or Cursor()
        self.until = until
        self.sample = float(sample)
        self.page = int(page)
        self.skip_origins = tuple(skip_origins)
        #: Runs passed over because they were not in the sample.
        self.unsampled = 0

    async def items(self):
        since = self.cursor.started_at or self.where.since
        where = replace(self.where, since=since, until=self.until or self.where.until)
        token: Optional[str] = None
        while True:
            page = await asyncio.to_thread(
                self.store.list_runs, where, "started_asc", self.page, token
            )
            for s in page.items:
                if self.cursor.passed(s):
                    continue
                if s.status == "running":
                    return  # it holds the cursor: judged once it has ended
                self.cursor.advance(s)
                if s.origin in self.skip_origins:
                    continue
                if not sampled(s.trace_id, self.sample):
                    self.unsampled += 1
                    continue
                yield s
            token = page.next_cursor
            if not token:
                return

    def __repr__(self) -> str:
        return f"runs({type(self.store).__name__}, sample={self.sample:g})"


@op
def pick(run: Any = None) -> dict:
    """The run a pass judges, by its trace id."""
    return {"trace_id": run.trace_id}


@graph
def online_pass(run):
    p = pick(run=run)
    START >> p >> END


class OnlineEval(Job):
    """Reference-free evaluators over stored runs, one pass per call.

    Args:
        name: The rule's name; every score it writes carries it as ``rule``.
        runs: Which runs — a :class:`RunFilter` or its fields as a mapping
            (``{"origin": "service", "name": "call"}``).
        store: The run store the runs are in: a store, or a
            ``"run_store:<name>"`` key.
        evaluators: Functions or judges, as an :class:`Eval` takes them —
            reference-free (see the module docstring).
        scores: Where the scores go: a ScoreStore, a
            ``"score_store:<name>"`` key, or a spec. Required: the budget
            and the trends read it.
        sample: The share of runs judged, in (0, 1].
        target: ``"trace"`` scores each run; ``"session"`` scores its
            conversation (the run's ``session_id`` metadata).
        budget_usd_per_day: What judges may spend in a UTC day.
        queue: ``{"to": "<queue>", "when": "any_failed" | "all",
            "sample": 1.0}`` — judged runs to put in a review queue.
        queues_dir: Where queues live (``.operonx/queues``).
        trace: Trace consumers for the judges' runs.
        judge_concurrency: Judge runs in flight at once.
        record_dir: Where passes are recorded; the cursor is kept there.
    """

    origin = "eval"
    items_fail_run = False  # a failed pass is a failed verdict, not a broken job
    folder = "online"

    def __init__(
        self,
        name: str,
        *,
        runs: Union[RunFilter, Mapping[str, Any], None] = None,
        store: Any,
        evaluators: Sequence[Any],
        scores: Any,
        sample: float = 1.0,
        target: str = "trace",
        budget_usd_per_day: Optional[float] = None,
        queue: Optional[Mapping[str, Any]] = None,
        queues_dir: Union[str, Path] = ".operonx/queues",
        trace: Any = (),
        judge_concurrency: int = 4,
        record_dir: Union[str, Path, None] = None,
        since: Optional[float] = None,
        until: Optional[float] = None,
        **kwargs: Any,
    ):
        if not 0 < float(sample) <= 1:
            raise ValueError(f"online eval {name!r}: sample is a share of runs in (0, 1]")
        if target not in TARGETS:
            raise ValueError(
                f"online eval {name!r}: target is {target!r}; one of {', '.join(TARGETS)}"
            )
        if budget_usd_per_day is not None and float(budget_usd_per_day) < 0:
            raise ValueError(f"online eval {name!r}: budget_usd_per_day is a spend, ≥ 0")
        if scores is None:
            raise ValueError(
                f"online eval {name!r} needs scores= (a score store): its scores, its "
                "budget and its trends live there"
            )
        self.runs = runs if isinstance(runs, RunFilter) else RunFilter(**dict(runs or {}))
        self.store = store
        self.evaluators = [evaluator_of(ev) for ev in evaluators]
        if not self.evaluators:
            raise ValueError(f"online eval {name!r} has no evaluators")
        self._prepared = [prepare(ev) for ev in self.evaluators]
        for p in self._prepared:
            asks = [a for a in OFFLINE_ONLY if _requires(p, a)]
            if asks:
                raise ValueError(
                    f"online eval {name!r}: evaluator {p.name!r} takes {asks}, which only "
                    "an experiment has — a production run has no expected answer. Online "
                    "evaluators take input, output, trace, trace_summary, run and judging"
                )
        self._judges = {p.name for p in self._prepared if is_judge(p.ev)}
        self._versions = {p.name: evaluator_version(p.ev) for p in self._prepared}
        self.sample = float(sample)
        self.target = target
        self.budget_usd_per_day = (
            float(budget_usd_per_day) if budget_usd_per_day is not None else None
        )
        self.queue = _check_queue(name, queue)
        self.queues_dir = Path(queues_dir)
        self.scores = _check_scores(name, scores)
        self.judge_trace = trace
        self.judge_concurrency = int(judge_concurrency)
        self.since, self.until = since, until
        self._source: Optional[RunStoreSource] = None
        self._run_store: Any = None
        self._store: Optional[ScoreStore] = None
        self._writer: Optional[ScoreWriter] = None
        self._judging: Optional[Judging] = None
        self._budget: Optional[_Budget] = None
        self._day = ""
        self._tally: Dict[str, Dict[str, int]] = {}
        self._queued = 0
        self._exhausted = 0

        super().__init__(
            name,
            # the item's run is judged in `judge`; the graph only hands it on, untraced
            graph=Operon(online_pass, params={"run": None}, trace=[]),
            items=self._runs,
            key=lambda run: run.trace_id,
            input="run",
            record_dir=record_dir,
            **kwargs,
        )

    # -- a pass ------------------------------------------------------------------

    @property
    def cursor_path(self) -> Path:
        return self.records() / self.name / "cursor.json"

    @property
    def is_backfill(self) -> bool:
        return self.since is not None

    def backfill(self, since: float, until: Optional[float] = None) -> "OnlineEval":
        """The same eval over the runs started in ``[since, until)``, leaving
        the cursor where it is."""
        twin = OnlineEval(
            self.name,
            runs=self.runs,
            store=self.store,
            evaluators=self.evaluators,
            scores=self.scores,
            sample=self.sample,
            target=self.target,
            budget_usd_per_day=self.budget_usd_per_day,
            queue=self.queue,
            queues_dir=self.queues_dir,
            trace=self.judge_trace,
            judge_concurrency=self.judge_concurrency,
            record_dir=self.record_dir,
            since=since,
            until=until,
            concurrency=self.concurrency,
        )
        return twin

    async def run(self, *, resume: bool = False, **kwargs: Any):
        self._run_store = _open_runs(self.store)
        self._store = _open_scores(self.scores)
        run = None
        try:
            run = await super().run(resume=resume, **kwargs)
        finally:
            writer, self._writer = self._writer, None
            if writer is not None:
                _, lost = await asyncio.to_thread(writer.finish, 10.0)
                if lost and run is not None:
                    warn_lost(self.name, lost, run)
        if run is not None and not self.is_backfill and self._source is not None:
            await asyncio.to_thread(self._source.cursor.save, self.cursor_path)
        return run

    async def _runs(self):
        if self.is_backfill:
            where = replace(self.runs, since=self.since, until=self.until)
            cursor = None
        else:
            where = self.runs
            cursor = await asyncio.to_thread(Cursor.load, self.cursor_path)
        self._source = RunStoreSource(
            self._run_store, where, cursor=cursor, until=self.until, sample=self.sample
        )
        async for run in self._source.items():
            yield run

    def begin(self, run_id: str, started: str) -> None:
        self._tally, self._queued, self._exhausted = {}, 0, 0
        self._day = _utc_day()
        self._budget = (
            _Budget(self.budget_usd_per_day, *self._spent_today())
            if self.budget_usd_per_day is not None
            else None
        )
        self._judging = Judging(
            trace=self.judge_trace,
            metadata={"job": self.name, "job_run": run_id, "rule": self.name},
            cache=self._store,
            concurrency=self.judge_concurrency,
        )
        self._writer = ScoreWriter(self._store, self.name)

    def _spent_today(self) -> Tuple[float, int]:
        """What this rule's judges spent today (UTC), across hosts, and on
        how many runs."""
        start = datetime.strptime(self._day, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        rows = self._store.scores(
            ScoreFilter(rule=self.name, source="judge", since=start.timestamp())
        )
        priced = [s for s in rows if s.cost_usd is not None]
        return sum(float(s.cost_usd) for s in priced), len({s.trace_id for s in priced})

    async def judge(self, raw: RunSummary, result: Any) -> None:
        """One stored run: read it back, judge it, write its scores."""
        if result.status not in (ITEM_OK, ITEM_EMPTY):
            result.verdict = {"passed": False, "error": result.error or result.status}
            return
        record = await asyncio.to_thread(self._run_store.get_run, raw.trace_id)
        if record is None:
            result.verdict = {
                "passed": False,
                "error": f"run {raw.trace_id} is no longer in the store (past its retention?)",
            }
            return
        view = TraceView.from_record(record)
        session = view.metadata.get("session_id")
        if self.target == "session" and not session:
            result.verdict = {
                "passed": False,
                "error": "target='session' but the run has no session_id metadata",
            }
            return
        prepared = self._prepared
        admitted = False
        if self._judges and self._budget is not None:
            admitted = await self._budget.admit()
            if not admitted:
                prepared = [p for p in prepared if p.name not in self._judges]
                self._exhausted += 1
        exhausted = bool(self._judges) and self._budget is not None and not admitted
        avail = {
            "input": view.input,
            "output": view.output,
            "trace": view,
            "trace_summary": view.as_text(),
            "run": raw,
            "judging": self._judging.for_case(key=raw.trace_id, judged_trace=raw.trace_id),
        }
        checks: Dict[str, Any] = {}
        try:
            checks = await judge_all(prepared, avail) if prepared else {}
        finally:
            if admitted:
                cost = [c["cost_usd"] for n, c in checks.items() if n in self._judges]
                await self._budget.settle(
                    sum(float(c) for c in cost if c is not None), bool(checks)
                )
        for name, check in checks.items():
            t = self._tally.setdefault(name, {"passed": 0, "failed": 0, "errors": 0})
            if check.get("error"):
                t["errors"] += 1
            t["passed" if check.get("passed") else "failed"] += 1
        failed = [name for name, c in checks.items() if not c.get("passed")]
        result.verdict = {
            "passed": not failed,
            "failed": failed,
            "checks": checks,
            **({"status": BUDGET_EXHAUSTED} if exhausted else {}),
        }
        self._writer.submit(self._scores_of(raw, view, checks, session))
        if self.queue is not None and self._wants_queue(raw.trace_id, failed):
            from .queues import enqueue

            await asyncio.to_thread(
                enqueue,
                self.queues_dir,
                self.queue["to"],
                target=self.target,
                trace_id=raw.trace_id,
                session_id=session,
                source=f"online:{self.name}",
                reason=", ".join(failed),
            )
            self._queued += 1

    def _wants_queue(self, trace_id: str, failed: Sequence[str]) -> bool:
        if self.queue["when"] == "any_failed" and not failed:
            return False
        return sampled(trace_id, self.queue["sample"], salt=self.queue["to"])

    def _scores_of(
        self, run: RunSummary, view: TraceView, checks: Mapping[str, Any], session: Any
    ) -> List[Any]:
        snapshot = {
            "input": _clip(view.input, SNAPSHOT_CHARS),
            "output": _clip(view.output, SNAPSHOT_CHARS),
        }
        ids: Dict[str, Any] = {"trace_id": run.trace_id}
        if self.target == "session":
            ids = {"session_id": str(session), "trace_id": run.trace_id}
        return [
            check_score(
                name,
                check,
                source="judge" if name in self._judges else "code",
                evaluator_version=self._versions[name],
                target="op" if check.get("op") else self.target,
                origin=run.origin,
                name=run.name,
                rule=self.name,
                snapshot=snapshot,
                **ids,
            )
            for name, check in checks.items()
        ]

    def summarize(self, status: str) -> Tuple[Dict[str, Any], str]:
        src = self._source
        online = {
            "rule": self.name,
            "sample": self.sample,
            "backfill": self.is_backfill,
            "unsampled": src.unsampled if src is not None else 0,
            "checks": self._tally,
            "queued": self._queued,
            "budget_usd_per_day": self.budget_usd_per_day,
            "spent_usd_today": round(self._budget.spent, 6) if self._budget else None,
            BUDGET_EXHAUSTED: self._exhausted,
            "cursor": asdict(src.cursor) if src is not None and not self.is_backfill else None,
        }
        return {"online": online}, status

    def describe(self) -> Dict[str, Any]:
        out = super().describe()
        out["online"] = {
            "runs": {k: v for k, v in asdict(self.runs).items() if v not in (None, {}, ())},
            "evaluators": [_name(ev) for ev in self.evaluators],
            "sample": self.sample,
            "target": self.target,
            "budget_usd_per_day": self.budget_usd_per_day,
            "queue": self.queue,
        }
        return out


class _Budget:
    """A day's judge spend, shared by a pass's items running at once.

    A judge that has started counts before it finishes: at the day's cost
    per judged run, so items running together cannot each see the same
    total and all go over it. Until one run's cost is known, judges start
    one at a time. A run is judged only when its estimated cost still fits;
    an unpriced judge costs nothing and never runs out.
    """

    def __init__(self, limit: float, spent: float = 0.0, runs: int = 0):
        self.limit, self.spent, self.runs = float(limit), float(spent), int(runs)
        self.in_flight = 0
        self.day = _utc_day()
        self._cond = asyncio.Condition()

    @property
    def per_run(self) -> Optional[float]:
        return self.spent / self.runs if self.runs else None

    async def admit(self) -> bool:
        """Whether one more run may be judged now; ``True`` holds a place
        until :meth:`settle`."""
        async with self._cond:
            while True:
                if _utc_day() != self.day:  # a pass running over midnight: a new day
                    self.day, self.spent, self.runs = _utc_day(), 0.0, 0
                cost = self.per_run
                if cost is None and self.in_flight:
                    await self._cond.wait()  # the first run's cost decides the rest
                    continue
                if (cost is None and self.spent >= self.limit) or (
                    cost is not None and self.spent + (self.in_flight + 1) * cost > self.limit
                ):
                    return False
                self.in_flight += 1
                return True

    async def settle(self, cost: float, judged: bool) -> None:
        """A run admitted by :meth:`admit` is done: *cost* is what it spent;
        a run whose judges never answered (``judged`` false) adds nothing."""
        async with self._cond:
            self.in_flight -= 1
            if judged:
                self.spent += cost
                self.runs += 1
            self._cond.notify_all()


def _requires(p: Any, name: str) -> bool:
    """Whether evaluator *p* cannot judge without *name*. A judge with
    ``reference="auto"`` (the default) shows ``expected`` only when there is
    one, and a function's ``expected=None`` has a default: both judge a
    production run as they are."""
    if p.params is None or name not in p.params:
        return False
    reference = getattr(p.ev, "reference", None)
    if reference is not None:  # a judge says what it needs
        return reference is True and name == "expected"
    try:
        param = inspect.signature(p.fn).parameters.get(name)
    except (TypeError, ValueError):
        return True
    return param is None or param.default is inspect.Parameter.empty


def _check_queue(name: str, queue: Optional[Mapping[str, Any]]) -> Optional[Dict[str, Any]]:
    if queue is None:
        return None
    if not isinstance(queue, Mapping) or not queue.get("to"):
        raise ValueError(
            f"online eval {name!r}: queue is {{to = '<queue>', when = 'any_failed', "
            f"sample = 1.0}}; got {queue!r}"
        )
    when = str(queue.get("when") or "any_failed")
    if when not in QUEUE_WHEN:
        raise ValueError(
            f"online eval {name!r}: queue when={when!r}; one of {', '.join(QUEUE_WHEN)}"
        )
    rate = float(queue.get("sample", 1.0))
    if not 0 < rate <= 1:
        raise ValueError(f"online eval {name!r}: queue sample is a share in (0, 1]")
    return {"to": str(queue["to"]), "when": when, "sample": rate}


def _open_runs(value: Any) -> Any:
    if isinstance(value, str):
        if not value.startswith("run_store:"):
            raise ValueError(
                f"store={value!r} is not a run store key; name one as 'run_store:<name>'"
            )
        from operonx.core.registry import ResourceHub

        return ResourceHub.instance().get(value)
    if not hasattr(value, "list_runs"):
        raise TypeError(f"store is a run store or a 'run_store:<name>' key, not {value!r}")
    return value


def _utc_day() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")
