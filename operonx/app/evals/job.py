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
from operonx.telemetry.runs.model import percentile
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
from .judges import Judging, evaluator_of
from .publish import ScoreWriter, experiment_of, item_of, scores_of, warn_lost
from .stats import Estimate, estimate, pass_hat_k
from .traceview import TraceView, run_cost

__all__ = ["Eval", "gate_run", "git_baseline", "numbers", "record_baseline"]

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
    (``concurrency``, ``timeout``, ``inputs``, ``input``,
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
        judge_cache: With a score store, a judge's verdict is kept there,
            keyed by its version and what it was shown, and an unchanged
            case is not judged again (no call). ``False`` asks every time.
        judge_concurrency: Judge runs in flight at once, over all cases.
    """

    origin = "eval"
    items_fail_run = False  # a failed case is a failed verdict; the gate decides
    folder = "evals"

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
        record_dir: Union[str, Path, None] = None,
        scores: Any = None,
        scores_timeout: float = 10.0,
        variant: Optional[str] = None,
        judge_cache: bool = True,
        judge_concurrency: int = 8,
        **kwargs: Any,
    ):
        self.dataset = dataset if isinstance(dataset, Dataset) else Dataset(dataset_path(dataset))
        self.evaluators = [evaluator_of(ev) for ev in evaluators]
        self._prepared = [prepare(ev) for ev in self.evaluators]
        # a case's trace view is built only when some evaluator can take it
        self._wants_trace = any(p.wants("trace") for p in self._prepared)
        # text, so never lazy: made only for an evaluator that names it (one taking
        # **kwargs has `trace`, and `trace.as_text()` when it wants it)
        self._wants_summary = any(
            p.params is not None and "trace_summary" in p.params for p in self._prepared
        )
        self._wants_judging = any(p.wants("judging") for p in self._prepared)
        if isinstance(judge_concurrency, bool) or int(judge_concurrency) < 1:
            raise ValueError(f"eval {name!r}: judge_concurrency is a number of judge runs, ≥ 1")
        self.judge_cache = bool(judge_cache)
        self.judge_concurrency = int(judge_concurrency)
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
        self._judging: Optional[Judging] = None
        self._judge_counts: Dict[str, Dict[str, Any]] = {}
        self._alignments: Optional[Dict[str, Dict[str, Dict[str, Any]]]] = None
        self._store: Optional[ScoreStore] = None
        self._writer: Optional[ScoreWriter] = None
        self._experiment: Optional[Experiment] = None
        self._rows: Dict[str, Dict[str, Any]] = {}
        self._verdicts: List[Dict[str, Any]] = []
        self._fingerprint: Optional[Dict[str, Any]] = None
        self._code: Optional["Future[Dict[str, Any]]"] = None
        self._baseline: Optional[Tuple[str, Dict[str, CaseOutcome], Optional[Dict]]] = None
        # a "main" / "git:<ref>" baseline: found before the record opens
        self._from_git: Optional[Tuple[str, Dict[str, CaseOutcome], Dict, str]] = None
        self._complete = False
        super().__init__(
            name, graph=graph, items=self._cases, key="id", record_dir=record_dir, **kwargs
        )

    # -- the experiment in a score store ---------------------------------------

    def begin(self, run_id: str, started: str) -> None:
        """The runner opened the record: the judges get their context (the
        eval's sinks, this run's ids, the cache); with ``scores=``, the
        experiment's ``running`` row goes to the store before any case."""
        self._writer, self._experiment = None, None
        self._judge_counts = {}
        self._judging = Judging(
            trace=self.engine().trace_consumers,
            metadata={"job": self.name, "job_run": run_id},
            cache=self._store if self.judge_cache else None,
            concurrency=self.judge_concurrency,
        )
        if self._store is None:
            return
        opened = JobRun(
            job=self.name,
            run_id=run_id,
            path=self.records() / self.name / run_id,
            status=RUN_RUNNING,
            started=started,
            ended=None,
            counts={},
            meta=self.describe(),
        )
        self._experiment = experiment_of(opened)
        self._writer = ScoreWriter(self._store, self.name)
        self._writer.submit([self._experiment])

    async def run(self, *, resume: bool = False, **kwargs: Any) -> JobRun:
        """Run the eval once; with ``scores=``, then wait (up to
        ``scores_timeout``) for the store to take the experiment."""
        # opened before the record: a store that cannot be named fails the
        # run before it starts, not with a record stuck at "running"
        self._store = _open_scores(self.scores) if self.scores is not None else None
        # so is a commit's baseline: no experiment there, no run (and no cost)
        ref = git_ref(self.gate.baseline) if self.gate is not None else None
        self._from_git = await asyncio.to_thread(self._git_baseline, ref) if ref else None
        # each judge's alignment record, read once: the report warns on an unvalidated one
        self._alignments = (
            await asyncio.to_thread(self._read_alignments) if self._store is not None else None
        )
        run: Optional[JobRun] = None
        try:
            run = await super().run(resume=resume, **kwargs)
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
        rows = self.dataset.rows()
        _, evaluators_hash = evaluators_version({_name(ev): ev for ev in self.evaluators})
        return git_baseline(
            self.name,
            str(self.gate.baseline),  # type: ignore[union-attr]
            ref,
            root=self.root or Path.cwd(),
            store=self._store,
            dataset_version=dataset_version(rows),
            evaluators_hash=evaluators_hash,
        )

    def _read_alignments(self) -> Dict[str, Dict[str, Dict[str, Any]]]:
        from .align import alignments_of

        return {name: alignments_of(self._store, name) for name in self._judges}

    def _load_baseline(self) -> Optional[Tuple[str, Dict[str, CaseOutcome], Optional[Dict]]]:
        return record_baseline(self.records(), self.name, str(self.gate.baseline))  # type: ignore[union-attr]

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
        else:  # items given on the command line: its rows are the cases
            row = self._rows.get(result.key) or (
                raw if isinstance(raw, Mapping) else {"input": raw}
            )
            repeat, digest = 0, case_hash(row)
        case = str(row.get("id", result.key))
        output = result.result
        # every frame the case sent: one result is one frame, several are a list
        sent = output if result.sent > 1 else ([output] if output is not None else [])
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
            trace = getattr(result, "trace", None)
            if self._wants_trace or self._wants_summary:
                view = TraceView.from_trace(trace) if trace is not None else None
                avail["trace"] = view
                if self._wants_summary:
                    avail["trace_summary"] = view.as_text() if view is not None else None
            if self._wants_judging and self._judging is not None:
                avail["judging"] = self._judging.for_case(
                    key=result.key, case=case, judged_trace=result.trace_id
                )
            checks = await judge_all(self._prepared, avail)
            self._count_judges(checks)
            verdict = case_verdict(checks)
        result.verdict = recorded_verdict(
            verdict,
            row,
            output,
            case=case,
            repeat=repeat,
            digest=digest,
            cluster=self._cluster_of(row),
            trace=getattr(result, "trace", None),
        )
        self._verdicts.append(trial_of(result.key, result.verdict, result.ms))
        if self._writer is not None:
            meta = {"eval": {"fingerprint": self._fingerprint}, "judges": self._judges}
            item = item_of(self._experiment.experiment_id, result)
            self._writer.submit([item, *scores_of(meta, self._experiment, result)])

    def _count_judges(self, checks: Mapping[str, Any]) -> None:
        for name in self._judges:
            c = checks.get(name)
            if c is None:
                continue
            n = self._judge_counts.setdefault(
                name, {"calls": 0, "cached": 0, "errors": 0, "cost_usd": None}
            )
            if c.get("cached"):
                n["cached"] += 1
            elif c.get("judge_trace_id"):
                n["calls"] += 1
            if c.get("error"):
                n["errors"] += 1
            if c.get("cost_usd") is not None:
                n["cost_usd"] = round((n["cost_usd"] or 0.0) + float(c["cost_usd"]), 10)

    def _gating(self, name: str) -> bool:
        """Whether *name*'s check can move the verdict (D60)."""
        gate = self.gate
        if gate is None:
            return True  # 1.9.0: any failed check fails the run
        thresholds = gate.thresholds()
        if PASS_METRIC in thresholds or name in thresholds:
            return True
        if gate.baseline is not None and (PASS_METRIC in gate.gated() or name in gate.gated()):
            return True
        tag = gate.must_pass_tag
        return bool(tag) and any(tag in (row.get("tags") or ()) for row in self._rows.values())

    def _judge_report(self, fp: Optional[Mapping[str, Any]]) -> Tuple[Dict[str, Any], List[str]]:
        """``summary["judges"]`` and the warnings it calls for (D57, D58, D60)."""
        from .align import ALIGNED_KAPPA, describe

        versions = (fp or {}).get("evaluators") or {}
        system_models = (fp or {}).get("models")
        out: Dict[str, Any] = {}
        warnings: List[str] = []
        by_name = {_name(ev): ev for ev in self.evaluators}
        for name in self._judges:
            ev = by_name[name]
            version = versions.get(name)
            models = ev.models() if hasattr(ev, "models") else []
            resolved = ev.model_resolved() if hasattr(ev, "model_resolved") else True
            counts = self._judge_counts.get(
                name, {"calls": 0, "cached": 0, "errors": 0, "cost_usd": None}
            )
            self_pref = (
                bool(set(models) & set(system_models)) if system_models is not None else None
            )
            records = (self._alignments or {}).get(name) if self._alignments is not None else None
            record = records.get(version) if records else None
            gating = self._gating(name)
            entry = {
                "version": version,
                "model": models[0] if len(models) == 1 else (models or None),
                "model_resolved": resolved,
                **counts,
                "gating": gating,
                "self_preference": self_pref,
                "alignment": None,
            }
            mine: List[str] = []
            if record is not None:
                entry["alignment"] = {
                    k: record.get(k)
                    for k in ("kappa", "kappa_ci", "tpr", "tnr", "accuracy", "n", "human")
                }
            if not resolved:
                mine.append(
                    f"judge {name!r}: its resource is not declared in the resource hub, so its "
                    "version names the resource, not the model behind it"
                )
            if self_pref:
                mine.append(
                    f"judge {name!r} runs on {entry['model']!r}, a model the system under test "
                    "also uses (self-preference: a model tends to favour its own answers) — "
                    "judge with another model"
                )
            if gating:
                fix = f"measure it: `operonx eval align {name}` on human-labelled cases"
                if self._alignments is None:
                    mine.append(
                        f"UNVALIDATED JUDGE {name!r} gates this eval and there is no score store "
                        f"to read an alignment record from (Eval(scores=…)); {fix}"
                    )
                elif record is None:
                    other = sorted(records) if records else []
                    found = (
                        f" (records exist for {', '.join(other)}: a rubric, model or graph "
                        "change makes a new judge)"
                        if other
                        else ""
                    )
                    mine.append(
                        f"UNVALIDATED JUDGE {name!r} gates this eval and has no alignment record "
                        f"for its version {version}{found}: nothing shows its verdicts agree "
                        f"with people; {fix}"
                    )
                elif record.get("kappa") is None or record["kappa"] < ALIGNED_KAPPA:
                    k = record.get("kappa")
                    said = f"κ = {k:.2f} < {ALIGNED_KAPPA}" if k is not None else "κ undefined"
                    mine.append(
                        f"UNVALIDATED JUDGE {name!r} gates this eval with {said} against human "
                        f"labels ({describe(record)}): it disagrees with people too often to "
                        "trust alone"
                    )
            entry["warnings"] = mine
            warnings += mine
            out[name] = entry
        return out, warnings

    # -- the run's numbers ---------------------------------------------------

    def summarize(self, status: str) -> tuple:
        """What run.json says about the eval, and the run's status.

        Without a gate: failed when a trial failed, or with a threshold,
        when the rate is under it (1.9.0, unchanged). With one: whatever
        the gate decides (see :mod:`operonx.app.evals.gate`).
        """
        vs, self._verdicts = self._verdicts, []
        nums, cases, metrics = numbers(vs, self.repeats)
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

        summary["gate"], status = gate_run(
            self.gate,
            self.threshold,
            status=status,
            nums=nums,
            cases=cases,
            metrics=metrics,
            complete=self._complete,
            baseline=self._baseline,
            fingerprint=fp,
            baseline_ref=self._from_git[3] if self._from_git is not None else None,
        )
        if self._judges:
            summary["judges"], warned = self._judge_report(fp)
            if warned:  # loud, but not a verdict: an unvalidated judge can still gate (T4)
                summary["gate"]["warnings"] = list(summary["gate"].get("warnings") or []) + warned
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

        path = self.records() / self.name / run_id
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

    def describe(self) -> Dict[str, Any]:
        out = super().describe()
        # the cases come from the dataset and the outputs go to the verdicts:
        # the items underneath are plumbing, not what the eval is
        out.update(
            {
                "kind": "eval",
                "items": str(self.dataset.path),
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


def recorded_verdict(
    verdict: Dict[str, Any],
    row: Mapping[str, Any],
    output: Any,
    *,
    case: str,
    repeat: int,
    digest: str,
    cluster: Optional[str],
    trace: Any,
) -> Dict[str, Any]:
    """A case's verdict as its record line holds it: the clipped output
    and expected value, tags, which case and repeat, its hash and cluster,
    and the case run's own cost."""
    verdict["output"] = _clip(output)
    if verdict["output"] is not output:
        verdict["output_clipped"] = True  # a preview: rescore cannot judge it again
    if row.get("expected") is not None:
        verdict["expected"] = _clip(row.get("expected"))
    if row.get("tags"):
        verdict["tags"] = list(row["tags"])
    verdict.update(case=case, repeat=repeat, case_hash=digest)
    if cluster is not None:
        verdict["cluster"] = cluster
    verdict.update(run_cost(trace))
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
        "cost_usd": verdict.get("cost_usd"),
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
    cost = [v["cost_usd"] for v in vs if v.get("cost_usd") is not None]
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
        "p95_ms": percentile(ms, 95) if ms else None,
        "cost_usd": round(sum(cost), 10) if cost else None,
        "judge_cost_usd": round(sum(judge), 8) if judge else None,
        "repeats": repeats,
        "trials": trials,
        "metrics": {k: e.as_dict() for k, e in metrics.items()},
    }
    if repeats > 1:
        out["reliability"] = _reliability(cases, repeats)
    return out, cases, metrics


def gate_run(
    gate: Optional[Gate],
    threshold: Optional[float],
    *,
    status: str,
    nums: Mapping[str, Any],
    cases: Mapping[str, CaseOutcome],
    metrics: Mapping[str, Estimate],
    complete: bool,
    baseline: Optional[Tuple[str, Mapping[str, CaseOutcome], Optional[Mapping]]] = None,
    fingerprint: Optional[Mapping[str, Any]] = None,
    baseline_ref: Optional[str] = None,
) -> Tuple[Dict[str, Any], str]:
    """The gate block of a judged run, and the run's status.

    Without a gate: failed when a trial failed, or with a *threshold*,
    when the rate is under it (1.9.0, unchanged). With one: whatever the
    gate decides (see :mod:`operonx.app.evals.gate`)."""
    if gate is None:
        trials, passed, pass_rate = nums["trials"], nums["passed"], nums["pass_rate"]
        reasons: List[str] = []
        if status != RUN_OK:
            reasons.append(f"the run itself ended {status}")
        elif trials:
            if threshold is not None and pass_rate < threshold:
                reasons.append(f"pass rate {pass_rate:.1%} < threshold {threshold:.1%}")
            elif threshold is None and passed < trials:
                reasons.append(f"{trials - passed} of {trials} trials failed")
            if reasons:
                status = RUN_FAILED
        ok = status == RUN_OK
        return {
            "verdict": PASS if ok else FAILED,
            "exit_code": 0 if ok else 1,
            "reasons": reasons,
        }, status
    block = decide(
        gate,
        current=cases,
        metrics=metrics,
        complete=complete,
        baseline=baseline,
        fingerprint=fingerprint,
    )
    if baseline_ref is not None and "comparison" in block:
        block["comparison"]["baseline_ref"] = baseline_ref
    if block["exit_code"] == 0:
        status = RUN_OK
    elif block["verdict"] != ERROR or status == RUN_OK:
        status = RUN_FAILED
    return block, status


def record_baseline(
    record_dir: Path, name: str, ref: str
) -> Optional[Tuple[str, Dict[str, CaseOutcome], Optional[Dict]]]:
    """A baseline from *name*'s job records: ``"latest"`` (its last finished,
    judged run that is not an ``error``; ``None`` when there is none) or a
    run id. ``(run_id, outcomes, fingerprint)``."""
    if ref == "latest":
        for path in reversed(runs_of(record_dir, name)):
            run = JobRun.load(path)
            ev = run.meta.get("eval") or {}
            gate = ev.get("gate") or {}
            if run.status != RUN_RUNNING and run.ended and ev and gate.get("verdict") != ERROR:
                break
        else:
            return None
    else:
        path = record_dir / name / ref
        if not (path / "run.json").is_file():
            raise ValueError(
                f"eval {name!r}: baseline run {ref!r} is not under {record_dir / name}"
            )
        run = JobRun.load(path)
    ev = run.meta.get("eval") or {}
    return run.run_id, outcomes(trials_of_items(run.items)), ev.get("fingerprint")


def git_baseline(
    name: str,
    baseline: str,
    ref: str,
    *,
    root: Path,
    store: Optional[ScoreStore],
    dataset_version: Optional[str] = None,
    evaluators_hash: Optional[str] = None,
) -> Tuple[str, Dict[str, CaseOutcome], Dict, str]:
    """The experiment of ``git merge-base HEAD <ref>``, from *store* (else
    the project's score store at *root*): ``(id, outcomes, fingerprint,
    "git:<ref> @ <sha>")`` — the one with this *dataset_version* and
    *evaluators_hash* when there is one, else the newest. None there
    raises ``ValueError`` saying how to get one."""
    from operonx.telemetry.scores import project_score_store

    from .experiments import ExperimentData, find_baseline, merge_base, store_name

    sha = merge_base(root, ref)
    if store is not None:
        opened, owned, where = store, False, store_name(store)
    else:
        src = project_score_store(root)
        opened, owned, where = src.open(), True, f"{src.describe()} ({src.source})"
    try:
        exp = find_baseline(
            opened,
            name,
            sha,
            dataset_version=dataset_version,
            evaluators_hash=evaluators_hash,
        )
        data = ExperimentData.from_store(opened, exp.experiment_id) if exp else None
    finally:
        if owned:
            opened.close()
    if data is None:
        branch = ref.split("/", 1)[-1]
        raise ValueError(
            f"eval {name!r}: baseline {baseline!r} is the experiment at {sha}, the merge-base "
            f"of HEAD and {ref}, and the score store ({where}) has no finished, clean run of "
            f"this eval there. Run the eval on {branch} first so its experiment is stored "
            f"(`operonx eval run {name}` in a pipeline on {branch}, or a scheduled one, keeps "
            "baselines warm), or compare with baseline='latest'"
        )
    return data.experiment_id, data.outcomes(), data.fingerprint, f"git:{ref} @ {sha}"


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


def _clip(value: Any, limit: int = 4000) -> Any:
    """A value small enough for a record line; large ones as a preview.
    Returns *value* itself when it fits — anything else is a preview."""
    try:
        text = json.dumps(value, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return repr(value)[:limit]
    return value if len(text) <= limit else text[:limit] + "…"
