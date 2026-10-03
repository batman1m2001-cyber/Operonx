"""The ingest graph (wiring only; the logic is in :mod:`operonx_kb.ops.ingest`).

::

    plan_ingest ─► if skip ─► skipped ──────────────────────────────────────────────────┐
                   else  ─► parse_document ─► build_tree ─► chunk_version ─► EmbedChunksOp │
                            ─► stage_index_writes ─► VectorUpsertOp ─► write_lexical       │
                            ─► commit_version ─► removed_vector_ids ─► VectorDeleteOp      │
                            ─► forget_index_writes ─► delete_lexical ─► report ◄────────────┘

Both indexes are written before the commit and cleaned after it (track5 §11.2).
A collection without a lexical index runs the same graph: the lexical ops do
nothing when its spec is ``None``.

:func:`build_ingest_graph` returns the per-document graph (``item``,
``collection`` → ``result``), which a script or test runs with ``Operon``.
:func:`build_ingest_flow` wraps it in doors, so the same pipeline runs as a
``Job`` over a ``DirSource`` or behind a ``webhook``/``http`` service.
"""

from __future__ import annotations

from typing import Optional

from operonx import END, START, graph
from operonx.app.serve import egress, ingress
from operonx.core.ops import if_
from operonx.providers.ops import VectorDeleteOp, VectorUpsertOp

from operonx_kb.model.collection import DenseIndexSpec, LexicalIndexSpec
from operonx_kb.ops import (
    EmbedChunksOp,
    build_tree,
    chunk_version,
    commit_version,
    forget_index_writes,
    parse_document,
    plan_ingest,
    removed_vector_ids,
    report,
    skipped,
    stage_index_writes,
)
from operonx_kb.ops.lexical import delete_lexical, write_lexical

__all__ = ["build_ingest_graph", "build_ingest_flow"]


def build_ingest_graph(
    dense: DenseIndexSpec,
    *,
    lexical: Optional[LexicalIndexSpec] = None,
    catalog: str = "kb_catalog:main",
    blobs: str = "kb_blob:main",
):
    """The per-document ingest graph for a collection's indexes.

    Args:
        dense: The collection's dense index spec (embedder, vector store, collection).
        lexical: Its lexical index spec, if it has one.
        catalog: The ``kb_catalog`` resource key.
        blobs: The ``kb_blob`` resource key.
    """
    collection_name = dense.collection or ""
    lexical_spec = lexical.model_dump(mode="json") if lexical else None

    @graph
    def ingest_document(item, collection):
        plan = plan_ingest(item=item, collection=collection, catalog=catalog, blobs=blobs)
        skip = skipped(plan=plan["plan"], catalog=catalog)
        parsed = parse_document(plan=plan["plan"], blobs=blobs)
        tree = build_tree(parsed=parsed["parsed"], plan=plan["plan"])
        chunks = chunk_version(tree=tree["tree"], plan=plan["plan"], catalog=catalog)
        embed = EmbedChunksOp.of(
            resource=dense.embedder,
            catalog=catalog,
            batch_size=dense.batch_size,
            template=dense.passage_template,
            chunks=chunks["todo"],
        )
        stage = stage_index_writes(
            todo=chunks["todo"],
            vectors=embed["vectors"],
            store=dense.store,
            vcollection=collection_name,
            collection=collection,
            catalog=catalog,
            payloads=plan["payloads"],
        )
        upsert = VectorUpsertOp.of(
            resource=dense.store,
            ids=stage["ids"],
            vectors=stage["vectors"],
            metadata=stage["metadata"],
            collection=dense.collection,
        )
        lex = write_lexical(
            todo=chunks["todo"],
            payloads=plan["payloads"],
            collection=collection,
            catalog=catalog,
            lexical=lexical_spec,
        )
        commit = commit_version(
            plan=plan["plan"],
            tree=tree["tree"],
            chunks=chunks["chunks"],
            occurrences=chunks["occurrences"],
            catalog=catalog,
            blobs=blobs,
            written=upsert["upserted"],
        )
        gone = removed_vector_ids(removed=commit["removed"])
        delete = VectorDeleteOp.of(
            resource=dense.store, ids=gone["ids"], collection=dense.collection
        )
        forget = forget_index_writes(
            removed=commit["removed"],
            store=dense.store,
            collection=collection_name,
            catalog=catalog,
            deleted=delete["deleted"],
        )
        unlex = delete_lexical(chunk_ids=commit["removed"], catalog=catalog, lexical=lexical_spec)
        result = report(
            plan=plan["plan"],
            committed=commit["stats"],
            chunking=chunks["stats"],
            embedding=embed["stats"],
            deleted=delete["deleted"],
            skip=skip["result"],
            lexical_written=lex["written"],
            lexical_deleted=unlex["deleted"],
        )
        START >> plan >> if_(plan["action"] == "skip", skip).else_(parsed)
        parsed >> tree >> chunks >> embed >> stage >> upsert >> lex >> commit
        commit >> gone >> delete >> forget >> unlex
        unlex >> result
        skip >> result
        result >> END

    return ingest_document


def build_ingest_flow(
    dense: DenseIndexSpec,
    *,
    lexical: Optional[LexicalIndexSpec] = None,
    catalog: str = "kb_catalog:main",
    blobs: str = "kb_blob:main",
):
    """The ingest graph behind doors: one result per item received."""
    ingest_document = build_ingest_graph(dense, lexical=lexical, catalog=catalog, blobs=blobs)

    @graph
    def ingest_flow(collection):
        src = ingress()
        doc = ingest_document(item=src["item"], collection=collection)
        out = egress(item=doc["result"])
        START >> src >> doc >> out >> END

    return ingest_flow
