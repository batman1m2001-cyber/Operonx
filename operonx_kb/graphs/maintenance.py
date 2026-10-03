"""Delete, GC and rebuild graphs (wiring only; the logic is in :mod:`operonx_kb.ops.maintenance`
and :mod:`operonx_kb.ops.lexical`).

::

    delete:          tombstone_document ─► VectorDeleteOp ─► delete_document_lexical ─► finish_delete
    gc:              find_stale_entries ─► VectorDeleteOp ─► forget_index_writes ─► collect_lexical
                     ─► collect_blobs ─► gc_report
    rebuild:         plan_rebuild ─► EmbedChunksOp (cache) ─► stage_index_writes ─► VectorUpsertOp
                     ─► finish_rebuild
    drop:            find_stale_entries(everything) ─► VectorDeleteOp ─► forget_index_writes
    rebuild_lexical: plan_rebuild ─► write_lexical ─► finish_lexical_rebuild
    drop_lexical:    collect_lexical(everything)

The lexical ops do nothing when the collection has no lexical index.
"""

from __future__ import annotations

from typing import Optional

from operonx import END, START, graph
from operonx.providers.ops import VectorDeleteOp, VectorUpsertOp

from operonx_kb.model.collection import DenseIndexSpec, LexicalIndexSpec
from operonx_kb.ops import EmbedChunksOp, forget_index_writes, stage_index_writes
from operonx_kb.ops.lexical import (
    collect_lexical,
    delete_document_lexical,
    finish_lexical_rebuild,
    write_lexical,
)
from operonx_kb.ops.maintenance import (
    collect_blobs,
    find_stale_entries,
    finish_delete,
    finish_rebuild,
    gc_report,
    plan_rebuild,
    tombstone_document,
)

__all__ = [
    "build_delete_graph",
    "build_gc_graph",
    "build_rebuild_graph",
    "build_drop_index_graph",
    "build_lexical_rebuild_graph",
    "build_lexical_drop_graph",
]


def _dump(lexical: Optional[LexicalIndexSpec]) -> Optional[dict]:
    return lexical.model_dump(mode="json") if lexical else None


def build_delete_graph(
    dense: DenseIndexSpec,
    *,
    lexical: Optional[LexicalIndexSpec] = None,
    catalog: str = "kb_catalog:main",
    blobs: str = "kb_blob:main",
):
    """Delete one document (``collection``, ``key``, ``purge``) from the catalog and the indexes."""
    vcollection = dense.collection or ""
    lexical_spec = _dump(lexical)

    @graph
    def delete_document(collection, key, purge):
        tomb = tombstone_document(
            collection=collection,
            key=key,
            store=dense.store,
            vcollection=vcollection,
            catalog=catalog,
        )
        delete = VectorDeleteOp.of(
            resource=dense.store, ids=tomb["vector_ids"], collection=dense.collection
        )
        lex = delete_document_lexical(
            document_id=tomb["document_id"], catalog=catalog, lexical=lexical_spec
        )
        finish = finish_delete(
            collection=collection,
            key=key,
            document_id=tomb["document_id"],
            chunk_ids=tomb["chunk_ids"],
            purge=purge,
            store=dense.store,
            vcollection=vcollection,
            catalog=catalog,
            blobs=blobs,
            deleted=delete["deleted"],
            lexical=lexical_spec,
            lexical_deleted=lex["deleted"],
        )
        START >> tomb >> delete >> lex >> finish >> END

    return delete_document


def build_gc_graph(
    dense: DenseIndexSpec,
    *,
    lexical: Optional[LexicalIndexSpec] = None,
    catalog: str = "kb_catalog:main",
    blobs: str = "kb_blob:main",
):
    """Garbage-collect one collection (``collection``, ``blobs_enabled``, ``grace_seconds``)."""
    vcollection = dense.collection or ""
    lexical_spec = _dump(lexical)

    @graph
    def collect_garbage(collection, blobs_enabled, grace_seconds):
        stale = find_stale_entries(
            collection=collection, store=dense.store, vcollection=vcollection, catalog=catalog
        )
        delete = VectorDeleteOp.of(
            resource=dense.store, ids=stale["vector_ids"], collection=dense.collection
        )
        forget = forget_index_writes(
            removed=stale["chunk_ids"],
            store=dense.store,
            collection=vcollection,
            catalog=catalog,
            deleted=delete["deleted"],
        )
        lex = collect_lexical(collection=collection, catalog=catalog, lexical=lexical_spec)
        sweep = collect_blobs(
            enabled=blobs_enabled, grace_seconds=grace_seconds, catalog=catalog, blobs=blobs
        )
        summary = gc_report(
            stale=stale["count"],
            deleted=delete["deleted"],
            forgotten=forget["forgotten"],
            blobs_deleted=sweep["blobs_deleted"],
            lexical_deleted=lex["deleted"],
        )
        START >> stale >> delete >> forget >> lex >> sweep >> summary >> END

    return collect_garbage


def build_rebuild_graph(
    dense: DenseIndexSpec,
    *,
    lexical: Optional[LexicalIndexSpec] = None,
    catalog: str = "kb_catalog:main",
    blobs: str = "kb_blob:main",
):
    """Rebuild a collection's dense index into ``dense.store``/``dense.collection`` from the
    catalog alone: no parsing, and embeddings come from the cache when the embedder is
    unchanged. Inputs: ``collection``, ``switch`` (make it the collection's index)."""
    vcollection = dense.collection or ""

    @graph
    def rebuild_index(collection, switch):
        plan = plan_rebuild(collection=collection, catalog=catalog)
        embed = EmbedChunksOp.of(
            resource=dense.embedder,
            catalog=catalog,
            batch_size=dense.batch_size,
            template=dense.passage_template,
            chunks=plan["chunks"],
        )
        stage = stage_index_writes(
            todo=plan["chunks"],
            vectors=embed["vectors"],
            store=dense.store,
            vcollection=vcollection,
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
        finish = finish_rebuild(
            collection=collection,
            store=dense.store,
            vcollection=vcollection,
            switch=switch,
            catalog=catalog,
            chunks=plan["count"],
            upserted=upsert["upserted"],
        )
        START >> plan >> embed >> stage >> upsert >> finish >> END

    return rebuild_index


def build_drop_index_graph(
    dense: DenseIndexSpec,
    *,
    lexical: Optional[LexicalIndexSpec] = None,
    catalog: str = "kb_catalog:main",
    blobs: str = "kb_blob:main",
):
    """Delete every vector the ledger records for a collection in ``dense.store`` (an old
    index generation). Input: ``collection``."""
    vcollection = dense.collection or ""

    @graph
    def drop_index(collection):
        entries = find_stale_entries(
            collection=collection,
            store=dense.store,
            vcollection=vcollection,
            catalog=catalog,
            everything=True,
        )
        delete = VectorDeleteOp.of(
            resource=dense.store, ids=entries["vector_ids"], collection=dense.collection
        )
        forget = forget_index_writes(
            removed=entries["chunk_ids"],
            store=dense.store,
            collection=vcollection,
            catalog=catalog,
            deleted=delete["deleted"],
        )
        START >> entries >> delete >> forget >> END
        delete >> END  # its count is part of the result

    return drop_index


def build_lexical_rebuild_graph(
    lexical: LexicalIndexSpec, *, catalog: str = "kb_catalog:main", blobs: str = "kb_blob:main"
):
    """Rebuild a collection's lexical index into ``lexical`` (another table, or another
    analyzer) from the catalog alone. Inputs: ``collection``, ``switch``."""
    lexical_spec = _dump(lexical)

    @graph
    def rebuild_lexical(collection, switch):
        plan = plan_rebuild(collection=collection, catalog=catalog)
        write = write_lexical(
            todo=plan["chunks"],
            payloads=plan["payloads"],
            collection=collection,
            catalog=catalog,
            lexical=lexical_spec,
        )
        finish = finish_lexical_rebuild(
            collection=collection,
            lexical=lexical_spec,
            switch=switch,
            catalog=catalog,
            chunks=plan["count"],
            upserted=write["written"],
        )
        START >> plan >> write >> finish >> END

    return rebuild_lexical


def build_lexical_drop_graph(
    lexical: LexicalIndexSpec, *, catalog: str = "kb_catalog:main", blobs: str = "kb_blob:main"
):
    """Delete every entry the ledger records for a collection in ``lexical`` (an old
    generation). Input: ``collection``."""
    lexical_spec = _dump(lexical)

    @graph
    def drop_lexical(collection):
        drop = collect_lexical(
            collection=collection, catalog=catalog, lexical=lexical_spec, everything=True
        )
        START >> drop >> END

    return drop_lexical
