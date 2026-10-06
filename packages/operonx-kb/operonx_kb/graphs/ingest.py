"""The ingest graph (wiring only; the logic is in :mod:`operonx_kb.ops.ingest`).

::

    plan_ingest ─► if skip ─► skipped ─────────────────────────────────────────────────────────┐
                   else  ─► index_settings ─► parse_document ─► build_tree ─┬─► chunk_version   │
                                       ─► contextualize ─► embed_chunks ─► stage_index_writes   │
                                       ─► VectorUpsertOp ─► write_lexical ─┐                    │
                                          contextualize ─► graph_concepts ─┤                    │
                                          build_tree ─► tree_index ────────┤                    │
                            commit_version ◄───────────────────────────────┘                    │
                            ─► removed_vector_ids ─► VectorDeleteOp ─► forget_index_writes      │
                            ─► delete_lexical ─► report ◄──────────────────────────────────────┘

Both indexes are written before the commit and cleaned after it (track5 §11.2).
Defined once, at module level (operonx guide 05): the collection's embedder, vector
store, lexical index and enrichment models are read by ``index_settings`` and wired
in as values. A collection without a lexical index, contextual enrichment, a tree
index or a concept graph runs the same graph: those steps have nothing to do.

:func:`ingest_document` is the per-document graph (``item``, ``collection``,
``catalog``, ``blobs`` → ``result``); :func:`ingest_flow` wraps it in doors, so
the same pipeline runs as a ``Job`` over a folder's files or behind a
``webhook``/``http`` service.
"""

from __future__ import annotations

from operonx import END, START, graph
from operonx.app.serve import egress, ingress
from operonx.core.ops import if_
from operonx.providers.ops import VectorDeleteOp, VectorUpsertOp

from operonx_kb.graphs.enrich import contextualize, tree_index
from operonx_kb.ops import (
    build_tree,
    chunk_version,
    commit_version,
    embed_chunks,
    forget_index_writes,
    parse_document,
    plan_ingest,
    removed_vector_ids,
    report,
    skipped,
    stage_index_writes,
)
from operonx_kb.ops.graph import graph_concepts
from operonx_kb.ops.lexical import delete_lexical, write_lexical
from operonx_kb.ops.settings import index_settings

__all__ = ["ingest_document", "ingest_flow"]


@graph
def ingest_document(item, collection, catalog, blobs):
    plan = plan_ingest(item=item, collection=collection, catalog=catalog, blobs=blobs)
    skip = skipped(plan=plan["plan"], catalog=catalog)
    s = index_settings(collection=collection, catalog=catalog)
    parsed = parse_document(plan=plan["plan"], blobs=blobs)
    tree = build_tree(parsed=parsed["parsed"], plan=plan["plan"])
    chunks = chunk_version(tree=tree["tree"], plan=plan["plan"], catalog=catalog,
                           enrichers=plan["enrichers"])  # fmt: skip
    enriched = contextualize(
        fresh=chunks["todo"],
        drafted=chunks["chunks"],
        requests=chunks["requests"],
        keys=chunks["keys"],
        enrichers=plan["enrichers"],
        llm=s["contextual_llm"],
        max_tokens=s["contextual_max_tokens"],
        catalog=catalog,
    )
    nodes = tree_index(tree=tree["tree"], plan=plan["plan"], enrichers=plan["enrichers"],
                       llm=s["tree_llm"], max_tokens=s["tree_max_tokens"], catalog=catalog)  # fmt: skip
    embed = embed_chunks(chunks=enriched["todo"], embedder=s["embedder"], catalog=catalog,
                         batch_size=s["batch_size"], template=s["passage_template"])  # fmt: skip
    stage = stage_index_writes(
        todo=enriched["todo"],
        vectors=embed["vectors"],
        store=s["store"],
        vcollection=s["vcollection"],
        collection=collection,
        catalog=catalog,
        payloads=plan["payloads"],
    )
    upsert = VectorUpsertOp.of(
        resource=s["store"],
        ids=stage["ids"],
        vectors=stage["vectors"],
        metadata=stage["metadata"],
        collection=s["vector_collection"],
    )
    lex = write_lexical(todo=enriched["todo"], payloads=plan["payloads"], collection=collection,
                        catalog=catalog, lexical=s["lexical"])  # fmt: skip
    concepts = graph_concepts(plan=plan["plan"], tree=tree["tree"], chunks=enriched["chunks"])
    commit = commit_version(
        plan=plan["plan"],
        tree=tree["tree"],
        chunks=enriched["chunks"],
        occurrences=chunks["occurrences"],
        catalog=catalog,
        blobs=blobs,
        written=upsert["upserted"],
        nodes=nodes["nodes"],
        mentions=concepts["mentions"],
    )
    gone = removed_vector_ids(removed=commit["removed"])
    delete = VectorDeleteOp.of(resource=s["store"], ids=gone["ids"],
                               collection=s["vector_collection"])  # fmt: skip
    forget = forget_index_writes(removed=commit["removed"], store=s["store"],
                                 collection=s["vcollection"], catalog=catalog,
                                 deleted=delete["deleted"])  # fmt: skip
    unlex = delete_lexical(chunk_ids=commit["removed"], catalog=catalog, lexical=s["lexical"])
    result = report(
        plan=plan["plan"],
        committed=commit["stats"],
        chunking=chunks["stats"],
        embedding=embed["stats"],
        deleted=delete["deleted"],
        skip=skip["result"],
        lexical_written=lex["written"],
        lexical_deleted=unlex["deleted"],
        contextual=enriched["stats"],
        tree=nodes["stats"],
        graph=concepts["stats"],
    )
    START >> plan >> if_(plan["action"] == "skip", skip).else_(s)
    s >> parsed >> tree >> chunks >> enriched >> embed >> stage >> upsert >> lex >> commit
    tree >> nodes >> commit
    enriched >> concepts >> commit
    commit >> gone >> delete >> forget >> unlex
    unlex >> result
    skip >> result
    result >> END


@graph
def ingest_flow(collection, catalog, blobs):
    """The ingest graph behind doors: one result per item received."""
    src = ingress()
    doc = ingest_document(item=src["item"], collection=collection, catalog=catalog, blobs=blobs)
    out = egress(item=doc["result"])
    START >> src >> doc >> out >> END
