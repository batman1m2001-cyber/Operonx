"""Concept graph ops: what :func:`operonx_kb.graphs.retrieve.graph_retrieve` and the
ingest graph run (PLAN G1-G5).

- :func:`graph_concepts` (ingest): each chunk's concepts, committed with the version.
- :func:`graph_hits` (search): a walk over the collection's graph from the seed
  retriever's best hits; chunks by where it ends, then the seed's other hits.
"""

from __future__ import annotations

from typing import Any, List, Optional

from operonx import op

from operonx_kb.enrich.concepts import chunk_concepts
from operonx_kb.model.collection import CollectionSpec, GraphSpec
from operonx_kb.model.filter import KBFilter, document_payload, matches
from operonx_kb.ops._resources import catalog_of, full_key
from operonx_kb.retrieval.graph import graph_for

__all__ = ["graph_concepts", "graph_hits"]


@op(bound="cpu", exclude={"trace": ["chunks", "tree"]}, show_keys="stats")
def graph_concepts(plan: dict, tree: dict, chunks: list) -> dict:
    """``mentions``: ``[chunk_id, concept, weight]`` for each chunk of the version,
    when the collection has a ``graph`` spec (none otherwise). A chunk's headings
    are its heading path under the document's title."""
    spec = CollectionSpec.model_validate(plan["spec"]).graph
    if spec is None:
        return {"mentions": [], "stats": {"chunks": 0, "mentions": 0}}
    title = plan.get("title") or tree.get("title") or ""  # the title commit_version keeps
    mentions: List[List[Any]] = []
    for chunk in chunks:
        headings = (
            [title, *chunk.get("heading_path", [])] if title else chunk.get("heading_path", [])
        )
        for concept, weight in chunk_concepts(chunk["text"], headings, spec.title_weight).items():
            mentions.append([chunk["id"], concept, weight])
    stats = {"chunks": len(chunks), "mentions": len(mentions),
             "concepts": len({m[1] for m in mentions})}  # fmt: skip
    return {"mentions": mentions, "stats": stats}


def _allowed_documents(cat, collection: str, filter: Optional[dict]) -> Optional[set]:
    """The documents a filtered search may walk through (``None``: no filter)."""
    flt = KBFilter.of(filter)
    if flt == KBFilter():
        return None
    coll = cat.get_collection(collection)
    checked = flt.checked(coll.spec)
    return {
        d.id
        for d in cat.list_documents(collection)
        if matches(checked, collection, document_payload(coll.spec, d))
    }


@op(bound="cpu", show_keys="stats")
def graph_hits(
    seed: list, collection: str, k: int, graph: dict, catalog: str, filter: Optional[dict] = None
) -> dict:
    """The seed's first ``graph.seeds`` hits (weighted 1/rank) start a personalized
    PageRank walk; the seeds and the first ``graph.expand`` other chunks it reaches
    come first, by the mass it leaves on them, then the seed's other hits in order,
    ``k`` in all."""
    spec = GraphSpec.model_validate(graph)
    cat = catalog_of(catalog)
    g = graph_for(cat, full_key(catalog, "kb_catalog"), collection,
                  max_df_share=spec.max_df_share, max_df_min=spec.max_df_min)  # fmt: skip
    seeds = {h["chunk_id"]: 1.0 / (i + 1) for i, h in enumerate(seed[: spec.seeds])}
    walked = g.rank(seeds, alpha=spec.alpha, iterations=spec.iterations,
                    documents=_allowed_documents(cat, collection, filter))  # fmt: skip
    seed_scores = {h["chunk_id"]: h.get("scores") or {} for h in seed}
    mass = dict(walked)
    brought = [c for c, _ in walked if c not in seeds][: spec.expand]
    head = [c for c, _ in walked if c in seeds or c in brought]
    order = head + [h["chunk_id"] for h in seed if h["chunk_id"] not in head]
    hits = [
        {"chunk_id": c, "score": mass.get(c, 0.0), "rank": i + 1, "retriever": "graph",
         "scores": {**seed_scores.get(c, {}), "graph": mass.get(c, 0.0)}}
        for i, c in enumerate(order[:k])
    ]  # fmt: skip
    stats = {"seeds": len(seeds), "walked": len(walked), "brought": len(brought),
             "returned": len(hits), "graph_chunks": len(g.chunk_ids),
             "graph_concepts": g.concepts, "graph_edges": g.edges}  # fmt: skip
    return {"hits": hits, "stats": stats}
