"""The pytest plugin: a test session is one experiment, each test one case.

Opt-in, never auto-loaded — installing operonx registers no ``pytest11``
entry point, so nobody's test run changes. Turn it on per run or per
project::

    pytest -p operonx.app.evals.pytest_plugin
    # or, in the root conftest.py:
    pytest_plugins = ["operonx.app.evals.pytest_plugin"]

A test asks for ``run_case``, runs the system under test on one case, and
judges it. The dataset becomes parametrised unit tests::

    from operonx.app.evals import exact
    from operonx.app.evals.pytest_plugin import cases

    @pytest.mark.parametrize("case", cases("dataset:labels"))
    async def test_labels(case, run_case):
        got = await run_case(flow, case, evaluators=[exact("label")], item_input="text")
        assert got.trace.path() == ["classify"]       # anything else about the run

``run_case`` runs the graph once through the job runner's own per-item
path — traced with ``origin=eval``, the run's trace id on the item — and
returns a :class:`CaseRun`: ``output``, ``outputs``, ``status``,
``trace`` (a :class:`~operonx.app.evals.traceview.TraceView`), ``checks``,
``passed``, ``why``, and ``check(evaluator)`` for one more (synchronous)
check. A sync test calls ``run_case.sync(...)``.

**The verdict is the test's outcome.** A test whose body passed but whose
checks failed is reported failed, with ``why``; a test whose body failed
records its assertion as an ``assert`` check. Every test that called
``run_case`` is one item of the session's experiment, keyed by its node
id (a test is one case: a second ``run_case`` in it raises — parametrise
instead); tests that did not are not part of it.

At the end of the session the record is finished like an eval's —
``<rootdir>/evals/<name>/<run_id>``, with the counts, metrics,
fingerprint and gate — and the terminal summary says the verdict and
where. Options:

    --operonx-eval-name NAME        the experiment's eval name (default: pytest)
    --operonx-eval-dir DIR          its record directory (default: <rootdir>/evals)
    --operonx-eval-baseline REF     latest, a run id, main or git:<ref> — fixed at session start
    --operonx-eval-tolerance X      how large a drop of the pass rate matters
    --operonx-eval-strict           inconclusive is a failure
    --operonx-eval-report md,json,junit  --operonx-eval-out DIR   (default: the record)
    --operonx-eval-store            write the experiment to the project's score store

A gate that does not pass (a regression the tests did not catch as
failures, an inconclusive comparison under ``--operonx-eval-strict``)
makes an otherwise passing session exit 1. One process: under
``pytest-xdist`` each worker would be its own experiment.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence

import pytest

from ..jobs import Job
from ..jobs.record import (
    ITEM_EMPTY,
    ITEM_OK,
    RUN_OK,
    RUN_STOPPED,
    ItemResult,
    JobRun,
    RunRecord,
)
from .dataset import Dataset, case_id, dataset_path
from .evaluators import _name, judge_all, judge_sync, prepare
from .experiments import git_ref
from .fingerprint import case_hash, config_spec, digest, fingerprint, graph_spec
from .gate import Gate
from .job import (
    _ask_git,
    _Capture,
    case_verdict,
    gate_run,
    git_baseline,
    numbers,
    record_baseline,
    recorded_verdict,
    trial_of,
)
from .traceview import TraceView

__all__ = ["CaseRun", "CaseRunner", "cases"]

#: The session's experiment, on the pytest config.
_EXPERIMENT = pytest.StashKey["_Experiment"]()


def cases(
    dataset: Any,
    *,
    split: Optional[str] = None,
    tags: Optional[Sequence[str]] = None,
    ids: Optional[Sequence[str]] = None,
    sample: Optional[int] = None,
) -> List[Any]:
    """A dataset's cases as ``pytest.param``s named by case id, for
    ``@pytest.mark.parametrize("case", cases("dataset:labels"))``.
    *dataset* is a :class:`Dataset`, a path or ``"dataset:name"``; the
    other arguments select, as :meth:`Dataset.select` does."""
    ds = dataset if isinstance(dataset, Dataset) else Dataset(dataset_path(dataset))
    if split or tags or ids or sample:
        ds = ds.select(split=split, tags=tags, ids=ids, sample=sample)
    return [pytest.param(row, id=str(row["id"])) for row in ds.rows()]


# ── one case ─────────────────────────────────────────────────────────────


@dataclass
class CaseRun:
    """One run of the system under test on one case, and its checks."""

    key: str
    row: Dict[str, Any]
    output: Any
    outputs: List[Any]
    result: ItemResult
    checks: Dict[str, Any] = field(default_factory=dict)
    _view: Any = field(default=None, repr=False)
    _seen: Optional[Callable[[Any], None]] = field(default=None, repr=False)

    @property
    def status(self) -> str:
        """The run's item status: ``ok``, ``empty``, ``failed``, ``timeout``."""
        return self.result.status

    @property
    def error(self) -> Optional[str]:
        return self.result.error

    @property
    def trace_id(self) -> Optional[str]:
        return self.result.trace_id

    @property
    def trace(self) -> Optional[TraceView]:
        """The case run as a :class:`TraceView` (built on first read)."""
        if self._view is None and self.result.trace is not None:
            self._view = TraceView.from_trace(self.result.trace)
        return self._view

    @property
    def passed(self) -> bool:
        """The run finished and every check passed."""
        ran = self.status in (ITEM_OK, ITEM_EMPTY)
        return ran and all(c.get("passed") for c in self.checks.values())

    @property
    def why(self) -> str:
        """What went wrong, in one line: the run's error, or each failed
        check with its reason."""
        if self.status not in (ITEM_OK, ITEM_EMPTY):
            return f"the run {self.status}: {self.error}"
        return "; ".join(
            name
            + (
                f": {c.get('reason') or c.get('error')}"
                if c.get("reason") or c.get("error")
                else ""
            )
            for name, c in self.checks.items()
            if not c.get("passed")
        )

    def available(self) -> Dict[str, Any]:
        """What an evaluator may take, by name."""
        return {
            "input": self.row.get("input"),
            "output": self.output,
            "expected": self.row.get("expected"),
            "row": self.row,
            "outputs": self.outputs,
        }

    def _avail_for(self, prepared: Sequence[Any]) -> Dict[str, Any]:
        avail = self.available()
        if any(p.wants("trace") for p in prepared):
            avail["trace"] = self.trace
        return avail

    def check(self, evaluator: Any) -> bool:
        """Judge the case with one more synchronous *evaluator*; its verdict
        joins ``checks``. Returns whether it passed. An async evaluator
        goes in ``run_case(evaluators=[…])`` instead."""
        p = prepare(evaluator)
        try:
            self.checks[p.name] = judge_sync(p, self._avail_for([p]))
        except TypeError as exc:
            raise TypeError(f"{exc}; pass it in run_case(evaluators=[...])") from None
        if self._seen is not None:
            self._seen(evaluator)
        return bool(self.checks[p.name]["passed"])


class _CaseJob(Job):
    """A graph as the session's cases run it: an eval's origin, the case's
    ``input`` as the item."""

    origin = "eval"

    def item_of(self, raw: Any) -> Any:
        return raw["input"] if isinstance(raw, Mapping) and "input" in raw else raw


class CaseRunner:
    """The ``run_case`` fixture: ``await run_case(graph, case, …)``, or
    ``run_case.sync(…)`` in a sync test."""

    def __init__(self, experiment: "_Experiment", nodeid: str):
        self._experiment = experiment
        self._nodeid = nodeid

    async def __call__(
        self,
        graph: Any,
        case: Any,
        *,
        evaluators: Sequence[Any] = (),
        item_input: Optional[str] = None,
        inputs: Optional[Dict[str, Any]] = None,
        timeout: Optional[float] = None,
    ) -> CaseRun:
        """Run *graph* on *case* (a dataset row — ``{"id", "input",
        "expected", …}`` — or the bare input) and judge it with
        *evaluators*. ``item_input`` binds the input to a graph with no
        doors; ``inputs`` are static inputs; ``timeout`` in seconds."""
        return await self._experiment.run_case(
            self._nodeid,
            graph,
            case,
            evaluators=evaluators,
            item_input=item_input,
            inputs=inputs or {},
            timeout=timeout,
        )

    def sync(self, graph: Any, case: Any, **kwargs: Any) -> CaseRun:
        """:meth:`__call__` for a sync test."""
        return asyncio.run(self(graph, case, **kwargs))


# ── the session's experiment ─────────────────────────────────────────────


class _Experiment:
    def __init__(self, config: pytest.Config):
        from .report import parse_formats

        self.config = config
        self.root = Path(config.rootpath)
        self.name = str(config.getoption("operonx_eval_name"))
        where = config.getoption("operonx_eval_dir")
        self.record_dir = (
            (Path(where) if Path(where).is_absolute() else self.root / where)
            if where
            else self.root / "evals"
        )
        baseline = config.getoption("operonx_eval_baseline")
        tolerance = config.getoption("operonx_eval_tolerance")
        strict = bool(config.getoption("operonx_eval_strict"))
        self.gate: Optional[Gate] = None
        if baseline or tolerance is not None or strict:
            if tolerance is not None and not baseline:
                raise pytest.UsageError(
                    "--operonx-eval-tolerance compares against a baseline: "
                    "give --operonx-eval-baseline too"
                )
            try:
                self.gate = Gate(baseline=baseline, tolerance=tolerance, strict=strict)
            except ValueError as exc:
                raise pytest.UsageError(f"operonx eval: {exc}") from None
        try:
            self.formats = parse_formats(config.getoption("operonx_eval_report") or "")
        except ValueError as exc:
            raise pytest.UsageError(f"--operonx-eval-report: {exc}") from None
        self.out = config.getoption("operonx_eval_out")
        self.store = bool(config.getoption("operonx_eval_store"))
        self.baseline: Any = None
        self.baseline_ref: Optional[str] = None
        self.record: Optional[RunRecord] = None
        self.code: Any = None
        self.jobs: Dict[Any, Job] = {}
        self.graphs: Dict[str, Any] = {}
        self.evaluators: Dict[str, Any] = {}
        self.rows: List[Dict[str, Any]] = []
        self.verdicts: List[Dict[str, Any]] = []
        self.pending: Dict[str, CaseRun] = {}
        self.done: set = set()
        self.run: Optional[JobRun] = None
        self.reports: Dict[str, Path] = {}
        self.stored: Optional[str] = None

    def resolve_baseline(self) -> None:
        """Fixed when the session starts, as an eval fixes it when it runs."""
        if self.gate is None or self.gate.baseline is None:
            return
        ref = git_ref(self.gate.baseline)
        try:
            if ref is not None:
                found = git_baseline(
                    self.name, str(self.gate.baseline), ref, root=self.root, store=None
                )
                self.baseline, self.baseline_ref = found[:3], found[3]
            else:
                self.baseline = record_baseline(self.record_dir, self.name, str(self.gate.baseline))
        except ValueError as exc:
            raise pytest.UsageError(f"operonx eval: {exc}") from None

    def open(self) -> RunRecord:
        if self.record is None:
            self.code = _ask_git(self.root)
            meta = {"kind": "eval", "graph": None, "variant": "pytest", "session": "per_item"}
            self.record = RunRecord(self.record_dir, self.name, meta=meta)
        return self.record

    def job(
        self, graph: Any, item_input: Optional[str], inputs: Dict[str, Any], timeout: Any
    ) -> Job:
        key = (id(graph), item_input, json.dumps(inputs, sort_keys=True, default=str), timeout)
        if key not in self.jobs:
            self.jobs[key] = _CaseJob(
                self.name,
                graph=graph,
                item_input=item_input,
                inputs=inputs,
                item_timeout=timeout,
                record_dir=self.record_dir,
            )
        return self.jobs[key]

    async def run_case(
        self,
        nodeid: str,
        graph: Any,
        case: Any,
        *,
        evaluators: Sequence[Any],
        item_input: Optional[str],
        inputs: Dict[str, Any],
        timeout: Optional[float],
    ) -> CaseRun:
        from ..jobs.runner import _attempt

        if nodeid in self.pending or nodeid in self.done:
            raise RuntimeError(
                f"{nodeid}: run_case was already called in this test — one test is one case "
                "of the experiment; parametrize the test over cases instead"
            )
        record = self.open()
        row = dict(case) if isinstance(case, Mapping) and "input" in case else {"input": case}
        row["id"] = case_id(row)
        job = self.job(graph, item_input, inputs, timeout)
        engine = job.engine()
        capture = _Capture()
        result = await _attempt(job, engine, capture, row, nodeid, record.run_id)
        sent = capture.by_key.pop(nodeid, [])
        got = CaseRun(
            key=nodeid,
            row=row,
            output=sent[0] if len(sent) == 1 else (sent or None),
            outputs=sent,
            result=result,
            _seen=self.saw,
        )
        self.graphs.setdefault(str(job.describe()["graph"]), engine.graph)
        if evaluators and result.status in (ITEM_OK, ITEM_EMPTY):
            prepared = [prepare(ev) for ev in evaluators]
            got.checks.update(await judge_all(prepared, got._avail_for(prepared)))
        for ev in evaluators:
            self.saw(ev)
        self.pending[nodeid] = got
        return got

    def saw(self, evaluator: Any) -> None:
        self.evaluators.setdefault(_name(evaluator), evaluator)

    def judged(self, nodeid: str, got: CaseRun) -> None:
        """The test is over: its verdict goes on the record."""
        self.done.add(nodeid)
        result = got.result
        if result.status in (ITEM_OK, ITEM_EMPTY):
            verdict = case_verdict(got.checks)
        else:
            verdict = {
                "passed": False,
                "error": result.error or result.status,
                "checks": got.checks,
            }
        result.verdict = recorded_verdict(
            verdict,
            got.row,
            got.output,
            case=nodeid,
            repeat=0,
            digest=case_hash(got.row),
            cluster=str(got.row["cluster"]) if got.row.get("cluster") not in (None, "") else None,
            trace=result.trace,
        )
        result.trace = None
        self.open().item(result)
        self.verdicts.append(trial_of(nodeid, result.verdict, result.ms))
        self.rows.append({**got.row, "id": nodeid})

    def _fingerprint(self) -> Dict[str, Any]:
        graphs = list(self.graphs.values())
        code = self.code.result() if self.code is not None else {}
        fp = fingerprint(graph=graphs[0], rows=self.rows, evaluators=self.evaluators, code=code)
        if len(graphs) > 1:
            parts: Dict[str, Any] = {}
            for name, g in sorted(self.graphs.items()):
                try:
                    spec = g.serialize()
                except NotImplementedError:
                    parts[name] = (None, None)
                else:
                    parts[name] = (digest(graph_spec(spec)), digest(config_spec(spec)))
            fp["graph_hash"] = digest({k: v[0] for k, v in parts.items()})
            fp["config_hash"] = digest({k: v[1] for k, v in parts.items()})
        return fp

    def finish(self, exitstatus: int) -> Optional[JobRun]:
        from .experiments import load_experiment
        from .report import write_reports

        if self.record is None:
            return None
        for nodeid in list(self.pending):  # a test that never reported its call
            self.judged(nodeid, self.pending.pop(nodeid))
        nums, outcomes, metrics = numbers(self.verdicts, 1)
        fp = self._fingerprint()
        complete = exitstatus != pytest.ExitCode.INTERRUPTED
        gate, status = gate_run(
            self.gate,
            None,
            status=RUN_OK if complete else RUN_STOPPED,
            nums=nums,
            cases=outcomes,
            metrics=metrics,
            complete=complete,
            baseline=self.baseline,
            fingerprint=fp,
            baseline_ref=self.baseline_ref,
        )
        summary = {"dataset": None, "threshold": None, **nums, "variant": "pytest"}
        summary["fingerprint"] = fp
        summary["gate"] = gate
        extra = {
            "eval": summary,
            "graph": ", ".join(sorted(self.graphs)),
            "evaluators": sorted(self.evaluators),
        }
        self.run = self.record.finish(status, extra=extra)
        if self.formats:
            out = Path(self.out) if self.out else self.run.path
            if not out.is_absolute():
                out = self.root / out
            self.reports = write_reports(load_experiment(self.run), self.formats, out)
        if self.store:
            self.publish()
        return self.run

    def publish(self) -> None:
        from operonx.telemetry.scores import project_score_store

        from .publish import publish

        src = project_score_store(self.root)
        store = src.open()
        try:
            publish(self.run, store)
        finally:
            store.close()
        self.stored = src.describe()


# ── the hooks ────────────────────────────────────────────────────────────


def pytest_addoption(parser: pytest.Parser) -> None:
    group = parser.getgroup("operonx-eval", "operonx eval: the session as one experiment")
    group.addoption("--operonx-eval-name", default="pytest", help="the experiment's eval name")
    group.addoption(
        "--operonx-eval-dir", default=None, help="record directory (default: <rootdir>/evals)"
    )
    group.addoption(
        "--operonx-eval-baseline",
        default=None,
        help="compare against: latest, a run id, main, git:<ref>",
    )
    group.addoption(
        "--operonx-eval-tolerance",
        type=float,
        default=None,
        help="how large a drop of the pass rate matters (with a baseline)",
    )
    group.addoption(
        "--operonx-eval-strict", action="store_true", help="an inconclusive gate fails the session"
    )
    group.addoption("--operonx-eval-report", default=None, help="md,json,junit reports to write")
    group.addoption(
        "--operonx-eval-out", default=None, help="where reports go (default: the record)"
    )
    group.addoption(
        "--operonx-eval-store",
        action="store_true",
        help="write the experiment to the project's score store",
    )


def pytest_configure(config: pytest.Config) -> None:
    config.stash[_EXPERIMENT] = _Experiment(config)


def pytest_sessionstart(session: pytest.Session) -> None:
    session.config.stash[_EXPERIMENT].resolve_baseline()


@pytest.fixture
def run_case(request: pytest.FixtureRequest) -> CaseRunner:
    """Run the system under test on this test's case — see the module docstring."""
    return CaseRunner(request.config.stash[_EXPERIMENT], request.node.nodeid)


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item: pytest.Item, call: pytest.CallInfo):
    outcome = yield
    report = outcome.get_result()
    if report.when != "call":
        return
    experiment = item.config.stash[_EXPERIMENT]
    got = experiment.pending.pop(item.nodeid, None)
    if got is None:
        return
    if report.skipped:  # not judged: not an item
        experiment.done.add(item.nodeid)
        return
    if report.failed and call.excinfo is not None:
        name = "assert" if isinstance(call.excinfo.value, AssertionError) else "test"
        got.checks[name] = {"passed": False, "reason": call.excinfo.exconly()[:2000]}
    elif report.passed and not got.passed:
        report.outcome = "failed"
        report.longrepr = f"operonx eval: {got.why}"
    experiment.judged(item.nodeid, got)


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    experiment = session.config.stash[_EXPERIMENT]
    run = experiment.finish(int(exitstatus))
    if run is None:
        return
    code = int(((run.meta.get("eval") or {}).get("gate") or {}).get("exit_code") or 0)
    if code and session.exitstatus == pytest.ExitCode.OK:
        session.exitstatus = pytest.ExitCode.TESTS_FAILED


def pytest_terminal_summary(terminalreporter: Any) -> None:
    experiment = terminalreporter.config.stash[_EXPERIMENT]
    run = experiment.run
    if run is None:
        return
    gate = (run.meta.get("eval") or {}).get("gate") or {}
    tr = terminalreporter
    tr.section(f"operonx eval {experiment.name}")
    tr.write_line(f"{run.summary()}  gate={gate.get('verdict')} (exit {gate.get('exit_code')})")
    tr.write_line(f"  {run.path}")
    for line in gate.get("reasons") or ():
        tr.write_line(f"  gate: {line}")
    for line in gate.get("warnings") or ():
        tr.write_line(f"  warning: {line}")
    for path in experiment.reports.values():
        tr.write_line(f"  report: {path}")
    if experiment.stored:
        tr.write_line(f"  experiment stored in {experiment.stored}")
