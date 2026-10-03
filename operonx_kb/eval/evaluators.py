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
    "metric_means",
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
    """Verified citations over all citations of an answer; an answer citing nothing scores 0."""

    def evaluate(output: Any = None) -> Dict[str, Any]:
        score = metrics.citation_precision(output or {})
        if score is None:
            return {"passed": False, "score": 0.0, "reason": "the answer cites nothing"}
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


def metric_means(run: Any) -> Dict[str, Any]:
    """Per evaluator, the mean score over a finished eval run's cases; plus case counts,
    errors and latency percentiles (ms) from the item records.

    The 1.14 ``Eval`` summary counts passes per check; the scores are on each item's
    verdict, and this averages them.
    """
    scores: Dict[str, List[float]] = {}
    errors: List[str] = []
    for item in run.items:
        verdict: Optional[Mapping[str, Any]] = item.verdict
        if not verdict or verdict.get("error"):
            errors.append(str((verdict or {}).get("error") or item.status))
            continue
        for name, check in verdict["checks"].items():
            if check.get("error"):
                errors.append(f"{name}: {check['error']}")
            elif check.get("score") is not None:
                scores.setdefault(name, []).append(float(check["score"]))
    ms = sorted(i.ms for i in run.items if i.ms)
    return {
        "cases": len(run.items),
        "errors": errors,
        "metrics": {name: round(mean(v), 4) for name, v in scores.items()},
        "per_case": {name: v for name, v in scores.items()},
        "p50_ms": ms[len(ms) // 2] if ms else None,
        "p95_ms": ms[min(len(ms) - 1, int(0.95 * len(ms)))] if ms else None,
    }
