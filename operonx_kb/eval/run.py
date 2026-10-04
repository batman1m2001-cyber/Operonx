"""Run an operonx ``Eval`` of a collection's search or answers over a dataset file."""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any, Dict, Optional, Sequence, Union

from operonx.app.evals import Eval, Gate

from operonx_kb.eval.evaluators import answer_evaluators, retrieval_evaluators, score_metrics
from operonx_kb.eval.labels import LabelResolver
from operonx_kb.graphs.answer import build_answer_flow
from operonx_kb.graphs.retrieve import build_search_flow

__all__ = ["evaluate_search", "evaluate_answers"]


async def evaluate_search(
    kb: Any,
    collection: str,
    dataset: Union[str, Path],
    *,
    mode: Optional[str] = None,
    reranker: Optional[str] = None,
    rerank_depth: int = 30,
    ks: Sequence[int] = (5, 10, 20),
    repeats: int = 1,
    gate: Optional[Gate] = None,
    record_dir: Optional[Union[str, Path]] = None,
    trace: Any = (),
) -> Dict[str, Any]:
    """Evaluate ``kb``'s search of ``collection`` (one mode, optionally reranked) on a dataset.

    Args:
        repeats: Runs per case (search is deterministic; answers are not).
        gate: An operonx ``Gate`` (threshold, baseline, tolerance) for the run.
        record_dir: Where the eval's record goes (default: a temporary folder), so
            ``operonx eval compare`` and ``load_experiment`` can read it later.

    Returns:
        :func:`~operonx_kb.eval.evaluators.score_metrics` of the run, plus ``mode``,
        ``reranker``, ``run_id`` and the eval's own ``summary`` (pass shares with
        intervals, the gate's verdict, the fingerprint).
    """
    flow = build_search_flow(kb.search_graph(collection, mode, reranker, rerank_depth), k=max(ks))
    resolver = LabelResolver(kb.catalog_key, kb.blobs_key)
    name = f"search_{collection}_{mode or 'default'}{'_rerank' if reranker else ''}"
    with tempfile.TemporaryDirectory() as tmp:
        ev = Eval(name, graph=flow, dataset=Path(dataset), evaluators=retrieval_evaluators(resolver, ks),
                  repeats=repeats, gate=gate, record_dir=record_dir or tmp, trace=list(trace),
                  concurrency=1)  # fmt: skip
        run = await ev.run()
    report = score_metrics(run)
    report.update(mode=mode, reranker=reranker, run_id=run.run_id, summary=run.meta.get("eval"))
    return report


async def evaluate_answers(
    kb: Any,
    collection: str,
    dataset: Union[str, Path],
    llm: str,
    *,
    mode: Optional[str] = None,
    reranker: Optional[str] = None,
    k: int = 8,
    repeats: int = 1,
    gate: Optional[Gate] = None,
    record_dir: Optional[Union[str, Path]] = None,
    trace: Any = (),
) -> Dict[str, Any]:
    """Evaluate ``kb``'s answers (citation precision, faithfulness proxy, grounded recall).

    Returns:
        :func:`~operonx_kb.eval.evaluators.score_metrics` of the run (with ``repeats``,
        each case's scores averaged over its answers), plus ``run_id`` and the eval's own
        ``summary``.
    """
    flow = build_answer_flow(kb.answer_graph(collection, llm, mode=mode, reranker=reranker), k=k)
    resolver = LabelResolver(kb.catalog_key, kb.blobs_key)
    with tempfile.TemporaryDirectory() as tmp:
        ev = Eval(f"answer_{collection}", graph=flow, dataset=Path(dataset),
                  evaluators=answer_evaluators(resolver), repeats=repeats, gate=gate,
                  record_dir=record_dir or tmp, trace=list(trace), concurrency=1)  # fmt: skip
        run = await ev.run()
    report = score_metrics(run)
    report.update(run_id=run.run_id, summary=run.meta.get("eval"))
    return report
