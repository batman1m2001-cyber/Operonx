"""Evaluation: quote-anchored datasets, retrieval and answer metrics as operonx evaluators,
and a runner that evaluates one collection in one retrieval mode (track5 §15.3).

    from operonx_kb.eval import evaluate_search
    report = await evaluate_search(kb, "corpus_vi", "datasets/corpus_vi.jsonl", mode="hybrid")
    report["metrics"]["recall@10"]

Any corpus plugs in as documents ingested into a collection plus a dataset file;
nothing here knows which corpus it is.
"""

from operonx_kb.eval.evaluators import (
    answer_evaluators,
    citation_precision,
    faithfulness,
    grounded_recall,
    metric_means,
    mrr,
    ndcg_at,
    recall_at,
    retrieval_evaluators,
)
from operonx_kb.eval.labels import LabelError, LabelResolver, occurrences
from operonx_kb.eval.run import evaluate_answers, evaluate_search

__all__ = [
    "LabelError",
    "LabelResolver",
    "answer_evaluators",
    "citation_precision",
    "evaluate_answers",
    "evaluate_search",
    "faithfulness",
    "grounded_recall",
    "metric_means",
    "mrr",
    "ndcg_at",
    "occurrences",
    "recall_at",
    "retrieval_evaluators",
]
