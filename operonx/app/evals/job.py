"""`Eval` — a dataset, the system under test, evaluators — run as a Job.

One eval run is one **experiment**: every case (times ``repeats``) is a
job item, judged after its run; ``run.json["eval"]`` holds the numbers,
the experiment's fingerprint, and the gate's verdict.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union

from operonx.core.workflow_trace import run_metadata
from operonx.telemetry.scores import Experiment, ScoreStore, open_score_store

from ..jobs import Job
from ..jobs.record import (
    ITEM_EMPTY,
    ITEM_OK,
    RUN_FAILED,
    RUN_OK,
    RUN_RUNNING,
    JobRun,
    runs_of,
)
from .dataset import Dataset, dataset_path
from .evaluators import _name, judge_all, prepare
from .experiments import git_ref
from .fingerprint import case_hash, dataset_version, evaluators_version, fingerprint
from .gate import (
    ERROR,
    FAILED,
    FLAKY,
    PASS,
    PASS_METRIC,
    STABLE_FAIL,
    STABLE_PASS,
    CaseOutcome,
    Gate,
    decide,
    outcomes,
    trials_of_items,
)
from .publish import ScoreWriter, experiment_of, item_of, scores_of, warn_lost
from .stats import Estimate, estimate, pass_hat_k
from .traceview import TraceView

__all__ = ["Eval"]

#: How many flaky case ids ``reliability`` lists (the count is whole).
FLAKY_LIST_MAX = 50

_git: Optional[ThreadPoolExecutor] = None
_versions: Dict[Path, "Future[Dict[str, Any]]"] = {}


def _ask_git(root: Path) -> "Future[Dict[str, Any]]":
    """``origin.code_version(root)``, asked once per process and root —
    the rule ``code_version`` states: read once, never per run. Its two
    git commands cost ~30 ms, so the first eval asks on a worker thread
    while its cases run, and the rest reuse the answer."""
    from ..origin import code_version

    global _git
    root = root.resolve()
    if root not in _versions:
        if _git is None:
            _git = ThreadPoolExecutor(max_workers=1, thread_name_prefix="operonx-eval-git")
        _versions[root] = _git.submit(code_version, root)
    return _versions[root]


# ── the eval ─────────────────────────────────────────────────────────────


class _Capture:
    """The eval's sink: what each case sent, kept for its evaluators."""

    def __init__(self) -> None:
        self.by_key: Dict[str, List[Any]] = {}

    async def write(self, key: str, item: Any) -> None:
        self.by_key.setdefault(key, []).append(item)

    async def close(self) -> None:
        pass

    def __repr__(self) -> str:
        return "<eval capture>"


@dataclass(frozen=True)
class _Trial:
    """One run of one case: the case's row, which repeat, the item key,
    and the case's hash (computed once per case, not per trial)."""

    row: Dict[str, Any]
    repeat: int
    key: str
    case_hash: str


class Eval(Job):
    """A dataset, a graph, evaluators — run as a job with ``origin=eval``.

    Everything but the arguments below is a :class:`Job` argument
    (``concurrency``, ``item_timeout``, ``inputs``, ``item_input``,
    ``trace``…).

    Args:
        dataset: A :class:`Dataset`, a JSONL path, or ``"dataset:name"``.
        evaluators: Functions judging each case (see the module docstring).
        threshold: With it, the run fails when the pass rate is under it;
            without, it fails when any case fails. With a ``gate``, put
            it in ``Gate(threshold=…)`` instead.
        repeats: Runs per case. Above 1, each case is ``repeats`` items
            keyed ``"<id>#<r>"``, and the summary says which cases are
            flaky and what pass^k is.
        cluster: The case field that groups cases (one scenario's
            variants, one conversation's turns) for the clustered SE.
            Without it a case's own ``cluster`` field is read; a case with
            neither is its own cluster.
        gate: A :class:`~operonx.app.evals.gate.Gate`: thresholds, a
            baseline to compare against, a must-pass tier, an error
            budget, and the exit code they make. Without one the run
            passes or fails exactly as ``threshold`` says.
        root: The project root, where the fingerprint asks git for the
            commit (the manifest's directory; else the working directory).
        scores: Where the experiment, its items and every check's score
            are written as the run goes: a
            :class:`~operonx.telemetry.scores.ScoreStore`, a
            ``"score_store:<name>"`` key, or a spec (``{"backend":
            "files"}``). Unset, nothing is written but the job record.
        scores_timeout: How long the run waits, at its end, for the store
            to take what is queued. What it did not take is counted and
            logged; the job record still holds every verdict, and
            :func:`~operonx.app.evals.publish` sends it later.
        variant: A free label for what this experiment tries ("prompt
            v7"), kept on its record and its experiment row.
    """

    origin = "eval"

    def __init__(
        self,
        name: str,
        *,
        graph: Any,
        dataset: Any,
        evaluators: Sequence[Any] = (),
        threshold: Optional[float] = None,
        repeats: int = 1,
        cluster: Optional[str] = None,
        gate: Optional[Gate] = None,
        root: Union[str, Path, None] = None,
        record_dir: Union[str, Path] = "evals",
        scores: Any = None,
        scores_timeout: float = 10.0,
        variant: Optional[str] = None,
        **kwargs: Any,
    ):
        self.dataset = dataset if isinstance(dataset, Dataset) else Dataset(dataset_path(dataset))
        self.evaluators = list(evaluators)
        self._prepared = [prepare(ev) for ev in self.evaluators]
        # a case's trace view is built only when some evaluator can take it
        self._wants_trace = any(p.wants("trace") for p in self._prepared)
        if threshold is not None and not 0 <= float(threshold) <= 1:
            raise ValueError(f"eval {name!r}: threshold is a pass rate in [0, 1]")
        self.threshold = float(threshold) if threshold is not None else None
        if isinstance(repeats, bool) or not isinstance(repeats, int) or repeats < 1:
            raise ValueError(f"eval {name!r}: repeats is a whole number of runs per case, ≥ 1")
        self.repeats = repeats
        self.cluster = cluster
        if gate is not None and not isinstance(gate, Gate):
            raise TypeError(f"eval {name!r}: gate is a Gate, not a {type(gate).__name__}")
        if gate is not None and threshold is not None:
            raise ValueError(
                f"eval {name!r}: threshold= is the gate-less shorthand; with a gate, "
                "give it as Gate(threshold=…)"
            )
        self.gate = gate
        if gate is not None:
            known = {PASS_METRIC} | {_name(ev) for ev in self.evaluators}
            unknown = [m for m in gate.named_metrics() if m not in known]
            if unknown:
                raise ValueError(
                    f"eval {name!r}: the gate names {unknown}, which are not metrics of "
                    f"this eval; metrics are {sorted(known)} ('pass' = every check passed)"
                )
        self.root = Path(root) if root is not None else None
        self.scores = _check_scores(name, scores)
        self.scores_timeout = float(scores_timeout)
        self.variant = str(variant) if variant else None
        self._judges = [
            _name(ev) for ev in self.evaluators if getattr(ev, "eval_kind", None) == "judge"
        ]
        self._store: Optional[ScoreStore] = None
        self._writer: Optional[ScoreWriter] = None
        self._experiment: Optional[Experiment] = None
        self._capture = _Capture()
        self._rows: Dict[str, Dict[str, Any]] = {}
        self._verdicts: List[Dict[str, Any]] = []
        self._fingerprint: Optional[Dict[str, Any]] = None
        self._code: Optional["Future[Dict[str, Any]]"] = None
        self._baseline: Optional[Tuple[str, Dict[str, CaseOutcome], Optional[Dict]]] = None
        # a "main" / "git:<ref>" baseline: found before the record opens
        self._from_git: Optional[Tuple[str, Dict[str, CaseOutcome], Dict, str]] = None
        self._complete = False
        kwargs.setdefault("on_error", "record")
        super().__init__(
            name,
            graph=graph,
            source=self._cases,
            sink=self._capture,
            key="id",
            session="per_item",
            record_dir=record_dir,
            **kwargs,
        )

    # -- the experiment in a score store ---------------------------------------

    def begin(self, run_id: str, started: str) -> None:
        """The runner opened the record: with ``scores=``, the experiment's
        ``running`` row goes to the store before any case."""
        self._writer, self._experiment = None, None
        if self._store is None:
            return
        opened = JobRun(
            job=self.name,
            run_id=run_id,
            path=Path(self.record_dir) / self.name / run_id,
            status=RUN_RUNNING,
            started=started,
            ended=None,
            counts={},
            meta=self.describe(),
        )
        self._experiment = experiment_of(opened)
        self._writer = ScoreWriter(self._store, self.name)
        self._writer.submit([self._experiment])

    async def run(self, *, resume: bool = False) -> JobRun:
        """Run the eval once; with ``scores=``, then wait (up to
        ``scores_timeout``) for the store to take the experiment."""
        # opened before the record: a store that cannot be named fails the
        # run before it starts, not with a record stuck at "running"
        self._store = _open_scores(self.scores) if self.scores is not None else None
        # so is a commit's baseline: no experiment there, no run (and no cost)
        ref = git_ref(self.gate.baseline) if self.gate is not None else None
        self._from_git = await asyncio.to_thread(self._git_baseline, ref) if ref else None
        run: Optional[JobRun] = None
        try:
            run = await super().run(resume=resume)
            if self._writer is not None:
                self._writer.submit([experiment_of(run)])
        finally:
            writer, self._writer = self._writer, None
            if writer is not None:  # even when the run raised: no writer thread outlives it
                _, lost = await asyncio.to_thread(writer.finish, self.scores_timeout)
                if lost and run is not None:
                    warn_lost(self.name, lost, run)
        return run

    # -- the cases -----------------------------------------------------------

    async def _cases(self):
        """The run's items: every case, ``repeats`` times, repeat-major —
        each case once, then again — so a passing outage spreads over the
        cases instead of sinking one. The fingerprint and the baseline are
        fixed here, before any case runs (git answers while they run)."""
        self._verdicts, self._complete, self._baseline = [], False, None
        rows = await asyncio.to_thread(self.dataset.rows)
        self._rows = {row["id"]: row for row in rows}
        if self._from_git is not None:
            self._baseline = self._from_git[:3]
        elif self.gate is not None and self.gate.baseline is not None:
            self._baseline = await asyncio.to_thread(self._load_baseline)
        self._code = _ask_git(self.root or Path.cwd())
        self._fingerprint, hashes = await asyncio.to_thread(self._identify, rows)
        for repeat in range(self.repeats):
            for row, digest in zip(rows, hashes):
                key = row["id"] if self.repeats == 1 else f"{row['id']}#{repeat}"
                yield _Trial(row, repeat, key, digest)
        self._complete = True

    def _identify(self, rows: List[Dict[str, Any]]) -> Tuple[Dict[str, Any], List[str]]:
        """The fingerprint (its code version filled in by ``summarize``)
        and each case's hash."""
        fp = fingerprint(
            graph=self.engine().graph,
            rows=rows,
            evaluators={_name(ev): ev for ev in self.evaluators},
            code={},
        )
        return fp, [case_hash(row) for row in rows]

    def _git_baseline(self, ref: str) -> Tuple[str, Dict[str, CaseOutcome], Dict, str]:
        """The experiment of ``git merge-base HEAD <ref>``, from the eval's
        score store (else the project's): ``(id, outcomes, fingerprint,
        "git:<ref> @ <sha>")``. None there is an error — nothing has run."""
        from operonx.telemetry.scores import project_score_store

        from .experiments import ExperimentData, find_baseline, merge_base, store_name

        root = self.root or Path.cwd()
        sha = merge_base(root, ref)
        rows = self.dataset.rows()
        _, evaluators_hash = evaluators_version({_name(ev): ev for ev in self.evaluators})
        if self._store is not None:
            store, owned, where = self._store, False, store_name(self._store)
        else:
            src = project_score_store(root)
            store, owned, where = src.open(), True, f"{src.describe()} ({src.source})"
        try:
            exp = find_baseline(
                store,
                self.name,
                sha,
                dataset_version=dataset_version(rows),
                evaluators_hash=evaluators_hash,
            )
            data = ExperimentData.from_store(store, exp.experiment_id) if exp else None
        finally:
            if owned:
                store.close()
        if data is None:
            branch = ref.split("/", 1)[-1]
            raise ValueError(
                f"eval {self.name!r}: baseline {self.gate.baseline!r} is the experiment at "  # type: ignore[union-attr]
                f"{sha}, the merge-base of HEAD and {ref}, and the score store ({where}) has "
                f"no finished, clean run of this eval there. Run the eval on {branch} first "
                f"so its experiment is stored (`operonx eval run {self.name}` in a pipeline on "
                f"{branch}, or a scheduled one, keeps baselines warm), or compare with "
                "baseline='latest'"
            )
        fp = data.fingerprint
        return data.experiment_id, data.outcomes(), fp, f"git:{ref} @ {sha}"

    def _load_baseline(self) -> Optional[Tuple[str, Dict[str, CaseOutcome], Optional[Dict]]]:
        ref = str(self.gate.baseline)  # type: ignore[union-attr]
        root = Path(self.record_dir)
        if ref == "latest":
            for path in reversed(runs_of(root, self.name)):
                run = JobRun.load(path)
                ev = run.meta.get("eval") or {}
                gate = ev.get("gate") or {}
                if run.status != RUN_RUNNING and run.ended and ev and gate.get("verdict") != ERROR:
                    break
            else:
                return None
        else:
            path = root / self.name / ref
            if not (path / "run.json").is_file():
                raise ValueError(
                    f"eval {self.name!r}: baseline run {ref!r} is not under {root / self.name}"
                )
            run = JobRun.load(path)
        ev = run.meta.get("eval") or {}
        return run.run_id, outcomes(trials_of_items(run.items)), ev.get("fingerprint")

    def item_of(self, raw: Any) -> Any:
        row = raw.row if isinstance(raw, _Trial) else raw
        return row["input"] if isinstance(row, Mapping) and "input" in row else row

    def key_of(self, item: Any) -> str:
        return item.key if isinstance(item, _Trial) else super().key_of(item)

    # -- judging -------------------------------------------------------------

    def _cluster_of(self, row: Mapping) -> Optional[str]:
        value = row.get(self.cluster) if self.cluster else row.get("cluster")
        return None if value in (None, "") else str(value)

    async def judge(self, raw: Any, result: Any) -> None:
        """After a case's run: its evaluators, and the verdict on its record."""
        if isinstance(raw, _Trial):
            row, repeat, digest = raw.row, raw.repeat, raw.case_hash
        else:  # a source given on the command line: its rows are the cases
            row = self._rows.get(result.key) or (
                raw if isinstance(raw, Mapping) else {"input": raw}
            )
            repeat, digest = 0, case_hash(row)
        case = str(row.get("id", result.key))
        sent = self._capture.by_key.pop(result.key, [])
        output = sent[0] if len(sent) == 1 else (sent or None)
        if result.status not in (ITEM_OK, ITEM_EMPTY):
            verdict = {"passed": False, "error": result.error or result.status, "checks": {}}
        else:
            avail = {
                "input": row.get("input"),
                "output": output,
                "expected": row.get("expected"),
                "row": row,
                "outputs": sent,
            }
            if self._wants_trace:
                trace = getattr(result, "trace", None)
                avail["trace"] = TraceView.from_trace(trace) if trace is not None else None
            verdict = case_verdict(await judge_all(self._prepared, avail))
        verdict["output"] = _clip(output)
        if verdict["output"] is not output:
            verdict["output_clipped"] = True  # a preview: rescore cannot judge it again
        if row.get("expected") is not None:
            verdict["expected"] = _clip(row.get("expected"))
        if row.get("tags"):
            verdict["tags"] = list(row["tags"])
        cluster = self._cluster_of(row)
        verdict.update(case=case, repeat=repeat, case_hash=digest)
        if cluster is not None:
            verdict["cluster"] = cluster
        verdict.update(_run_cost(getattr(result, "trace", None)))
        result.verdict = verdict
        self._verdicts.append(trial_of(result.key, verdict, result.ms))
        if self._writer is not None:
            meta = {"eval": {"fingerprint": self._fingerprint}, "judges": self._judges}
            item = item_of(self._experiment.experiment_id, result)
            self._writer.submit([item, *scores_of(meta, self._experiment, result)])

    # -- the run's numbers ---------------------------------------------------

    def summarize(self, status: str) -> tuple:
        """What run.json says about the eval, and the run's status.

        Without a gate: failed when a trial failed, or with a threshold,
        when the rate is under it (1.9.0, unchanged). With one: whatever
        the gate decides (see :mod:`operonx.app.evals.gate`).
        """
        vs, self._verdicts = self._verdicts, []
        nums, cases, metrics = numbers(vs, self.repeats)
        trials, passed, pass_rate = nums["trials"], nums["passed"], nums["pass_rate"]
        summary: Dict[str, Any] = {
            "dataset": str(self.dataset.path),
            "threshold": self.threshold,
            **nums,
        }
        if self.variant:
            summary["variant"] = self.variant
        if self.dataset.selection:
            summary["selection"] = self.dataset.selection
        fp = self._fingerprint
        if fp is not None and self._code is not None:
            code = self._code.result()
            fp = {
                **fp,
                "code_version": code.get("version"),
                "version_dirty": code.get("version_dirty"),
            }
        self._fingerprint = fp
        summary["fingerprint"] = fp

        if self.gate is None:
            reasons: List[str] = []
            if status != RUN_OK:
                reasons.append(f"the run itself ended {status}")
            elif trials:
                if self.threshold is not None and pass_rate < self.threshold:
                    reasons.append(f"pass rate {pass_rate:.1%} < threshold {self.threshold:.1%}")
                elif self.threshold is None and passed < trials:
                    reasons.append(f"{trials - passed} of {trials} trials failed")
                if reasons:
                    status = RUN_FAILED
            ok = status == RUN_OK
            summary["gate"] = {
                "verdict": PASS if ok else FAILED,
                "exit_code": 0 if ok else 1,
                "reasons": reasons,
            }
        else:
            gate = decide(
                self.gate,
                current=cases,
                metrics=metrics,
                complete=self._complete,
                baseline=self._baseline,
                fingerprint=self._fingerprint,
            )
            if self._from_git is not None and "comparison" in gate:
                gate["comparison"]["baseline_ref"] = self._from_git[3]
            summary["gate"] = gate
            if gate["exit_code"] == 0:
                status = RUN_OK
            elif gate["verdict"] != ERROR or status == RUN_OK:
                status = RUN_FAILED
        return {"eval": summary}, status

    # -- judging a recorded run again ---------------------------------------

    async def rescore(
        self,
        run_id: str,
        evaluators: Optional[Sequence[Any]] = None,
        *,
        store: Any = None,
        dataset: Any = None,
        scores: Any = None,
    ) -> Any:
        """Judge this eval's recorded run *run_id* again without running the
        graph (:func:`~operonx.app.evals.rescore`). Without
        *evaluators*, the eval's own — its judges left out, and named in
        ``skipped``. *store* is the run store its traces went to; *scores*
        a score store the new scores go to."""
        from .rescoring import is_judge, rescore

        path = Path(self.record_dir) / self.name / run_id
        if not (path / "run.json").is_file():
            raise ValueError(f"eval {self.name!r}: no run {run_id!r} under {path.parent}")
        chosen = list(self.evaluators if evaluators is None else evaluators)
        skipped: List[str] = []
        if evaluators is None:
            skipped = [_name(ev) for ev in chosen if is_judge(ev)]
            chosen = [ev for ev in chosen if not is_judge(ev)]
        out = await rescore(
            path,
            chosen,
            store=store,
            dataset=self.dataset if dataset is None else dataset,
            scores=None if scores is None else _open_scores(_check_scores(self.name, scores)),
        )
        out.skipped = skipped
        return out

    # -- declared, described -------------------------------------------------

    @classmethod
    def from_spec(cls, spec: Any, root: Union[str, Path, None] = None) -> "Eval":
        """An Eval from a ``[[job]]`` block with ``dataset`` and ``evaluators``
        (and optionally ``threshold``, ``repeats``, ``cluster``, ``[job.gate]``)."""
        from ..serve.registry import load_object

        root = Path(root) if root is not None else Path.cwd()
        opts = dict(spec.options)
        evaluators = [
            load_object(e, field=f"[[job]] {spec.name!r} evaluators") if isinstance(e, str) else e
            for e in opts.get("evaluators") or []
        ]
        gate = opts.get("gate")
        if gate is not None and not isinstance(gate, Mapping):
            raise ValueError(f"[[job]] {spec.name!r}: `gate` is a table of Gate settings")
        record_dir = Path(spec.record_dir) if spec.record_dir else Path("evals")
        return cls(
            spec.name,
            graph=spec.graph,
            dataset=dataset_path(opts["dataset"], root),
            evaluators=evaluators,
            threshold=opts.get("threshold"),
            repeats=opts.get("repeats", 1),
            cluster=opts.get("cluster"),
            gate=Gate.from_options(gate) if gate is not None else None,
            scores=opts.get("scores"),
            root=root,
            record_dir=record_dir if record_dir.is_absolute() else root / record_dir,
            concurrency=spec.concurrency,
            item_timeout=spec.item_timeout,
            trace=list(spec.trace) if spec.trace is not None else None,
            inputs=dict(spec.inputs),
            item_input=spec.item_input,
            schedule=spec.schedule,
            description=spec.description,
        )

    def describe(self) -> Dict[str, Any]:
        out = super().describe()
        # the cases come from the dataset and the outputs go to the verdicts:
        # the source and sink underneath are plumbing, not what the eval is
        out.update(
            {
                "kind": "eval",
                "source": str(self.dataset.path),
                "sink": None,
                "dataset": str(self.dataset.path),
                "evaluators": [_name(e) for e in self.evaluators],
                "threshold": self.threshold,
            }
        )
        if self.repeats > 1:
            out["repeats"] = self.repeats
        if self.variant:
            out["variant"] = self.variant
        if self.dataset.selection:
            out["selection"] = self.dataset.selection
        if self.cluster:
            out["cluster"] = self.cluster
        if self.gate is not None:
            out["gate"] = self.gate.describe()
        if self._judges:
            out["judges"] = list(self._judges)
        project = run_metadata().get("project")
        if project:
            out["project"] = str(project)
        return out


# ── verdicts and the numbers over them (an eval run and a rescore share them) ──


def case_verdict(checks: Dict[str, Any]) -> Dict[str, Any]:
    """A case's checks as its verdict: passed when every check passed."""
    verdict = {
        "passed": all(c["passed"] for c in checks.values()) if checks else True,
        "checks": checks,
    }
    cost = [c["cost_usd"] for c in checks.values() if c.get("cost_usd") is not None]
    if cost:
        verdict["judge_cost_usd"] = round(sum(cost), 8)
    return verdict


def trial_of(key: str, verdict: Mapping[str, Any], ms: float) -> Dict[str, Any]:
    """One judged trial, as the statistics read it."""
    return {
        "key": key,
        "case": verdict.get("case", key),
        "repeat": verdict.get("repeat", 0),
        "case_hash": verdict.get("case_hash"),
        "cluster": verdict.get("cluster"),
        "tags": verdict.get("tags") or (),
        "passed": verdict["passed"],
        "checks": {k: v["passed"] for k, v in (verdict.get("checks") or {}).items()},
        "ms": ms,
        "error": bool(verdict.get("error")),
        "judge_cost_usd": verdict.get("judge_cost_usd"),
    }


def _metrics(cases: Mapping[str, CaseOutcome]) -> Dict[str, Estimate]:
    """``pass`` and each check: the mean over cases of the share of their
    trials that passed, with its interval."""
    names = [PASS_METRIC] + sorted({m for o in cases.values() for m in o.checks})
    clustered = any(o.cluster is not None for o in cases.values())
    out: Dict[str, Estimate] = {}
    for metric in names:
        held = [(o.share(metric), o.cluster or o.case) for o in cases.values()]
        held = [h for h in held if h[0] is not None]
        if held:
            out[metric] = estimate(
                [h[0] for h in held],
                [h[1] for h in held] if clustered else None,
                bounds=(0.0, 1.0),
            )
    return out


def _reliability(cases: Mapping[str, CaseOutcome], repeats: int) -> Dict[str, Any]:
    by = {STABLE_PASS: 0, STABLE_FAIL: 0, FLAKY: 0}
    for o in cases.values():
        by[o.stability] += 1
    flaky = sorted(c for c, o in cases.items() if o.stability == FLAKY)
    hat = {}
    for k in range(1, repeats + 1):
        vals = [
            pass_hat_k(sum(o.passed), len(o.passed), k)
            for o in cases.values()
            if len(o.passed) >= k
        ]
        if vals:
            hat[str(k)] = round(sum(vals) / len(vals), 6)
    return {**by, "flaky_cases": flaky[:FLAKY_LIST_MAX], "pass_hat_k": hat}


def numbers(
    vs: Sequence[Mapping[str, Any]], repeats: int
) -> Tuple[Dict[str, Any], Dict[str, CaseOutcome], Dict[str, Estimate]]:
    """The counts, rates, per-check tallies and metrics over judged trials
    (:func:`trial_of`): what ``run.json["eval"]`` reports beside the
    dataset, threshold, fingerprint and gate."""
    trials = len(vs)
    passed = sum(1 for v in vs if v["passed"])
    per_check: Dict[str, Dict[str, int]] = {}
    for v in vs:
        for k, ok in v["checks"].items():
            c = per_check.setdefault(k, {"passed": 0, "cases": 0})
            c["cases"] += 1
            c["passed"] += int(ok)
    ms = sorted(v["ms"] for v in vs if v["ms"])
    judge = [v["judge_cost_usd"] for v in vs if v.get("judge_cost_usd") is not None]
    cases = outcomes(vs)
    if repeats == 1:
        n_cases = trials
        pass_rate = round(passed / trials, 4) if trials else None
    else:
        shares = [o.share(PASS_METRIC) for o in cases.values()]
        n_cases = len(cases)
        pass_rate = round(sum(shares) / len(shares), 4) if shares else None
    metrics = _metrics(cases)
    out: Dict[str, Any] = {
        "cases": n_cases,
        "passed": passed,
        "failed": trials - passed,
        "errored": sum(1 for v in vs if v["error"]),
        "pass_rate": pass_rate,
        "checks": per_check,
        "p50_ms": ms[len(ms) // 2] if ms else None,
        "judge_cost_usd": round(sum(judge), 8) if judge else None,
        "repeats": repeats,
        "trials": trials,
        "metrics": {k: e.as_dict() for k, e in metrics.items()},
    }
    if repeats > 1:
        out["reliability"] = _reliability(cases, repeats)
    return out, cases, metrics


def _check_scores(name: str, value: Any) -> Any:
    """``scores=`` as given, checked now; opened when the run starts."""
    if value is None or isinstance(value, (ScoreStore, Mapping)):
        return value
    if isinstance(value, str):
        if not value.startswith("score_store:"):
            raise ValueError(
                f"eval {name!r}: scores={value!r} is not a score store key; "
                "name one as 'score_store:<name>' (resources.yaml), or pass a spec mapping"
            )
        return value
    raise TypeError(
        f"eval {name!r}: scores is a ScoreStore, a 'score_store:<name>' key or a spec "
        f"mapping, not a {type(value).__name__}"
    )


def _open_scores(value: Any) -> ScoreStore:
    if isinstance(value, ScoreStore):
        return value
    if isinstance(value, Mapping):
        return open_score_store(dict(value))
    from operonx.core.registry import ResourceHub

    return ResourceHub.instance().get(value)


def _run_cost(trace: Any) -> Dict[str, Any]:
    """The case run's own LLM cost and tokens, as the run store counts them
    (an execution reporting ``cost_usd`` is an LLM call; the cost is
    ``None`` when none was priced). Nothing for a run with no LLM call."""
    calls, cost, tokens_in, tokens_out = 0, None, 0, 0
    for node in getattr(trace, "nodes", None) or ():
        out = node.outputs
        if not isinstance(out, dict) or "cost_usd" not in out:
            continue
        calls += 1
        if isinstance(out["cost_usd"], (int, float)):
            cost = (cost or 0.0) + float(out["cost_usd"])
        usage = out.get("usage")
        if isinstance(usage, dict):
            tokens_in += int(usage.get("prompt_tokens") or 0)
            tokens_out += int(usage.get("completion_tokens") or 0)
    if not calls:
        return {}
    return {"cost_usd": cost, "tokens_in": tokens_in, "tokens_out": tokens_out}


def _clip(value: Any, limit: int = 4000) -> Any:
    """A value small enough for a record line; large ones as a preview.
    Returns *value* itself when it fits — anything else is a preview."""
    try:
        text = json.dumps(value, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return repr(value)[:limit]
    return value if len(text) <= limit else text[:limit] + "…"
