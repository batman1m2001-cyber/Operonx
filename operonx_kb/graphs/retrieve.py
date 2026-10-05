"""Retriever factories (wiring only; the logic is in :mod:`operonx_kb.ops.retrieve`).

Every retriever is a ``@graph`` with the inputs ``(query, collection, filter, k)``
and the output ``hits`` (chunk ids and scores; track5 §9.1), so any two compose
with :func:`hybrid_retriever` and any one becomes a search with
:func:`search_graph`. Each factory takes a few parameters; there is no god
graph::

    dense   = dense_retriever(spec.dense)                 # EmbeddingOp ─► VectorSearchOp ─► dense_hits
    lexical = lexical_retriever(spec.lexical)             # lexical_search
    hybrid  = hybrid_retriever(dense, lexical)            # both, concurrently ─► rrf
    tree    = tree_retriever(hybrid, spec.tree)           # seed ─► beam loop over the tree index (LLMOp)
    graph   = graph_retriever(hybrid, spec.graph)         # seed ─► personalized PageRank over concepts
    search  = search_graph(hybrid)                        # retriever ─► hydrate (the catalog gate)
    best    = reranked(search, reranker="bge-reranker")   # search(depth) ─► RerankOp ─► apply_rerank
    flow    = build_search_flow(best)                     # behind doors: a Job, an Eval, a Service

A search's outputs are ``hits`` (hydrated: text, document, version, spans,
pages), ``documents`` (what a reranker reads) and ``stats``.
"""

from __future__ import annotations

from operonx import END, PARENT, START, graph
from operonx.app.serve import egress, ingress
from operonx.core.ops import if_
from operonx.providers.ops import EmbeddingOp, LLMOp, RerankOp, VectorSearchOp

from operonx_kb.errors import KBError
from operonx_kb.model.collection import DenseIndexSpec, GraphSpec, LexicalIndexSpec, TreeSpec
from operonx_kb.ops.graph import graph_hits
from operonx_kb.ops.retrieve import (
    apply_rerank,
    dense_hits,
    fusion_depth,
    hydrate,
    lexical_search,
    plan_dense,
    rerank_depth,
    rrf,
)
from operonx_kb.ops.serve import search_request, search_result
from operonx_kb.ops.tree import tree_advance, tree_candidates, tree_hits, tree_options

__all__ = [
    "dense_retriever",
    "lexical_retriever",
    "hybrid_retriever",
    "tree_retriever",
    "graph_retriever",
    "search_graph",
    "reranked",
    "build_search_flow",
]


def _embedding_name(key: str) -> str:
    """The name ``EmbeddingOp`` takes for an embedder key (it resolves ``embedding:<name>`` only)."""
    category, _, name = key.rpartition(":")
    if category in ("", "embedding"):
        return name
    raise KBError(
        f"the query side embeds with operonx's EmbeddingOp, which reaches embedding: resources "
        f"only, and {key!r} is in {category!r}. Alias it, e.g. "
        f"ResourceHub.instance().alias('embedding:{name}', '{key}'), and name the embedder "
        f"{name!r} in the collection's DenseIndexSpec."
    )


def dense_retriever(
    dense: DenseIndexSpec,
    *,
    catalog: str = "kb_catalog:main",
    overfetch: float = 1.5,
    post_filter_overfetch: int = 10,
):
    """A retriever over the collection's dense index: the query (under
    ``dense.query_template``) is embedded and searched under the compiled filter."""
    embedder = _embedding_name(dense.embedder)
    vcollection = dense.collection or ""

    @graph
    def dense_retrieve(query, collection, filter, k):
        plan = plan_dense(
            query=query,
            collection=collection,
            filter=filter,
            k=k,
            store=dense.store,
            template=dense.query_template,
            catalog=catalog,
            overfetch=overfetch,
            post_filter_overfetch=post_filter_overfetch,
        )
        embed = EmbeddingOp.of(resource=embedder, texts=plan["texts"])
        found = VectorSearchOp.of(
            resource=dense.store,
            query_vector=embed["embeddings"][0],
            top_k=plan["fetch_k"],
            filter=plan["native"],
            collection=dense.collection,
        )
        hits = dense_hits(
            ids=found["ids"],
            scores=found["scores"],
            plan=plan["plan"],
            collection=collection,
            filter=filter,
            store=dense.store,
            vcollection=vcollection,
            catalog=catalog,
        )
        START >> plan >> embed >> found >> hits >> END

    return dense_retrieve


def lexical_retriever(
    lexical: LexicalIndexSpec, *, catalog: str = "kb_catalog:main", overfetch: float = 1.5
):
    """A retriever over the collection's lexical index (BM25 on SQLite, cover density on Postgres)."""
    spec = lexical.model_dump(mode="json")

    @graph
    def lexical_retrieve(query, collection, filter, k):
        matched = lexical_search(
            query=query,
            collection=collection,
            filter=filter,
            k=k,
            lexical=spec,
            catalog=catalog,
            overfetch=overfetch,
        )
        START >> matched >> END

    return lexical_retrieve


def hybrid_retriever(first, second, *, rrf_k: int = 60, depth: int = 50):
    """Two retrievers run concurrently, fused by reciprocal rank. Each returns
    ``max(k, depth)`` candidates; the fused list is cut to ``k``."""

    @graph
    def hybrid_retrieve(query, collection, filter, k):
        size = fusion_depth(k=k, depth=depth)
        a = first(query=query, collection=collection, filter=filter, k=size["depth"])
        b = second(query=query, collection=collection, filter=filter, k=size["depth"])
        fused = rrf(first=a["hits"], second=b["hits"], k=k, rrf_k=rrf_k)
        START >> size
        size >> a
        size >> b
        a >> fused
        b >> fused  # fused waits for both
        fused >> END

    return hybrid_retrieve


def tree_retriever(seed, tree: TreeSpec, *, catalog: str = "kb_catalog:main"):
    """Tree search (PLAN E7): ``seed`` (a retriever) names the candidate documents,
    a navigator walks their tree index in a bounded beam loop, and the picked
    sections' chunks come first, then the seed's other hits.

    ::

        seed ─► tree_candidates ─► if any ─► tree_options ─► LLMOp (navigator) ─► tree_advance ─┐
                                    │              ▲                                            │
                                    │              └──────────────── not done ◄─────────────────┤
                                    └─ else ───────────────────────────────► tree_hits ◄─ done ─┘
    """

    @graph
    def tree_retrieve(query, collection, filter, k):
        PARENT.declare(roots=None, frontier=None, picked=None, depth=0)
        size = fusion_depth(k=k, depth=tree.seed_depth)
        found = seed(query=query, collection=collection, filter=filter, k=size["depth"])
        start = tree_candidates(
            hits=found["hits"], collection=collection, docs=tree.docs, catalog=catalog
        )
        start["roots"] >> PARENT["roots"]
        step = tree_options(
            query=query,
            roots=PARENT["roots"],
            frontier=PARENT["frontier"],
            beam=tree.beam,
            catalog=catalog,
        )
        nav = LLMOp.of(
            resource=tree.navigator_llm,
            messages=step["messages"],
            fields=["choose: list", "enough: bool"],
            parser="json",
            max_retries=1,
        )
        move = tree_advance(
            options=step["options"],
            choose=nav["choose"],
            enough=nav["enough"],
            picked=PARENT["picked"],
            depth=PARENT["depth"],
            beam=tree.beam,
            max_depth=tree.max_depth,
        )
        move["frontier"] >> PARENT["frontier"]
        move["picked"] >> PARENT["picked"]
        move["depth"] >> PARENT["depth"]
        hits = tree_hits(seed=found["hits"], picked=PARENT["picked"], k=k, catalog=catalog)
        START >> size >> found >> start >> if_(start["any"] == True, step).else_(hits)  # noqa: E712
        step >> nav >> move
        move >> if_(move["done"] == True, hits, max_iterations=tree.max_depth).else_(step)  # noqa: E712
        hits >> END

    return tree_retrieve


def graph_retriever(seed, spec: GraphSpec, *, catalog: str = "kb_catalog:main"):
    """Graph search (PLAN G4): ``seed`` (a retriever) finds where to start, a
    personalized PageRank walk over the collection's concept graph finds what
    those chunks link to, and the chunks come by where the walk ends, then the
    seed's other hits. No model call.

    ::

        fusion_depth ─► seed ─► graph_hits
    """
    settings = spec.model_dump(mode="json")

    @graph
    def graph_retrieve(query, collection, filter, k):
        size = fusion_depth(k=k, depth=spec.seed_depth)
        found = seed(query=query, collection=collection, filter=filter, k=size["depth"])
        walked = graph_hits(seed=found["hits"], collection=collection, k=k, graph=settings,
                            catalog=catalog, filter=filter)  # fmt: skip
        START >> size >> found >> walked >> END

    return graph_retrieve


def search_graph(retriever, *, catalog: str = "kb_catalog:main"):
    """A retriever followed by the hydration gate: hits with text and provenance."""

    @graph
    def search(query, collection, filter, k):
        found = retriever(query=query, collection=collection, filter=filter, k=k)
        hydrated = hydrate(
            hits=found["hits"], collection=collection, filter=filter, k=k, catalog=catalog
        )
        START >> found >> hydrated >> END

    return search


def reranked(search, reranker: str, *, depth: int = 30):
    """A search whose ``max(k, depth)`` hydrated hits are re-ordered by ``RerankOp``
    (a ``reranking:`` resource, e.g. a cross-encoder) and cut to ``k``."""

    @graph
    def rerank_search(query, collection, filter, k):
        size = rerank_depth(k=k, depth=depth)
        found = search(query=query, collection=collection, filter=filter, k=size["depth"])
        scored = RerankOp.of(resource=reranker, query=query, documents=found["documents"], top_k=k)
        ordered = apply_rerank(hits=found["hits"], reranks=scored["reranks"], k=k)
        START >> size >> found >> scored >> ordered >> END

    return rerank_search


def build_search_flow(search, *, k: int = 10):
    """A search behind doors: each item ``{"query", "collection", "filter"?, "k"?}``
    gets one ``{"hits", "stats"}`` back. Runs as a Job, an Eval or a Service."""

    @graph
    def search_flow():
        src = ingress()
        req = search_request(item=src["item"], k=k)
        found = search(query=req["query"], collection=req["collection"], filter=req["filter"],
                       k=req["k"])  # fmt: skip
        res = search_result(hits=found["hits"], stats=found["stats"])
        out = egress(item=res["result"])
        START >> src >> req >> found >> res >> out >> END

    return search_flow
