"""Retrieval ops: the logic of :mod:`operonx_kb.graphs.retrieve` (track5 §9, PLAN R4/R5).

A retriever answers ``(query, collection, filter, k)`` with **hits**: chunk ids
and scores, nothing else. Index keys become chunk ids through the catalog's
ledger, restricted to the collection, so a vector store or lexical table
shared by several collections never hands one another's chunks. Text,
documents and provenance come later, in :func:`hydrate`, the consistency gate:
a hit whose chunk is gone, superseded, tombstoned, or no longer passes the
filter against the catalog is dropped there, and the drop is counted.

A hit is a JSON dict (it is traced)::

    {"chunk_id": "ch_…", "score": 0.83, "rank": 1, "retriever": "dense",
     "scores": {"dense": 0.83}}

and a hydrated hit adds ``document_id``, ``key``, ``title``, ``version_id``,
``ordinal``, ``text``, ``heading_path``, ``pages`` and ``spans``.
"""

from __future__ import annotations

import math
from typing import Any, Dict, List, Optional

from operonx import op

from operonx_kb.errors import CatalogError
from operonx_kb.model.collection import CollectionSpec, LexicalIndexSpec
from operonx_kb.model.filter import KBFilter, document_payload, matches
from operonx_kb.ops._resources import catalog_of, full_key, lexical_of, vector_store_of
from operonx_kb.retrieval.filters import PostFilter, native_filter
from operonx_kb.retrieval.fusion import rrf_fuse
from operonx_kb.text.analyze import Analyzer

__all__ = [
    "plan_dense",
    "dense_hits",
    "lexical_search",
    "fusion_depth",
    "rrf",
    "hydrate",
    "rerank_depth",
    "apply_rerank",
]


def _spec(catalog: str, collection: str) -> CollectionSpec:
    coll = catalog_of(catalog).get_collection(collection)
    if coll is None:
        raise CatalogError(f"no collection {collection!r}; create it with create_collection()")
    return coll.spec


def _hits(
    keys: List[int], scores: List[float], chunk_of: Dict[int, str], retriever: str
) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    seen = set()
    for key, score in zip(keys, scores):
        cid = chunk_of.get(int(key))
        if cid is None or cid in seen:
            continue
        seen.add(cid)
        out.append({"chunk_id": cid, "score": float(score), "rank": len(out) + 1,
                    "retriever": retriever, "scores": {retriever: float(score)}})  # fmt: skip
    return out


@op(bound="cpu", show_keys="plan")
def plan_dense(
    query: str,
    collection: str,
    k: int,
    store: str,
    template: str,
    catalog: str,
    filter: Optional[dict] = None,
    overfetch: float = 1.5,
    post_filter_overfetch: int = 10,
) -> dict:
    """Compile the filter for the vector store and size the search.

    Under a native filter the store returns only matching entries and the
    over-fetch (``ceil(overfetch × k)``) covers what the catalog gate drops.
    FAISS has no payload, so its hits are filtered against the catalog after
    the search and ``post_filter_overfetch × k`` are fetched.
    """
    checked = KBFilter.of(filter).checked(_spec(catalog, collection))
    native = native_filter(vector_store_of(store), checked, collection)
    post = isinstance(native, PostFilter)
    fetch_k = k * post_filter_overfetch if post else math.ceil(k * overfetch)
    return {
        "texts": [template.replace("{text}", query)],
        "fetch_k": max(int(fetch_k), 1),
        "native": None if post else native,
        "plan": {"fetch_k": int(fetch_k), "post_filter": post, "k": k},
    }


@op(bound="cpu", exclude={"trace": ["ids", "scores"]}, show_keys="stats")
def dense_hits(
    ids: list,
    scores: list,
    plan: dict,
    collection: str,
    store: str,
    vcollection: str,
    catalog: str,
    filter: Optional[dict] = None,
) -> dict:
    """Vector keys to chunk hits of this collection; with a post-filter (FAISS), only
    chunks whose document passes the filter in the catalog. At most ``k`` hits."""
    cat = catalog_of(catalog)
    chunk_of = cat.chunks_for_keys(full_key(store, "vector_store"), vcollection, collection, ids)
    hits = _hits(ids, scores, chunk_of, "dense")
    foreign = len(ids) - len(chunk_of)
    dropped = 0
    if plan["post_filter"]:
        spec = _spec(catalog, collection)
        checked = KBFilter.of(filter).checked(spec)
        active = cat.active_chunks(collection, [h["chunk_id"] for h in hits])
        kept = [
            h
            for h in hits
            if h["chunk_id"] in active
            and matches(checked, collection, document_payload(spec, active[h["chunk_id"]].document))
        ]
        dropped = len(hits) - len(kept)
        hits = [{**h, "rank": i + 1} for i, h in enumerate(kept)]
    hits = hits[: plan["k"]]
    stats = {"fetched": len(ids), "foreign": foreign, "post_filtered": dropped,
             "returned": len(hits)}  # fmt: skip
    return {"hits": hits, "stats": stats}


@op(bound="cpu", show_keys="stats")
def lexical_search(
    query: str,
    collection: str,
    k: int,
    lexical: dict,
    catalog: str,
    filter: Optional[dict] = None,
    overfetch: float = 1.5,
) -> dict:
    """Analyze the query, search the lexical index under the compiled filter, map keys to chunks."""
    spec = LexicalIndexSpec.model_validate(lexical)
    checked = KBFilter.of(filter).checked(_spec(catalog, collection))
    index = lexical_of(spec.index)
    native = native_filter(index, checked, collection)
    tokens = Analyzer(spec.analyzer).query_tokens(query)
    fetch_k = max(math.ceil(k * overfetch), 1)
    keys, scores = index.search(tokens, top_k=fetch_k, filter=native, collection=spec.collection)
    chunk_of = catalog_of(catalog).chunks_for_keys(
        full_key(spec.index, "kb_lexical"), spec.collection or "", collection, keys
    )
    hits = _hits(keys, scores, chunk_of, "lexical")[:k]
    stats = {"tokens": len(tokens), "fetched": len(keys), "foreign": len(keys) - len(chunk_of),
             "returned": len(hits)}  # fmt: skip
    return {"hits": hits, "stats": stats}


@op(show_keys="depth")
def fusion_depth(k: int, depth: int) -> dict:
    """How many candidates each fused retriever returns: at least ``k``."""
    return {"depth": max(int(k), int(depth))}


@op(show_keys="stats")
def rrf(first: list, second: list, k: int, rrf_k: int = 60) -> dict:
    """Reciprocal rank fusion of two hit lists (k=60, no score calibration; track5 §9.4)."""
    fused = rrf_fuse([first, second], rrf_k=rrf_k)[:k]
    stats = {"first": len(first), "second": len(second),
             "both": len({h["chunk_id"] for h in first} & {h["chunk_id"] for h in second}),
             "returned": len(fused)}  # fmt: skip
    return {"hits": fused, "stats": stats}


@op(bound="cpu", show_keys="stats")
def hydrate(
    hits: list, collection: str, k: int, catalog: str, filter: Optional[dict] = None
) -> dict:
    """The consistency gate (track5 §11.2): keep the hits an active version of a live
    document of the collection holds and whose document passes the filter in the catalog,
    then attach text and provenance. At most ``k`` hits, re-ranked from 1.

    The filter is applied again here, against the store of record, whatever the
    index already did: a payload written for an older version can be stale, and
    a stale payload may cost recall but must never show a document the filter
    excludes.
    """
    spec = _spec(catalog, collection)
    checked = KBFilter.of(filter).checked(spec)
    active = catalog_of(catalog).active_chunks(collection, [h["chunk_id"] for h in hits])
    out: List[Dict[str, Any]] = []
    inactive = filtered = 0
    for h in hits:
        found = active.get(h["chunk_id"])
        if found is None:
            inactive += 1
            continue
        if not matches(checked, collection, document_payload(spec, found.document)):
            filtered += 1
            continue
        if len(out) == k:
            break
        out.append(
            {
                **h,
                "rank": len(out) + 1,
                "document_id": found.document.id,
                "key": found.document.key,
                "title": found.document.title,
                "version_id": found.occurrence.version_id,
                "ordinal": found.occurrence.ordinal,
                "text": found.chunk.text,
                "heading_path": found.chunk.heading_path,
                "pages": found.occurrence.pages,
                "spans": [list(s) for s in found.occurrence.spans],
            }
        )
    stats = {"in": len(hits), "dropped_inactive": inactive, "dropped_filter": filtered,
             "out": len(out)}  # fmt: skip
    documents = [{"content": h["text"], "chunk_id": h["chunk_id"]} for h in out]
    return {"hits": out, "documents": documents, "stats": stats}


@op(show_keys="depth")
def rerank_depth(k: int, depth: int) -> dict:
    """How many hydrated hits the reranker sees: at least ``k``."""
    return {"depth": max(int(k), int(depth))}


@op(show_keys="stats")
def apply_rerank(hits: list, reranks: list, k: int) -> dict:
    """Order hits by the reranker's scores (``RerankOp`` keeps each document's ``chunk_id``)."""
    by_id = {h["chunk_id"]: h for h in hits}
    out: List[Dict[str, Any]] = []
    for r in reranks:
        h = by_id.get(r.get("chunk_id"))
        if h is None or len(out) == k:
            continue
        score = float(r["score"])
        out.append({**h, "rank": len(out) + 1, "score": score,
                    "scores": {**h.get("scores", {}), "rerank": score}})  # fmt: skip
    documents = [{"content": h["text"], "chunk_id": h["chunk_id"]} for h in out]
    stats = {"in": len(hits), "out": len(out)}
    return {"hits": out, "documents": documents, "stats": stats}
