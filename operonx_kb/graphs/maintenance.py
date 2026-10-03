"""Delete and GC graphs (wiring only; the logic is in :mod:`operonx_kb.ops.maintenance`).

::

    delete:  tombstone_document ─► VectorDeleteOp ─► finish_delete (forget ledger rows; purge)
    gc:      find_stale_entries ─► VectorDeleteOp ─► forget_index_writes ─► collect_blobs ─► gc_report
"""

from __future__ import annotations

from operonx import END, START, graph
from operonx.providers.ops import VectorDeleteOp

from operonx_kb.model.collection import DenseIndexSpec
from operonx_kb.ops import forget_index_writes
from operonx_kb.ops.maintenance import (
    collect_blobs,
    find_stale_entries,
    finish_delete,
    gc_report,
    tombstone_document,
)

__all__ = ["build_delete_graph", "build_gc_graph"]


def build_delete_graph(
    dense: DenseIndexSpec, *, catalog: str = "kb_catalog:main", blobs: str = "kb_blob:main"
):
    """Delete one document (``collection``, ``key``, ``purge``) from the catalog and the index."""
    vcollection = dense.collection or ""

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
        )
        START >> tomb >> delete >> finish >> END

    return delete_document


def build_gc_graph(
    dense: DenseIndexSpec, *, catalog: str = "kb_catalog:main", blobs: str = "kb_blob:main"
):
    """Garbage-collect one collection (``collection``, ``blobs_enabled``, ``grace_seconds``)."""
    vcollection = dense.collection or ""

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
        sweep = collect_blobs(
            enabled=blobs_enabled, grace_seconds=grace_seconds, catalog=catalog, blobs=blobs
        )
        summary = gc_report(
            stale=stale["count"],
            deleted=delete["deleted"],
            forgotten=forget["forgotten"],
            blobs_deleted=sweep["blobs_deleted"],
        )
        START >> stale >> delete >> forget >> sweep >> summary >> END

    return collect_garbage
