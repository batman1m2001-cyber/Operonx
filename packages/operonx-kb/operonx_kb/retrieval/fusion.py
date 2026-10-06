"""Rank fusion (track5 §9.4): pure functions over hit lists.

Reciprocal rank fusion scores a chunk ``Σ 1 / (rrf_k + rank)`` over the lists
it appears in. It uses ranks only, so a cosine similarity and a BM25 score
never need calibrating against each other, and ``rrf_k = 60`` is the value of
the original paper (Cormack et al., 2009) that every hybrid system since uses.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence

__all__ = ["rrf_fuse"]


def rrf_fuse(
    lists: Sequence[Sequence[Dict[str, Any]]],
    rrf_k: int = 60,
    weights: Optional[Sequence[float]] = None,
) -> List[Dict[str, Any]]:
    """Fuse hit lists (each best first) into one, best first.

    A fused hit keeps every source score in ``scores``; its ``score`` is the
    fused one and ``retriever`` is ``"hybrid"``. Ties break on the best single
    rank, then the chunk id, so the result does not depend on the order of
    ``lists``.
    """
    weights = list(weights) if weights is not None else [1.0] * len(lists)
    if len(weights) != len(lists):
        raise ValueError(f"{len(weights)} weights for {len(lists)} lists")
    fused: Dict[str, float] = {}
    best: Dict[str, int] = {}
    scores: Dict[str, Dict[str, float]] = {}
    for hits, weight in zip(lists, weights):
        for rank, hit in enumerate(hits, start=1):
            cid = hit["chunk_id"]
            fused[cid] = fused.get(cid, 0.0) + weight / (rrf_k + rank)
            best[cid] = min(best.get(cid, rank), rank)
            scores.setdefault(cid, {}).update(hit.get("scores") or {hit["retriever"]: hit["score"]})
    order = sorted(fused, key=lambda c: (-fused[c], best[c], c))
    return [
        {
            "chunk_id": c,
            "score": fused[c],
            "rank": i + 1,
            "retriever": "hybrid",
            "scores": scores[c],
        }
        for i, c in enumerate(order)
    ]
