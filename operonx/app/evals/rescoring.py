"""`rescore` — judge a recorded eval run again, without running its graph.

A new or edited deterministic check, run over what an experiment already
produced: each item's recorded output, its case from the dataset, and —
for a check that asks for ``trace`` — its run read back from the run
store the eval traced into::

    again = await rescore(run, [trajectory.ops(mode="strict")], store=runs)
    again.summary["pass_rate"], again.verdicts["refund-1"]

Nothing runs the graph and nothing is written to the job record. What
cannot be judged again is said on the item, never guessed: a case whose
``case_hash`` changed since the run, an output the record holds only as a
clipped preview, a run the store no longer has. Judges (``llm_judge``)
are refused: they are not deterministic, and re-asking a model is a new
experiment, not a rescore.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Union

from ..jobs.record import ITEM_EMPTY, ITEM_OK, JobRun
from .dataset import Dataset
from .evaluators import judge_all, prepare
from .fingerprint import case_hash
from .job import case_verdict, numbers, trial_of
from .traceview import TraceView

__all__ = ["Rescored", "is_judge", "rescore"]


@dataclass
class Rescored:
    """A rescore's outcome: the run it judged again, each item's new
    verdict (by item key), the summary numbers ``run.json["eval"]`` would
    hold for them, and the evaluators left out (an eval's judges)."""

    run_id: str
    eval: str
    verdicts: Dict[str, Dict[str, Any]]
    summary: Dict[str, Any]
    skipped: List[str] = field(default_factory=list)


def is_judge(ev: Any) -> bool:
    """An evaluator that asks a model (``eval_kind = "judge"``)."""
    return getattr(ev, "eval_kind", None) == "judge"


def _load(run: Union[JobRun, str, Path]) -> JobRun:
    found = run if isinstance(run, JobRun) else JobRun.load(Path(run))
    if not isinstance(found.meta.get("eval"), Mapping):
        raise ValueError(
            f"run {found.run_id} of {found.job!r} is not an eval run (its run.json has no "
            "'eval' block): rescore judges what an Eval recorded"
        )
    return found


def _rows(run: JobRun, dataset: Any) -> Dict[str, Dict[str, Any]]:
    if dataset is None:
        dataset = run.meta["eval"].get("dataset")
    ds = dataset if isinstance(dataset, Dataset) else Dataset(Path(str(dataset)))
    if not ds.path.is_file():
        raise ValueError(
            f"rescore of {run.job!r} {run.run_id}: its dataset {str(ds.path)!r} is not there; "
            "pass dataset= (the same cases — an edited case is reported, not rescored)"
        )
    return {str(r["id"]): r for r in ds.rows()}


def _outputs(output: Any, sent: int) -> List[Any]:
    """What the case sent, from the one value the record keeps."""
    if not sent:
        return []
    return [output] if sent == 1 else list(output or [])


async def rescore(
    run: Union[JobRun, str, Path],
    evaluators: Sequence[Any],
    *,
    store: Any = None,
    dataset: Any = None,
    scores: Any = None,
) -> Rescored:
    """Judge *run* (a :class:`JobRun`, or its directory) again with
    *evaluators*. ``store`` is the run store its traces went to — needed
    when an evaluator reads ``trace``; ``dataset`` overrides the dataset
    path the run recorded; ``scores`` (a ScoreStore) receives the new
    scores, one per check per judged run, ids by trace and evaluator
    version — beside the experiment's own, never over them."""
    found = _load(run)
    prepared = [prepare(ev) for ev in evaluators]
    judges = [p.name for p in prepared if is_judge(p.ev)]
    if judges:
        raise ValueError(
            f"rescore re-runs deterministic evaluators; {judges} ask a model. "
            "Run the eval again to judge with them"
        )
    wants_trace = any(p.wants("trace") for p in prepared)
    if wants_trace and store is None:
        names = [p.name for p in prepared if p.wants("trace")]
        raise ValueError(
            f"{names} read the case's trace: pass store= (the run store the eval traced "
            "into, e.g. open_run_store({'backend': 'files'}))"
        )
    rows = _rows(found, dataset)
    ev_meta = found.meta["eval"]

    verdicts: Dict[str, Dict[str, Any]] = {}
    trials = []
    for item in found.items:
        old = item.verdict
        if not old:
            continue  # skipped on resume: judged in the run it came from
        verdict = dict(old)
        if item.status in (ITEM_OK, ITEM_EMPTY):
            for key in ("passed", "checks", "judge_cost_usd", "error"):
                verdict.pop(key, None)
            verdict.update(await _again(item, old, rows, prepared, store if wants_trace else None))
        verdicts[item.key] = verdict
        trials.append(trial_of(item.key, verdict, item.ms))
    nums, _, _ = numbers(trials, int(ev_meta.get("repeats") or 1))
    if scores is not None:
        rows = _scores(found, verdicts, prepared)
        await asyncio.to_thread(scores.put_scores, rows)
    return Rescored(found.run_id, found.job, verdicts, nums)


def _scores(run: JobRun, verdicts: Mapping[str, Dict[str, Any]], prepared: Sequence[Any]) -> List[Any]:
    """The rescore's checks as scores on the runs they judged."""
    from .fingerprint import evaluator_version
    from .publish import check_score

    versions = {p.name: evaluator_version(p.ev) for p in prepared}
    rows = []
    for item in run.items:
        verdict = verdicts.get(item.key)
        if not verdict or not item.trace_id:
            continue
        for name, check in (verdict.get("checks") or {}).items():
            if name not in versions:
                continue  # a check the recorded verdict kept, not one this rescore ran
            rows.append(
                check_score(
                    name,
                    check,
                    source="code",
                    evaluator_version=versions[name],
                    target="op" if check.get("op") else "trace",
                    trace_id=item.trace_id,
                    experiment_id=run.run_id,
                    case_id=str(verdict.get("case", item.key)),
                    repeat=int(verdict.get("repeat") or 0),
                    origin="eval",
                    name=run.job,
                )
            )
    return rows


async def _again(
    item: Any,
    old: Mapping[str, Any],
    rows: Mapping[str, Dict[str, Any]],
    prepared: Sequence[Any],
    store: Any,
) -> Dict[str, Any]:
    """One item's new ``passed``/``checks`` (or the error that stops it)."""
    case = str(old.get("case", item.key))
    row = rows.get(case)
    if row is None:
        return {"passed": False, "checks": {}, "error": f"case {case!r} is no longer in the dataset"}
    if old.get("case_hash") and case_hash(row) != old["case_hash"]:
        return {
            "passed": False,
            "checks": {},
            "error": f"case {case!r} changed since the run (case_hash {old['case_hash']} → "
            f"{case_hash(row)}): run the eval again to judge it",
        }
    if old.get("output_clipped"):
        return {
            "passed": False,
            "checks": {},
            "error": "the record holds only a clipped preview of this output; "
            "run the eval again to judge it",
        }
    output = old.get("output")
    avail: Dict[str, Any] = {
        "input": row.get("input"),
        "output": output,
        "expected": row.get("expected"),
        "row": row,
        "outputs": _outputs(output, item.sent),
    }
    if store is not None:
        try:
            avail["trace"] = await asyncio.to_thread(TraceView.from_store, store, item.trace_id)
        except LookupError as exc:
            return {"passed": False, "checks": {}, "error": str(exc)}
    return case_verdict(await judge_all(prepared, avail))
