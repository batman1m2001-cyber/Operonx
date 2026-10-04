"""`compare_pairwise` — a model's preference between two experiments' answers.

The statistical :func:`~operonx.app.evals.compare.compare` says whether a
check's pass rate moved; a pairwise judge says, case by case, which of the
two answers is better — for qualities no check pins down::

    a, b = load_experiment(old_id, store=store), load_experiment(new_id, store=store)
    got = await compare_pairwise(a, b, [pairwise("llm:judge", "judges/helpful.md")])
    got["judges"]["pairwise:helpful"]["wins_b"], ...["inconsistency_rate"]

Each case both experiments ran cleanly, with an unchanged ``case_hash``, is
judged once (repeat 0 of each), in both orders at once; the input comes
from the dataset. A judge whose choice follows the position is reported by
its swap-inconsistency rate, a judge of the systems' own model by a
self-preference warning.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from operonx.telemetry.scores import Score, ScoreStore

from .dataset import Dataset
from .experiments import ExperimentData
from .fingerprint import case_hash
from .judges import Judging, PairwiseJudge
from .stats import estimate

__all__ = ["compare_pairwise"]

#: A preference as a number: b's answer won, a tie, a's answer won.
VALUE = {"b": 1.0, "tie": 0.5, "a": 0.0}

#: Above this share of swap-inconsistent cases the result is called position bias.
POSITION_BIAS = 0.2


def _first_trials(exp: ExperimentData) -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    for item in exp.items:
        if int(item.get("repeat") or 0) == 0:
            out[str(item["case"])] = item
    return out


def _clean(item: Dict[str, Any]) -> bool:
    return item.get("status") in ("ok", "empty") and not (
        item.get("error") and not item.get("checks")
    )


async def compare_pairwise(
    a: ExperimentData,
    b: ExperimentData,
    judges: Sequence[PairwiseJudge],
    *,
    dataset: Any = None,
    scores: Optional[ScoreStore] = None,
    trace: Sequence[Any] = (),
    judge_cache: Optional[ScoreStore] = None,
    concurrency: int = 8,
) -> Dict[str, Any]:
    """*b*'s answers against *a*'s, judged by each of *judges*.

    Args:
        dataset: Where the cases' inputs are read (a :class:`Dataset` or a
            path); default *b*'s dataset.
        scores: A score store the preferences go to (target ``pair``).
        trace: Consumers the judges' runs are traced to.
        judge_cache: A score store used as the judge cache.
        concurrency: Judge runs in flight.

    Returns ``{"baseline", "candidate", "cases", "skipped", "judges":
    {name: {wins_a, wins_b, ties, inconsistent, n, inconsistency_rate,
    preference, calls, cached, errors, cost_usd, warnings, cases}}}``.
    """
    for j in judges:
        if not isinstance(j, PairwiseJudge):
            raise TypeError(
                f"compare_pairwise takes pairwise judges (pairwise(...)), not {type(j).__name__}"
            )
    ds = dataset if isinstance(dataset, Dataset) else None
    if ds is None:
        path = dataset or b.summary.get("dataset") or a.summary.get("dataset")
        if not path or not Path(str(path)).is_file():
            raise ValueError(
                f"compare_pairwise: the cases' inputs are read from the dataset, and "
                f"{path or 'no dataset path'} is not a file; pass dataset="
            )
        ds = Dataset(Path(str(path)))
    rows = {str(r["id"]): r for r in ds.rows()}

    left, right = _first_trials(a), _first_trials(b)
    pairs, skipped = [], {}
    for case in sorted(set(left) & set(right)):
        x, y, row = left[case], right[case], rows.get(case)
        why = None
        if row is None:
            why = "not in the dataset"
        elif x.get("case_hash") != y.get("case_hash") or (
            x.get("case_hash") and x["case_hash"] != case_hash(row)
        ):
            why = "the case changed between the experiments"
        elif not (_clean(x) and _clean(y)):
            why = "a run errored"
        elif x.get("output_clipped") or y.get("output_clipped"):
            why = "an output was clipped in the record"
        if why:
            skipped[case] = why
        else:
            pairs.append((case, row, x, y))

    meta = {"job": b.eval, "job_run": b.experiment_id, "baseline": a.experiment_id}
    judging = Judging(trace=trace, metadata=meta, cache=judge_cache, concurrency=concurrency)
    models_a = a.fingerprint.get("models")
    models_b = b.fingerprint.get("models")
    out: Dict[str, Any] = {
        "baseline": a.experiment_id,
        "candidate": b.experiment_id,
        "cases": len(pairs),
        "skipped": skipped,
        "judges": {},
    }
    found: List[Score] = []
    for j in judges:

        async def one(case: str, row: Dict[str, Any], x: Dict[str, Any], y: Dict[str, Any]):
            v = await j.compare(
                input=row.get("input"),
                a=x.get("output"),
                b=y.get("output"),
                expected=row.get("expected"),
                judging=judging.for_case(case=case, judged_trace=y.get("trace_id")),
            )
            return case, v

        got = await asyncio.gather(*(one(*p) for p in pairs))
        out["judges"][j.eval_name] = block = _summary(j, got, models_a, models_b)
        found += [
            _score(j, a, b, case, v)
            for case, v in got
            if not v.get("error") and v.get("winner") is not None
        ]
    if scores is not None and found:
        await asyncio.to_thread(scores.put_scores, found)
    return out


def _summary(j: PairwiseJudge, got, models_a, models_b) -> Dict[str, Any]:
    decided = [(c, v) for c, v in got if not v.get("error") and v.get("winner")]
    wins = {k: sum(1 for _, v in decided if v["winner"] == k) for k in ("a", "b", "tie")}
    inconsistent = sum(1 for _, v in decided if v.get("inconsistent"))
    n = len(decided)
    costs = [v["cost_usd"] for _, v in got if v.get("cost_usd") is not None]
    warnings: List[str] = []
    rate = inconsistent / n if n else None
    if rate is not None and rate > POSITION_BIAS:
        warnings.append(
            f"{j.eval_name}: {inconsistent} of {n} cases flipped with the order "
            f"({rate:.0%}): its choice follows the position more than the answers — those "
            "cases count as ties; read its preference with care"
        )
    model = (j.models() or [None])[0]
    if models_a is None or models_b is None:
        warnings.append(
            f"{j.eval_name}: an experiment from before E5 does not say which models it ran, "
            "so self-preference was not checked"
        )
    elif model and model in set(models_a) | set(models_b):
        warnings.append(
            f"{j.eval_name}: the judge runs on {model!r}, a model the compared systems also use "
            "(self-preference: a model tends to favour its own answers) — judge with another model"
        )
    errors = [v["error"] for _, v in got if v.get("error")]
    if errors:
        warnings.append(f"{j.eval_name}: {len(errors)} case(s) not judged — {errors[0]}")
    pref = estimate([VALUE[v["winner"]] for _, v in decided], bounds=(0.0, 1.0)) if n else None
    return {
        "version": j.eval_version,
        "model": model,
        "n": n,
        "wins_a": wins["a"],
        "wins_b": wins["b"],
        "ties": wins["tie"],
        "inconsistent": inconsistent,
        "inconsistency_rate": round(rate, 6) if rate is not None else None,
        "preference": pref.as_dict() if pref is not None else None,
        "calls": sum(1 for _, v in got if v.get("judge_trace_id") and not v.get("cached")),
        "cached": sum(1 for _, v in got if v.get("cached")),
        "errors": len(errors),
        "cost_usd": round(sum(costs), 10) if costs else None,
        "warnings": warnings,
        "cases": [{"case": c, **v} for c, v in got],
    }


def _score(j: PairwiseJudge, a: ExperimentData, b: ExperimentData, case: str, v) -> Score:
    return Score(
        score_name=j.eval_name,
        target="pair",
        source="judge",
        data_type="categorical",
        value=VALUE[v["winner"]],
        label=v["winner"],
        reason=str(v.get("reason") or ""),
        experiment_id=a.experiment_id,
        pair_experiment_id=b.experiment_id,
        case_id=case,
        origin="eval",
        name=b.eval,
        evaluator_version=j.eval_version,
        judge_trace_id=v.get("judge_trace_id"),
        cost_usd=v.get("cost_usd"),
        metadata={"inconsistent": v.get("inconsistent"), "ab": v.get("ab"), "ba": v.get("ba")},
    )
