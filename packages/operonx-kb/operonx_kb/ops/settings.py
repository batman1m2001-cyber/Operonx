"""A collection's settings as graph values (module-level graphs; operonx guide 05).

The KB's graphs are defined once, at module level. What differs per collection —
its embedder, vector store, lexical index, tree and graph specs, the retrieval
mode — is read here from the catalog (the store of record) when a run starts,
and wired to the ops that need it, provider ops included (``resource=`` is a
graph input since operonx 1.16).
"""

from __future__ import annotations

from typing import Optional

from operonx import op

from operonx_kb.errors import KBError, QueryError
from operonx_kb.ops._resources import catalog_of

__all__ = [
    "search_settings",
    "index_settings",
    "default_mode",
    "check_mode",
    "embedding_name",
    "MODES",
]

#: Retrieval modes. ``tree`` and ``graph`` are seeded by the collection's default mode.
MODES = ("dense", "lexical", "hybrid", "tree", "graph")


def embedding_name(key: str) -> str:
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


def default_mode(spec) -> str:
    """Hybrid when the collection has both indexes, else the one it has."""
    if spec.dense is not None and spec.lexical is not None:
        return "hybrid"
    return "dense" if spec.dense is not None else "lexical"


def check_mode(collection_id: str, spec, mode: str) -> None:
    """Refuse a mode the collection cannot serve, naming the fix."""
    if mode not in MODES:
        raise QueryError(f"unknown retrieval mode {mode!r}; use one of {list(MODES)}")
    if mode == "tree" and spec.tree is None:
        raise QueryError(
            f"collection {collection_id!r} has no tree index for mode 'tree'; set "
            "CollectionSpec(tree=TreeSpec(llm=...)) and re-add its documents"
        )
    if mode == "graph" and spec.graph is None:
        raise QueryError(
            f"collection {collection_id!r} has no concept graph for mode 'graph'; set "
            "CollectionSpec(graph=GraphSpec()) and re-add its documents"
        )
    seed = default_mode(spec) if mode in ("tree", "graph") else mode
    if seed in ("dense", "hybrid") and spec.dense is None:
        raise QueryError(f"collection {collection_id!r} has no dense index for mode {mode!r}")
    if seed in ("lexical", "hybrid") and spec.lexical is None:
        raise QueryError(
            f"collection {collection_id!r} has no lexical index for mode {mode!r}; set "
            "CollectionSpec(lexical=LexicalIndexSpec(...)) and run rebuild_lexical()"
        )


@op(bound="cpu", show_keys="mode")
def search_settings(
    collection: str, catalog: str, mode: Optional[str] = None, embeds: bool = False
) -> dict:
    """What a search of ``collection`` runs with: the resolved mode, the seed mode of
    tree/graph search, and each index's settings (``None`` for an index it lacks).
    ``embeds``: the caller embeds the query (``dense_retrieve``), so the embedder's
    ``EmbeddingOp`` name is resolved — and refused when ``EmbeddingOp`` cannot reach it."""
    coll = catalog_of(catalog).get_collection(collection)
    if coll is None:
        raise QueryError(f"no collection {collection!r}; create it with create_collection()")
    spec = coll.spec
    mode = mode or default_mode(spec)
    check_mode(collection, spec, mode)
    dense, tree = spec.dense, spec.tree
    return {
        "mode": mode,
        "seed_mode": default_mode(spec),
        "embedder": embedding_name(dense.embedder) if embeds and dense else None,
        "store": dense.store if dense else None,
        "vector_collection": dense.collection if dense else None,
        "vcollection": (dense.collection or "") if dense else "",
        "query_template": dense.query_template if dense else "{text}",
        "lexical": spec.lexical.model_dump(mode="json") if spec.lexical else None,
        "tree": tree.model_dump(mode="json") if tree else None,
        "navigator": tree.navigator_llm if tree else None,
        "tree_docs": tree.docs if tree else 0,
        "tree_seed_depth": tree.seed_depth if tree else 0,
        "tree_beam": tree.beam if tree else 0,
        "tree_max_depth": tree.max_depth if tree else 0,
        "graph": spec.graph.model_dump(mode="json") if spec.graph else None,
        "graph_seed_depth": spec.graph.seed_depth if spec.graph else 0,
    }


@op(bound="cpu", show_keys="store")
def index_settings(collection: str, catalog: str) -> dict:
    """What ingest and maintenance of ``collection`` write with: its dense index (the
    embedder, the vector store and its collection, batch size, passage template), its
    lexical index, and the models of its enrichment stages (``None`` when off)."""
    coll = catalog_of(catalog).get_collection(collection)
    if coll is None:
        raise KBError(f"collection {collection!r} does not exist; create it with create_collection")
    spec = coll.spec
    dense, ctx, tree = spec.dense, spec.contextual, spec.tree
    if dense is None:
        raise KBError(
            f"collection {collection!r} has no dense index; set CollectionSpec(dense=...)"
        )
    return {
        "embedder": dense.embedder,
        "store": dense.store,
        "vector_collection": dense.collection,
        "vcollection": dense.collection or "",
        "batch_size": dense.batch_size,
        "passage_template": dense.passage_template,
        "lexical": spec.lexical.model_dump(mode="json") if spec.lexical else None,
        "contextual_llm": ctx.llm if ctx else None,
        "contextual_max_tokens": ctx.max_tokens if ctx else None,
        "tree_llm": tree.llm if tree else None,
        "tree_max_tokens": tree.max_tokens if tree else None,
    }
