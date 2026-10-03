"""Delete and GC ops: the logic of :mod:`operonx_kb.graphs.maintenance`.

A delete is two steps (track5 §11.5). The **tombstone** sets ``deleted_at``
and clears the active version in one catalog write, so the document leaves
every hydrated result at once. Its vectors are then deleted
(``VectorDeleteOp``) and only after that leave the ledger. ``purge`` also
erases every catalog row and every blob no other version references, then
proves nothing is left (:func:`~operonx_kb.maintenance.verify_document_gone`).
"""

from __future__ import annotations

from typing import Optional

from operonx import op

from operonx_kb.errors import CatalogError
from operonx_kb.maintenance import delete_unreferenced_blobs, verify_document_gone
from operonx_kb.model.ids import document_id as make_document_id
from operonx_kb.ops._resources import blobs_of, catalog_of, full_key

__all__ = [
    "tombstone_document",
    "finish_delete",
    "find_stale_entries",
    "collect_blobs",
    "gc_report",
]


@op(bound="cpu", show_keys="document_id")
def tombstone_document(
    collection: str, key: str, store: str, vcollection: str, catalog: str
) -> dict:
    """Hide the document now; return the vector keys the ledger holds for it."""
    cat = catalog_of(catalog)
    doc_id = make_document_id(collection, key)
    if cat.get_document(doc_id) is None:
        raise CatalogError(f"no document {key!r} in collection {collection!r}")
    cat.tombstone(doc_id)
    entries = cat.index_entries(full_key(store, "vector_store"), vcollection, document_id=doc_id)
    return {
        "document_id": doc_id,
        "chunk_ids": sorted(entries),
        "vector_ids": [entries[c] for c in sorted(entries)],
    }


@op(bound="cpu", show_keys="report")
def finish_delete(
    collection: str,
    key: str,
    document_id: str,
    chunk_ids: list,
    purge: bool,
    store: str,
    vcollection: str,
    catalog: str,
    blobs: str,
    deleted: Optional[int] = None,
) -> dict:
    """After the vectors are gone: drop their ledger rows, and with ``purge`` erase the document."""
    cat = catalog_of(catalog)
    store_key = full_key(store, "vector_store")
    forgotten = cat.forget_index_entries(store_key, vcollection, chunk_ids)
    report = {
        "document_id": document_id,
        "index_deleted": deleted,
        "ledger_forgotten": forgotten,
        "purged": False,
    }
    if purge:
        store_ = blobs_of(blobs)
        result = cat.purge(document_id)
        removed = sum(int(store_.delete(sha)) for sha in result.orphan_blobs)
        verify_document_gone(cat, store_, document_id, result.orphan_blobs, store_key, vcollection)
        report.update(purged=True, chunks_purged=len(result.chunk_ids), blobs_deleted=removed)
    cat.log_ingest(collection, key, "purge" if purge else "delete", document_id=document_id)
    return {"report": report}


@op(bound="cpu", show_keys="count")
def find_stale_entries(collection: str, store: str, vcollection: str, catalog: str) -> dict:
    """Ledger entries of the collection that no active version holds any more."""
    cat = catalog_of(catalog)
    entries = cat.index_entries(
        full_key(store, "vector_store"), vcollection, collection_id=collection
    )
    stale = sorted(set(entries) - cat.active_chunk_ids(collection_id=collection))
    return {"chunk_ids": stale, "vector_ids": [entries[c] for c in stale], "count": len(stale)}


@op(bound="cpu", show_keys="blobs_deleted")
def collect_blobs(enabled: bool, grace_seconds: float, catalog: str, blobs: str) -> dict:
    """Delete unreferenced blobs older than the grace period, when ``enabled``.

    The blob store is shared by every collection of the catalog, so this is opt-in.
    """
    deleted = (
        delete_unreferenced_blobs(catalog_of(catalog), blobs_of(blobs), grace_seconds)
        if enabled
        else 0
    )
    return {"blobs_deleted": deleted}


@op(show_keys="report")
def gc_report(
    stale: int = 0, deleted: Optional[int] = None, forgotten: int = 0, blobs_deleted: int = 0
) -> dict:
    """One summary of a GC run."""
    return {
        "report": {
            "stale": stale,
            "index_deleted": deleted,
            "ledger_forgotten": forgotten,
            "blobs_deleted": blobs_deleted,
        }
    }
