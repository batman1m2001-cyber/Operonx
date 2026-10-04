"""Experiments, wherever they are kept — a job record or a score store.

An eval run is an experiment. The machine that ran it has its job record
(``run.json`` and ``items.jsonl``); with a score store, every machine has
its rows. :class:`ExperimentData` is one experiment read from either, in
one shape — ``summary`` is ``run.json["eval"]``'s, ``items`` one dict per
trial — so the reports, ``compare``, ``calibrate`` and ``power`` never ask
where it came from::

    exp = load_experiment("20261004T101500-000001", store=store, record_dirs=["evals"])
    exp.summary["gate"]["verdict"], exp.outcomes()["refund-1"].passed

:func:`merge_base` and :func:`find_baseline` are how ``Gate(baseline=
"main")`` finds the experiment main's code produced: the eval's
experiment at ``git merge-base HEAD origin/main``.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Union

from operonx.telemetry.scores import Experiment, ExperimentFilter, ScoreFilter, ScoreStore

from ..jobs.record import JobRun
from .gate import ERROR, CaseOutcome, outcomes

__all__ = [
    "ExperimentData",
    "MAIN_REF",
    "experiments_of",
    "find_baseline",
    "git_ref",
    "load_experiment",
    "merge_base",
    "store_name",
]

#: What ``baseline="main"`` compares against: the remote's main branch.
#: A repository whose default branch has another name says ``git:<ref>``.
MAIN_REF = "origin/main"

#: The fingerprint's fields an experiment row holds as columns.
_FINGERPRINT = (
    "code_version",
    "version_dirty",
    "graph_hash",
    "config_hash",
    "dataset_version",
    "evaluators_hash",
    "operonx_version",
)


def _iso(epoch: Optional[float]) -> Optional[str]:
    if epoch is None:
        return None
    return datetime.fromtimestamp(epoch, timezone.utc).isoformat(timespec="seconds")


@dataclass
class ExperimentData:
    """One experiment: ``summary`` in ``run.json["eval"]``'s shape (counts,
    ``metrics``, ``gate``, ``fingerprint``, ``reliability``…), and one
    item per trial — ``key``, ``case``, ``repeat``, ``status``,
    ``passed``, ``checks`` (name → ``{passed, score?, reason?, …}``),
    ``error``, ``output``, ``expected`` (records only), ``tags``,
    ``cluster``, ``case_hash``, ``trace_id``, ``ms``, ``cost_usd``.
    ``source`` says where it was read: ``record <path>`` or ``store``."""

    experiment_id: str
    eval: str
    status: str
    started: Optional[str]
    ended: Optional[str]
    summary: Dict[str, Any]
    items: List[Dict[str, Any]] = field(default_factory=list)
    source: str = ""
    path: Optional[Path] = None

    @property
    def gate(self) -> Dict[str, Any]:
        return dict(self.summary.get("gate") or {})

    @property
    def fingerprint(self) -> Dict[str, Any]:
        return dict(self.summary.get("fingerprint") or {})

    @property
    def metrics(self) -> Dict[str, Any]:
        return dict(self.summary.get("metrics") or {})

    def trials(self) -> List[Dict[str, Any]]:
        """The items as the statistics read them (``gate.outcomes``)."""
        return [
            {
                "case": i["case"],
                "passed": bool(i.get("passed")),
                "checks": {k: bool(c.get("passed")) for k, c in (i.get("checks") or {}).items()},
                "error": bool(i.get("error")) and i.get("status") not in ("ok", "empty"),
                "case_hash": i.get("case_hash"),
                "cluster": i.get("cluster"),
                "tags": i.get("tags") or (),
            }
            for i in self.items
        ]

    def outcomes(self) -> Dict[str, CaseOutcome]:
        """Every trial grouped by case."""
        return outcomes(self.trials())

    def as_dict(self) -> Dict[str, Any]:
        return {
            "experiment_id": self.experiment_id,
            "eval": self.eval,
            "status": self.status,
            "started": self.started,
            "ended": self.ended,
            "source": self.source,
            "summary": self.summary,
            "items": self.items,
        }

    # -- reading -------------------------------------------------------------

    @classmethod
    def from_run(cls, run: JobRun) -> "ExperimentData":
        """An eval's job record."""
        summary = run.meta.get("eval")
        if not isinstance(summary, dict):
            raise ValueError(
                f"run {run.run_id} of {run.job!r} at {run.path} is not an eval run "
                "(its run.json has no 'eval' block)"
            )
        items = []
        for item in run.items:
            v = item.verdict
            if not v:
                continue  # skipped on resume: judged in the run it came from
            row = {
                "key": item.key,
                "case": str(v.get("case", item.key)),
                "repeat": int(v.get("repeat") or 0),
                "status": item.status,
                "passed": bool(v.get("passed")),
                "checks": dict(v.get("checks") or {}),
                "error": item.error or v.get("error"),
                "output": v.get("output"),
                "tags": list(v.get("tags") or []),
                "cluster": v.get("cluster"),
                "case_hash": v.get("case_hash"),
                "trace_id": item.trace_id,
                "ms": item.ms,
                "cost_usd": v.get("cost_usd"),
            }
            if "expected" in v:
                row["expected"] = v["expected"]
            if v.get("output_clipped"):
                row["output_clipped"] = True
            items.append(row)
        return cls(
            experiment_id=run.run_id,
            eval=run.job,
            status=run.status,
            started=run.started,
            ended=run.ended,
            summary=dict(summary),
            items=items,
            source=f"record {run.path}",
            path=run.path,
        )

    @classmethod
    def from_experiment(cls, exp: Experiment) -> "ExperimentData":
        """One store row as an experiment with its summary and no items —
        what a list shows, read without the items and scores."""
        meta = exp.metadata or {}
        summary: Dict[str, Any] = {
            "dataset": meta.get("dataset_path") or exp.dataset,
            **{
                k: meta[k]
                for k in (
                    "pass_rate",
                    "passed",
                    "failed",
                    "trials",
                    "threshold",
                    "reliability",
                    "judges",
                )
                if k in meta
            },
            "cases": exp.cases,
            "errored": exp.errored,
            "repeats": exp.repeats,
            "p50_ms": exp.p50_ms,
            "p95_ms": exp.p95_ms,
            "cost_usd": exp.cost_usd,
            "judge_cost_usd": exp.judge_cost_usd,
            "metrics": dict(exp.metrics or {}),
            "gate": dict(exp.gate or {}),
            "fingerprint": {
                **{k: getattr(exp, k) for k in _FINGERPRINT},
                **({"models": meta["models"]} if "models" in meta else {}),
            },
        }
        for k in ("variant", "split"):
            if getattr(exp, k):
                summary[k] = getattr(exp, k)
        return cls(
            experiment_id=exp.experiment_id,
            eval=exp.eval,
            status=exp.status,
            started=_iso(exp.started_at),
            ended=_iso(exp.ended_at),
            summary=summary,
            source="store",
        )

    @classmethod
    def from_store(cls, store: ScoreStore, experiment_id: str) -> Optional["ExperimentData"]:
        """An experiment's rows in *store*: the experiment, its items, and
        each item's scores as its checks. ``None`` when it is not there."""
        record = store.get_experiment(experiment_id)
        if record is None:
            return None
        exp = record.experiment
        checks: Dict[tuple, Dict[str, Any]] = {}
        limit = max(1, len(record.items)) * max(1, len(exp.metrics)) + 1
        for s in store.scores(ScoreFilter(experiment_id=experiment_id, target="item"), limit):
            check: Dict[str, Any] = {"passed": s.passed}
            if s.data_type == "numeric":
                check["score"] = s.value
            if s.label is not None:
                check["label"] = s.label
            if s.reason:
                check["reason"] = s.reason
            if s.op_id:
                check["op"] = s.op_id
            if s.cost_usd is not None:
                check["cost_usd"] = s.cost_usd
            check.update(s.metadata or {})
            checks.setdefault((s.case_id, int(s.repeat or 0)), {})[s.score_name] = check
        out = cls.from_experiment(exp)
        out.items = [
            {
                "key": it.case_id if exp.repeats <= 1 else f"{it.case_id}#{it.repeat}",
                "case": it.case_id,
                "repeat": it.repeat,
                "status": it.status,
                "passed": bool(it.passed),
                "checks": checks.get((it.case_id, it.repeat), {}),
                "error": it.error,
                "output": it.output,
                "tags": list(it.tags or []),
                "cluster": it.cluster,
                "case_hash": it.case_hash or None,
                "trace_id": it.trace_id,
                "ms": it.ms,
                "cost_usd": it.cost_usd,
            }
            for it in record.items
        ]
        return out


def load_experiment(
    ref: Union[str, Path, JobRun],
    *,
    store: Optional[ScoreStore] = None,
    record_dirs: Iterable[Union[str, Path]] = (),
) -> ExperimentData:
    """An experiment by *ref*: a :class:`JobRun`, a record directory, or an
    id — looked up under *record_dirs* first (``<dir>/<eval>/<id>``: a
    record holds the expected values and every verdict field), then in
    *store*."""
    if isinstance(ref, JobRun):
        return ExperimentData.from_run(ref)
    path = Path(str(ref))
    if (path / "run.json").is_file():
        return ExperimentData.from_run(JobRun.load(path))
    dirs = [Path(d) for d in record_dirs]
    text = str(ref)
    if "/" not in text and text not in (".", ".."):
        for d in dirs:
            for hit in sorted(d.glob(f"*/{text}/run.json")):
                return ExperimentData.from_run(JobRun.load(hit.parent))
    if store is not None:
        found = ExperimentData.from_store(store, text)
        if found is not None:
            return found
    looked = [f"record dirs {[str(d) for d in dirs]}"] if dirs else []
    if store is not None:
        looked.append("the score store")
    raise ValueError(
        f"no experiment {text!r}: not a run directory"
        + (f", and not in {' or '.join(looked)}" if looked else "")
    )


# ── baselines from git ───────────────────────────────────────────────────


def git_ref(baseline: Any) -> Optional[str]:
    """The git ref a ``Gate(baseline=…)`` names: ``"main"`` → ``origin/main``,
    ``"git:<ref>"`` → ``<ref>``; ``None`` for ``"latest"`` or a run id."""
    text = str(baseline) if baseline is not None else ""
    if text == "main":
        return MAIN_REF
    if text.startswith("git:"):
        return text[len("git:") :]
    return None


def merge_base(root: Union[str, Path], ref: str) -> str:
    """``git merge-base HEAD <ref>`` at *root*, as ``code_version`` writes
    a commit (12 hex). Raises ``ValueError`` saying how to make it work."""
    advice = (
        f"The ref must exist in this checkout with the history down to the fork point: "
        f"in CI, `git fetch origin {ref.split('/', 1)[-1]}` first, with full history "
        "(GitLab: GIT_DEPTH: 0; GitHub: actions/checkout with fetch-depth: 0)"
    )
    try:
        got = subprocess.run(
            ["git", "merge-base", "HEAD", ref],
            cwd=str(root),
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise ValueError(f"`git merge-base HEAD {ref}` could not run at {root}: {exc}") from None
    if got.returncode != 0 or not got.stdout.strip():
        why = (got.stderr.strip().splitlines() or ["no common ancestor"])[-1]
        raise ValueError(f"`git merge-base HEAD {ref}` failed at {root}: {why}. {advice}")
    return got.stdout.strip()[:12]


def find_baseline(
    store: ScoreStore,
    eval_name: str,
    code_version: str,
    *,
    dataset_version: Optional[str] = None,
    evaluators_hash: Optional[str] = None,
) -> Optional[Experiment]:
    """The experiment of *eval_name* that commit *code_version* produced:
    finished, not an infrastructure ``error``, on a clean tree (a dirty
    run is not that commit's code). The newest with the same
    *dataset_version* and *evaluators_hash* wins; else the newest."""
    found: List[Experiment] = []
    cursor = None
    while True:
        page = store.list_experiments(
            ExperimentFilter(eval=eval_name, code_version=code_version), limit=200, cursor=cursor
        )
        found.extend(page.items)
        cursor = page.next_cursor
        if not cursor:
            break
    usable = [
        e
        for e in found
        if e.ended_at is not None
        and e.status != "running"
        and not e.version_dirty
        and (e.gate or {}).get("verdict") != ERROR
    ]
    usable.sort(key=lambda e: e.started_at, reverse=True)
    for e in usable:
        if e.dataset_version == dataset_version and e.evaluators_hash == evaluators_hash:
            return e
    return usable[0] if usable else None


def store_name(store: Any) -> str:
    """A score store in a few words, for an error message."""
    where = getattr(store, "root", None) or getattr(store, "path", None)
    return f"{type(store).__name__} at {where}" if where else type(store).__name__


def experiments_of(
    eval_name: str,
    *,
    store: Optional[ScoreStore] = None,
    record_dirs: Sequence[Union[str, Path]] = (),
    limit: int = 50,
    items: bool = True,
) -> List[ExperimentData]:
    """*eval_name*'s finished experiments, newest first: its records, and
    those only the store holds (run elsewhere). ``items=False`` reads the
    summaries only — each record's ``run.json``, and the store's rows in
    one ``list_experiments`` call — for a list."""
    from ..jobs.record import RUN_RUNNING, runs_of

    out: Dict[str, ExperimentData] = {}
    for d in record_dirs:
        for path in reversed(runs_of(d, eval_name)):
            run = JobRun.load(path, items=items)
            if run.status != RUN_RUNNING and run.ended and isinstance(run.meta.get("eval"), dict):
                out.setdefault(run.run_id, ExperimentData.from_run(run))
    if store is not None:
        page = store.list_experiments(ExperimentFilter(eval=eval_name), limit=limit)
        for e in page.items:
            if e.experiment_id in out or e.ended_at is None:
                continue
            got = (
                ExperimentData.from_store(store, e.experiment_id)
                if items
                else ExperimentData.from_experiment(e)
            )
            if got is not None:
                out[e.experiment_id] = got
    ordered = sorted(out.values(), key=lambda d: (d.started or "", d.experiment_id), reverse=True)
    return ordered[:limit]
