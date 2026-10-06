"""The KB's metrics as operonx ``Eval`` evaluators (PLAN R8).

Each factory returns a function the operonx 1.14 ``Eval`` calls per case with
``input``, ``output`` and ``expected``, returning ``{passed, score, reason}``.
Retrieval evaluators read a search flow's output (``{"hits", "stats"}``),
answer evaluators an answer flow's output (the checked answer). Labels are
resolved through a :class:`~operonx_kb.eval.labels.LabelResolver` shared by the
evaluators of one run.

A dataset row::

    {"id": "cvi-005-taxi",
     "input": {"query": "…", "collection": "corpus_vi", "k": 20},
     "expected": {"relevant": [{"doc_key": "doc_005.md", "quote": "…"}], "answer": "…"},
     "tags": ["vi"]}
"""

from __future__ import annotations

from statistics import mean
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence

from operonx_kb.eval import metrics
from operonx_kb.eval.labels import LabelResolver

__all__ = [
    "recall_at",
    "mrr",
    "ndcg_at",
    "citation_precision",
    "faithfulness",
    "grounded_recall",
    "retrieval_evaluators",
    "answer_evaluators",
    "score_metrics",
    "compare_metric",
]


def _named(fn: Callable, name: str) -> Callable:
    fn.eval_name = name
    return fn


def _labels(resolver: LabelResolver, input: Mapping[str, Any], expected: Mapping[str, Any]):
    return resolver.resolve(input["collection"], (expected or {}).get("relevant") or [])


def recall_at(k: int, resolver: LabelResolver, pass_at: float = 0.5) -> Callable:
    """Recall@k of a search output."""

    def evaluate(input: Any = None, output: Any = None, expected: Any = None) -> Dict[str, Any]:  # noqa: A002
        score = metrics.recall_at(
            (output or {}).get("hits") or [], _labels(resolver, input, expected), k
        )
        return {"passed": score >= pass_at, "score": score}

    return _named(evaluate, f"recall@{k}")


def mrr(resolver: LabelResolver, pass_at: float = 0.5) -> Callable:
    """Reciprocal rank of the first relevant hit of a search output."""

    def evaluate(input: Any = None, output: Any = None, expected: Any = None) -> Dict[str, Any]:  # noqa: A002
        score = metrics.reciprocal_rank(
            (output or {}).get("hits") or [], _labels(resolver, input, expected)
        )
        return {"passed": score >= pass_at, "score": score}

    return _named(evaluate, "mrr")


def ndcg_at(k: int, resolver: LabelResolver, pass_at: float = 0.5) -> Callable:
    """nDCG@k of a search output (binary gains, each label counted once)."""

    def evaluate(input: Any = None, output: Any = None, expected: Any = None) -> Dict[str, Any]:  # noqa: A002
        score = metrics.ndcg_at(
            (output or {}).get("hits") or [], _labels(resolver, input, expected), k
        )
        return {"passed": score >= pass_at, "score": score}

    return _named(evaluate, f"ndcg@{k}")


def citation_precision(pass_at: float = 0.9) -> Callable:
    """Verified citations over all citations of an answer. An answer citing nothing has
    no precision (no score: it is left out of the mean); its sentences count against the
    faithfulness proxy instead."""

    def evaluate(output: Any = None) -> Dict[str, Any]:
        score = metrics.citation_precision(output or {})
        if score is None:
            return {"passed": False, "reason": "the answer cites nothing"}
        return {"passed": score >= pass_at, "score": score}

    return _named(evaluate, "citation_precision")


def faithfulness(pass_at: float = 0.5) -> Callable:
    """The faithfulness proxy of an answer (:func:`~operonx_kb.eval.metrics.faithfulness_proxy`)."""

    def evaluate(output: Any = None) -> Dict[str, Any]:
        score = metrics.faithfulness_proxy(output or {})
        return {"passed": score >= pass_at, "score": score}

    return _named(evaluate, "faithfulness")


def grounded_recall(resolver: LabelResolver, pass_at: float = 0.5) -> Callable:
    """The share of a case's labels the answer's verified citations cover."""

    def evaluate(input: Any = None, output: Any = None, expected: Any = None) -> Dict[str, Any]:  # noqa: A002
        score = metrics.grounded_recall(output or {}, _labels(resolver, input, expected))
        return {"passed": score >= pass_at, "score": score}

    return _named(evaluate, "grounded_recall")


def retrieval_evaluators(
    resolver: LabelResolver, ks: Sequence[int] = (5, 10, 20), ndcg: int = 10
) -> List[Callable]:
    """Recall@k for each ``k``, MRR and nDCG@``ndcg``."""
    return [*(recall_at(k, resolver) for k in ks), mrr(resolver), ndcg_at(ndcg, resolver)]


def answer_evaluators(resolver: LabelResolver) -> List[Callable]:
    """Citation precision, the faithfulness proxy and grounded recall."""
    return [citation_precision(), faithfulness(), grounded_recall(resolver)]


def score_metrics(run: Any) -> Dict[str, Any]:
    """Each evaluator's **score** over a finished eval run, with its 95% interval.

    operonx's ``Eval`` summarises each check as the share of trials that *passed*
    (``run.meta["eval"]["metrics"]``, kept under ``summary``). A KB check carries a
    graded score (MRR, nDCG, a citation share), so here each case's score is
    averaged over its repeats and the mean over cases is estimated with operonx's
    own statistics (:func:`operonx.app.evals.stats.estimate`: Wilson for 0/1 scores,
    the CLT otherwise).

    Returns:
        ``metrics`` (name → ``{n, mean, se, ci_lo, ci_hi, method}``), ``means``
        (name → mean, for tables), ``per_case`` (name → case id → score), ``cases``,
        ``errors``, and the item latency percentiles ``p50_ms``/``p95_ms``.
    """
    from operonx.app.evals.stats import estimate

    trials: Dict[str, Dict[str, List[float]]] = {}
    errors: List[str] = []
    cases = set()
    for item in run.items:
        case = getattr(item, "case", None) or item.key.rsplit("#", 1)[0]
        cases.add(case)
        verdict: Optional[Mapping[str, Any]] = item.verdict
        if not verdict or verdict.get("error"):
            errors.append(str((verdict or {}).get("error") or item.status))
            continue
        for name, check in verdict["checks"].items():
            if check.get("error"):
                errors.append(f"{name}: {check['error']}")
            elif check.get("score") is not None:
                trials.setdefault(name, {}).setdefault(case, []).append(float(check["score"]))
    per_case = {n: {c: mean(v) for c, v in by.items()} for n, by in trials.items()}
    metrics = {
        n: estimate(list(v.values()), bounds=(0.0, 1.0)).as_dict(4) for n, v in per_case.items()
    }
    ms = sorted(i.ms for i in run.items if i.ms)
    return {
        "cases": len(cases),
        "errors": errors,
        "metrics": metrics,
        "means": {n: m["mean"] for n, m in metrics.items()},
        "per_case": per_case,
        "p50_ms": ms[len(ms) // 2] if ms else None,
        "p95_ms": ms[min(len(ms) - 1, int(0.95 * len(ms)))] if ms else None,
    }


def compare_metric(
    base: Mapping[str, Any], candidate: Mapping[str, Any], metric: str
) -> Dict[str, Any]:
    """``candidate`` against ``base`` on one metric, paired over the cases both scored,
    with operonx's paired test (:func:`operonx.app.evals.stats.compare_paired`: exact
    McNemar and Newcombe's interval for 0/1 scores, a seeded paired bootstrap otherwise)."""
    from operonx.app.evals.stats import compare_paired

    a, b = base["per_case"][metric], candidate["per_case"][metric]
    shared = sorted(set(a) & set(b))
    return compare_paired([a[c] for c in shared], [b[c] for c in shared]).as_dict(4)
