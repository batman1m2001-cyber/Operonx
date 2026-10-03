"""An eval run as ScoreStore rows — written live, or published from its record.

One set of converters turns a job record into an
:class:`~operonx.telemetry.scores.Experiment`, its
:class:`~operonx.telemetry.scores.ExperimentItem` rows and one
:class:`~operonx.telemetry.scores.Score` per check per item. An eval with
``scores=`` sends them while it runs (through :class:`ScoreWriter`, so a
slow or dead store costs the run nothing); :func:`publish` sends a
recorded run — one whose store was down, or one run before the store
existed. Both use the same converters on the same record, and score ids
are derived from what was judged, so publishing what was already written
writes nothing new.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from operonx.core.loggings import LOGGER
from operonx.telemetry.runs.model import percentile
from operonx.telemetry.scores import Experiment, ExperimentItem, Score, ScoreStore
from operonx.telemetry.writer import BackgroundWriter

from ..jobs.record import ItemResult, JobRun

__all__ = ["ScoreWriter", "check_score", "experiment_of", "item_of", "publish", "scores_of"]

#: Check fields that have a column of their own on a score.
_SCORE_FIELDS = {"passed", "score", "label", "reason", "error", "op", "cost_usd", "judge_trace_id"}


def _epoch(stamp: Optional[str]) -> Optional[float]:
    return datetime.fromisoformat(stamp).timestamp() if stamp else None


def experiment_of(run: JobRun) -> Experiment:
    """A job record (finished or just started) as its experiment row."""
    meta = run.meta
    ev: Mapping[str, Any] = meta.get("eval") or {}
    fp: Mapping[str, Any] = ev.get("fingerprint") or {}
    gate: Mapping[str, Any] = ev.get("gate") or {}
    dataset = str(ev.get("dataset") or meta.get("dataset") or "")
    verdicts = [i.verdict for i in run.items if i.verdict]
    costs = [v["cost_usd"] for v in verdicts if v.get("cost_usd") is not None]
    ms = [i.ms for i in run.items if i.verdict and i.ms]
    return Experiment(
        experiment_id=run.run_id,
        eval=run.job,
        project=str(meta.get("project") or ""),
        dataset=Path(dataset).stem if dataset else "",
        dataset_version=str(fp.get("dataset_version") or ""),
        graph=str(meta.get("graph") or ""),
        code_version=fp.get("code_version"),
        version_dirty=fp.get("version_dirty"),
        graph_hash=str(fp.get("graph_hash") or ""),
        config_hash=str(fp.get("config_hash") or ""),
        evaluators_hash=str(fp.get("evaluators_hash") or ""),
        operonx_version=str(fp.get("operonx_version") or ""),
        repeats=int(ev.get("repeats") or meta.get("repeats") or 1),
        baseline_id=(gate.get("comparison") or {}).get("baseline"),
        status=run.status,
        started_at=_epoch(run.started) or 0.0,
        ended_at=_epoch(run.ended),
        cases=int(ev.get("cases") or 0),
        errored=int(ev.get("errored") or 0),
        cost_usd=round(sum(costs), 10) if costs else None,
        judge_cost_usd=ev.get("judge_cost_usd"),
        p50_ms=ev.get("p50_ms"),
        p95_ms=percentile(ms, 95) if ms else None,
        metrics=dict(ev.get("metrics") or {}),
        gate=dict(gate),
        metadata={
            "dataset_path": dataset,
            "record": str(run.path),
            **{
                k: ev[k]
                for k in ("pass_rate", "passed", "failed", "trials", "threshold", "reliability")
                if k in ev
            },
        },
    )


def item_of(experiment_id: str, item: ItemResult) -> Optional[ExperimentItem]:
    """One recorded item as its row; ``None`` for one with no verdict
    (skipped on resume: it belongs to the run that judged it)."""
    v = item.verdict
    if not v:
        return None
    return ExperimentItem(
        experiment_id=experiment_id,
        case_id=str(v.get("case", item.key)),
        repeat=int(v.get("repeat") or 0),
        case_hash=str(v.get("case_hash") or ""),
        trace_id=item.trace_id,
        status=item.status,
        ms=float(item.ms or 0.0),
        cost_usd=v.get("cost_usd"),
        tokens_in=int(v.get("tokens_in") or 0),
        tokens_out=int(v.get("tokens_out") or 0),
        passed=v.get("passed"),
        tags=list(v.get("tags") or []),
        cluster=v.get("cluster"),
        output=v.get("output"),
        error=item.error or v.get("error"),
    )


def check_score(
    score_name: str,
    check: Mapping[str, Any],
    *,
    source: str,
    evaluator_version: str,
    **ids: Any,
) -> Score:
    """One check's verdict as a score: numeric when it gave a score."""
    numeric = check.get("score") is not None
    return Score(
        score_name=score_name,
        source=source,
        data_type="numeric" if numeric else "bool",
        value=float(check["score"]) if numeric else None,
        passed=check.get("passed"),
        label=check.get("label"),
        reason=str(check.get("reason") or check.get("error") or ""),
        op_id=check.get("op"),
        evaluator_version=evaluator_version,
        judge_trace_id=check.get("judge_trace_id"),
        cost_usd=check.get("cost_usd"),
        metadata={k: v for k, v in check.items() if k not in _SCORE_FIELDS},
        **ids,
    )


def scores_of(run_meta: Mapping[str, Any], experiment: Experiment, item: ItemResult) -> List[Score]:
    """One item's checks as scores. *run_meta* is the record's (or, live,
    the eval's ``describe()`` with its fingerprint): evaluator versions and
    which checks are judges come from it."""
    v = item.verdict
    if not v:
        return []
    versions = ((run_meta.get("eval") or {}).get("fingerprint") or {}).get("evaluators") or {}
    judges = set(run_meta.get("judges") or ())
    return [
        check_score(
            name,
            check,
            source="judge" if name in judges else "code",
            evaluator_version=str(versions.get(name) or ""),
            target="item",
            experiment_id=experiment.experiment_id,
            case_id=str(v.get("case", item.key)),
            repeat=int(v.get("repeat") or 0),
            trace_id=item.trace_id,
            origin="eval",
            name=experiment.eval,
            created_at=experiment.started_at,  # one experiment, one point in time
        )
        for name, check in (v.get("checks") or {}).items()
    ]


def publish(run: Any, store: ScoreStore) -> Dict[str, int]:
    """Write a recorded eval run (a :class:`JobRun` or its directory) to
    *store*: its experiment, items and scores. Safe to repeat."""
    run = run if isinstance(run, JobRun) else JobRun.load(Path(run))
    experiment = experiment_of(run)
    items = [x for x in (item_of(run.run_id, i) for i in run.items) if x is not None]
    scores = [s for i in run.items for s in scores_of(run.meta, experiment, i)]
    store.put_experiment(experiment)
    store.put_items(items)
    store.put_scores(scores)
    return {"experiments": 1, "items": len(items), "scores": len(scores)}


class ScoreWriter:
    """A ScoreStore behind a :class:`BackgroundWriter`: :meth:`submit`
    queues a row and returns; one thread writes batches, retries, and past
    that drops and counts. :meth:`finish` waits up to a timeout, stops,
    and says what was lost."""

    def __init__(self, store: ScoreStore, name: str):
        self.store = store
        self.writer = BackgroundWriter(
            self._write, name=f"scores:{name}", max_queue=100_000, batch_size=500, flush_interval=0.2
        )

    def submit(self, rows: Iterable[Any]) -> None:
        for row in rows:
            self.writer.submit(row)

    def _write(self, rows: Sequence[Any]) -> None:
        items = [r for r in rows if isinstance(r, ExperimentItem)]
        scores = [r for r in rows if isinstance(r, Score)]
        for r in rows:
            if isinstance(r, Experiment):
                self.store.put_experiment(r)
        if items:
            self.store.put_items(items)
        if scores:
            self.store.put_scores(scores)

    def finish(self, timeout: float) -> Tuple[int, int]:
        """Wait up to *timeout* seconds, stop; ``(written, lost)`` rows."""
        self.writer.flush(timeout)
        self.writer.close(timeout=0)
        stats = self.writer.stats
        return stats["written"], stats["submitted"] - stats["written"]


def warn_lost(eval_name: str, lost: int, run: JobRun) -> None:
    LOGGER.warning(
        f"[eval:{eval_name}] the score store did not take {lost} row(s) of experiment "
        f"{run.run_id} (down, or slower than scores_timeout). No verdict is lost: the run's "
        f"record at {run.path} has every one; publish(run, store) sends them when it is back"
    )
