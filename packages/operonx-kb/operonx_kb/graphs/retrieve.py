"""Retrieval graphs (wiring only; the logic is in :mod:`operonx_kb.ops.retrieve`).

Every graph is defined once, at module level (operonx guide 05). A retriever
takes ``(query, collection, filter, k, catalog)`` and returns ``hits`` (chunk
ids and scores; track5 §9.1); what differs per collection — its embedder,
vector store, lexical index, tree and graph specs — is read from the catalog
by :func:`~operonx_kb.ops.settings.search_settings` when the run starts. The
retrieval mode is a branch, not a generated graph::

    retrieve ─► search_settings ─► mode = tree    ─► tree_retrieve  ─┐   (auto: routed here)
                                   mode = graph   ─► graph_retrieve ─┤
                                   else           ─► seed_retrieve  ─┴► pick_hits
    seed_retrieve:  dense ─► dense_retrieve | lexical ─► lexical_retrieve | hybrid_retrieve
    search:         retrieve ─► hydrate (the catalog gate)
    rerank_search:  search(depth) ─► RerankOp ─► apply_rerank
    ranked_search:  wants_rerank ─► rerank_search | search ─► pick_search
    search_flow:    ingress ─► search_request ─► ranked_search ─► search_result ─► egress

A search's outputs are ``hits`` (hydrated: text, document, version, spans,
pages), ``documents`` (what a reranker reads) and ``stats``.
"""

from __future__ import annotations

from operonx import END, PARENT, START, graph
from operonx.app.serve import egress, ingress
from operonx.core.ops import if_
from operonx.providers.ops import EmbeddingOp, LLMOp, RerankOp, VectorSearchOp

from operonx_kb.ops.graph import graph_hits
from operonx_kb.ops.retrieve import (
    apply_rerank,
    dense_hits,
    fusion_depth,
    hydrate,
    lexical_search,
    pick_hits,
    pick_search,
    plan_dense,
    rrf,
    wants_rerank,
)
from operonx_kb.ops.retrieve import rerank_depth as rerank_size  # rerank_depth is an input
from operonx_kb.ops.serve import search_request, search_result
from operonx_kb.ops.settings import search_settings
from operonx_kb.ops.tree import tree_advance, tree_candidates, tree_hits, tree_options

__all__ = [
    "dense_retrieve",
    "lexical_retrieve",
    "hybrid_retrieve",
    "seed_retrieve",
    "tree_retrieve",
    "graph_retrieve",
    "retrieve",
    "search",
    "rerank_search",
    "ranked_search",
    "search_flow",
]

#: Candidates each side of a hybrid search returns before fusion (at least ``k``).
HYBRID_DEPTH = 50
#: Reciprocal rank fusion's constant.
RRF_K = 60
#: A safety cap on the tree navigator's loop; the collection's ``max_depth`` stops it first.
TREE_LOOP_CAP = 16


@graph
def dense_retrieve(query, collection, filter, k, catalog):
    """The collection's dense index: the query (under its template) embedded and searched."""
    s = search_settings(collection=collection, catalog=catalog, embeds=True)
    plan = plan_dense(query=query, collection=collection, filter=filter, k=k, store=s["store"],
                      template=s["query_template"], catalog=catalog)  # fmt: skip
    embed = EmbeddingOp.of(resource=s["embedder"], texts=plan["texts"])
    found = VectorSearchOp.of(
        resource=s["store"],
        query_vector=embed["embeddings"][0],
        top_k=plan["fetch_k"],
        filter=plan["native"],
        collection=s["vector_collection"],
    )
    hits = dense_hits(ids=found["ids"], scores=found["scores"], plan=plan["plan"],
                      collection=collection, filter=filter, store=s["store"],
                      vcollection=s["vcollection"], catalog=catalog)  # fmt: skip
    START >> s >> plan >> embed >> found >> hits >> END


@graph
def lexical_retrieve(query, collection, filter, k, catalog):
    """The collection's lexical index (BM25 on SQLite, cover density on Postgres)."""
    s = search_settings(collection=collection, catalog=catalog)
    matched = lexical_search(query=query, collection=collection, filter=filter, k=k,
                             lexical=s["lexical"], catalog=catalog)  # fmt: skip
    START >> s >> matched >> END


@graph
def hybrid_retrieve(query, collection, filter, k, catalog):
    """Dense and lexical, concurrently, fused by reciprocal rank and cut to ``k``."""
    size = fusion_depth(k=k, depth=HYBRID_DEPTH)
    a = dense_retrieve(query=query, collection=collection, filter=filter, k=size["depth"],
                       catalog=catalog)  # fmt: skip
    b = lexical_retrieve(query=query, collection=collection, filter=filter, k=size["depth"],
                         catalog=catalog)  # fmt: skip
    fused = rrf(first=a["hits"], second=b["hits"], k=k, rrf_k=RRF_K)
    START >> size
    size >> a
    size >> b
    a >> fused
    b >> fused  # fused waits for both
    fused >> END


@graph
def seed_retrieve(query, collection, filter, k, mode, catalog):
    """One of the three index modes, by ``mode`` (``dense``, ``lexical``, ``hybrid``)."""
    d = dense_retrieve(query=query, collection=collection, filter=filter, k=k, catalog=catalog)
    lx = lexical_retrieve(query=query, collection=collection, filter=filter, k=k, catalog=catalog)
    h = hybrid_retrieve(query=query, collection=collection, filter=filter, k=k, catalog=catalog)
    picked = pick_hits(first=d["hits"], second=lx["hits"], third=h["hits"])
    START >> if_(mode == "dense", d).if_(mode == "lexical", lx).else_(h)
    d >> picked
    lx >> picked
    h >> picked
    picked >> END


@graph
def tree_retrieve(query, collection, filter, k, catalog):
    """Tree search (PLAN E7): the collection's default mode names the candidate
    documents, a navigator walks their tree index in a bounded beam loop, and the
    picked sections' chunks come first, then the seed's other hits.

    ::

        seed ─► tree_candidates ─► if any ─► tree_options ─► LLMOp (navigator) ─► tree_advance ─┐
                                    │              ▲                                            │
                                    │              └──────────────── not done ◄─────────────────┤
                                    └─ else ───────────────────────────────► tree_hits ◄─ done ─┘
    """
    # loop state, and the loop's settings: ops inside the loop read cells, not `s`
    PARENT.declare(roots=None, frontier=None, picked=None, depth=0, beam=1, max_depth=1,
                   navigator=None)  # fmt: skip
    s = search_settings(collection=collection, catalog=catalog)
    s["tree_beam"] >> PARENT["beam"]
    s["tree_max_depth"] >> PARENT["max_depth"]
    s["navigator"] >> PARENT["navigator"]
    size = fusion_depth(k=k, depth=s["tree_seed_depth"])
    found = seed_retrieve(query=query, collection=collection, filter=filter, k=size["depth"],
                          mode=s["seed_mode"], catalog=catalog)  # fmt: skip
    start = tree_candidates(hits=found["hits"], collection=collection, docs=s["tree_docs"],
                            catalog=catalog)  # fmt: skip
    start["roots"] >> PARENT["roots"]
    step = tree_options(query=query, roots=PARENT["roots"], frontier=PARENT["frontier"],
                        beam=PARENT["beam"], catalog=catalog)  # fmt: skip
    nav = LLMOp.of(
        resource=PARENT["navigator"],
        messages=step["messages"],
        fields=["choose: list", "enough: bool"],
        parser="json",
        max_retries=1,
    )
    move = tree_advance(options=step["options"], choose=nav["choose"], enough=nav["enough"],
                        picked=PARENT["picked"], depth=PARENT["depth"], beam=PARENT["beam"],
                        max_depth=PARENT["max_depth"])  # fmt: skip
    move["frontier"] >> PARENT["frontier"]
    move["picked"] >> PARENT["picked"]
    move["depth"] >> PARENT["depth"]
    hits = tree_hits(seed=found["hits"], picked=PARENT["picked"], k=k, catalog=catalog)
    START >> s >> size >> found >> start >> if_(start["any"] == True, step).else_(hits)  # noqa: E712
    step >> nav >> move
    move >> if_(move["done"] == True, hits, max_iterations=TREE_LOOP_CAP).else_(step)  # noqa: E712
    hits >> END


@graph
def graph_retrieve(query, collection, filter, k, catalog):
    """Graph search (PLAN G4): the collection's default mode finds where to start, a
    personalized PageRank walk over its concept graph finds what those chunks link to.
    No model call."""
    s = search_settings(collection=collection, catalog=catalog)
    size = fusion_depth(k=k, depth=s["graph_seed_depth"])
    found = seed_retrieve(query=query, collection=collection, filter=filter, k=size["depth"],
                          mode=s["seed_mode"], catalog=catalog)  # fmt: skip
    walked = graph_hits(seed=found["hits"], collection=collection, k=k, graph=s["graph"],
                        catalog=catalog, filter=filter)  # fmt: skip
    START >> s >> size >> found >> walked >> END


@graph
def retrieve(query, collection, filter, k, mode, catalog):
    """The collection's retriever for ``mode`` (default: hybrid with both indexes, else
    the one it has; ``auto`` routes on the query). A mode the collection cannot serve
    fails in ``search_settings``."""
    s = search_settings(collection=collection, catalog=catalog, mode=mode, query=query)
    t = tree_retrieve(query=query, collection=collection, filter=filter, k=k, catalog=catalog)
    g = graph_retrieve(query=query, collection=collection, filter=filter, k=k, catalog=catalog)
    plain = seed_retrieve(query=query, collection=collection, filter=filter, k=k,
                          mode=s["mode"], catalog=catalog)  # fmt: skip
    picked = pick_hits(first=t["hits"], second=g["hits"], third=plain["hits"])
    START >> s >> if_(s["mode"] == "tree", t).if_(s["mode"] == "graph", g).else_(plain)
    t >> picked
    g >> picked
    plain >> picked
    picked >> END


@graph
def search(query, collection, filter, k, mode, catalog):
    """A retriever followed by the hydration gate: hits with text and provenance."""
    found = retrieve(query=query, collection=collection, filter=filter, k=k, mode=mode,
                     catalog=catalog)  # fmt: skip
    hydrated = hydrate(hits=found["hits"], collection=collection, filter=filter, k=k,
                       catalog=catalog)  # fmt: skip
    START >> found >> hydrated >> END


@graph
def rerank_search(query, collection, filter, k, mode, reranker, rerank_depth, catalog):
    """``max(k, rerank_depth)`` hydrated hits re-ordered by ``RerankOp`` (``reranker``,
    a ``reranking:`` resource) and cut to ``k``."""
    size = rerank_size(k=k, depth=rerank_depth)
    found = search(query=query, collection=collection, filter=filter, k=size["depth"],
                   mode=mode, catalog=catalog)  # fmt: skip
    scored = RerankOp.of(resource=reranker, query=query, documents=found["documents"], top_k=k)
    ordered = apply_rerank(hits=found["hits"], reranks=scored["reranks"], k=k)
    START >> size >> found >> scored >> ordered >> END


@graph
def ranked_search(query, collection, filter, k, mode, reranker, rerank_depth, catalog):
    """``search``, or ``rerank_search`` when ``reranker`` names one."""
    w = wants_rerank(reranker=reranker)
    plain = search(query=query, collection=collection, filter=filter, k=k, mode=mode,
                   catalog=catalog)  # fmt: skip
    rr = rerank_search(query=query, collection=collection, filter=filter, k=k, mode=mode,
                       reranker=reranker, rerank_depth=rerank_depth, catalog=catalog)  # fmt: skip
    picked = pick_search(hits=plain["hits"], documents=plain["documents"], stats=plain["stats"],
                         rr_hits=rr["hits"], rr_documents=rr["documents"],
                         rr_stats=rr["stats"])  # fmt: skip
    START >> w >> if_(w["yes"] == True, rr).else_(plain)  # noqa: E712
    plain >> picked
    rr >> picked
    picked >> END


@graph
def search_flow(mode, reranker, rerank_depth, k, catalog):
    """A search behind doors: each item ``{"query", "collection", "filter"?, "k"?}``
    gets one ``{"hits", "stats"}`` back. Runs as a Job, an Eval or a Service; the
    run's inputs (``mode``, ``reranker``…) are the same for every item."""
    src = ingress()
    req = search_request(item=src["item"], k=k)
    found = ranked_search(query=req["query"], collection=req["collection"], filter=req["filter"],
                          k=req["k"], mode=mode, reranker=reranker, rerank_depth=rerank_depth,
                          catalog=catalog)  # fmt: skip
    res = search_result(hits=found["hits"], stats=found["stats"])
    out = egress(item=res["result"])
    START >> src >> req >> found >> res >> out >> END
