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
from operonx_kb.model.collection import LexicalIndexSpec
from operonx_kb.model.filter import document_payload
from operonx_kb.model.ids import document_id as make_document_id
from operonx_kb.ops._resources import blobs_of, catalog_of, full_key

__all__ = [
    "tombstone_document",
    "finish_delete",
    "find_stale_entries",
    "collect_blobs",
    "gc_report",
    "plan_rebuild",
    "finish_rebuild",
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
    lexical: Optional[dict] = None,
    lexical_deleted: int = 0,
) -> dict:
    """After the vectors and lexical entries are gone: drop the dense ledger rows, and with
    ``purge`` erase the document and prove nothing is left in either index's ledger."""
    cat = catalog_of(catalog)
    store_key = full_key(store, "vector_store")
    forgotten = cat.forget_index_entries(store_key, vcollection, chunk_ids)
    report = {
        "document_id": document_id,
        "index_deleted": deleted,
        "lexical_deleted": lexical_deleted,
        "ledger_forgotten": forgotten,
        "purged": False,
    }
    if purge:
        store_ = blobs_of(blobs)
        result = cat.purge(document_id)
        removed = sum(int(store_.delete(sha)) for sha in result.orphan_blobs)
        indexes = [(store_key, vcollection)]
        if lexical is not None:
            spec = LexicalIndexSpec.model_validate(lexical)
            indexes.append((full_key(spec.index, "kb_lexical"), spec.collection or ""))
        verify_document_gone(cat, store_, document_id, result.orphan_blobs, indexes)
        report.update(purged=True, chunks_purged=len(result.chunk_ids), blobs_deleted=removed)
    cat.log_ingest(collection, key, "purge" if purge else "delete", document_id=document_id)
    return {"report": report}


@op(bound="cpu", show_keys="count")
def find_stale_entries(
    collection: str, store: str, vcollection: str, catalog: str, everything: bool = False
) -> dict:
    """Ledger entries of the collection that no active version holds any more
    (with ``everything``, all of them: dropping a whole index generation)."""
    cat = catalog_of(catalog)
    entries = cat.index_entries(
        full_key(store, "vector_store"), vcollection, collection_id=collection
    )
    live = set() if everything else cat.active_chunk_ids(collection_id=collection)
    stale = sorted(set(entries) - live)
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
    stale: int = 0,
    deleted: Optional[int] = None,
    forgotten: int = 0,
    blobs_deleted: int = 0,
    lexical_deleted: int = 0,
) -> dict:
    """One summary of a GC run."""
    return {
        "report": {
            "stale": stale,
            "index_deleted": deleted,
            "ledger_forgotten": forgotten,
            "lexical_deleted": lexical_deleted,
            "blobs_deleted": blobs_deleted,
        }
    }


@op(bound="cpu", exclude={"trace": ["chunks", "payloads"]}, show_keys="count")
def plan_rebuild(collection: str, catalog: str) -> dict:
    """Every chunk an active version of the collection holds, and each document's
    index payload, from the catalog alone."""
    cat = catalog_of(catalog)
    coll = cat.get_collection(collection)
    if coll is None:
        raise CatalogError(f"no collection {collection!r}")
    ids = sorted(cat.active_chunk_ids(collection_id=collection))
    chunks = cat.get_chunks(ids)
    payloads = {
        d.id: document_payload(coll.spec, d)
        for d in cat.list_documents(collection)
        if d.active_version_id
    }
    return {
        "chunks": [chunks[i].model_dump(mode="json") for i in ids],
        "payloads": payloads,
        "count": len(ids),
    }


@op(bound="cpu", show_keys="report")
def finish_rebuild(
    collection: str,
    store: str,
    vcollection: str,
    switch: bool,
    catalog: str,
    chunks: int = 0,
    upserted: int = 0,
) -> dict:
    """With ``switch``, point the collection's dense index at the rebuilt one (the alias flip)."""
    cat = catalog_of(catalog)
    coll = cat.get_collection(collection)
    if coll is None:
        raise CatalogError(f"no collection {collection!r}")
    previous = coll.spec.dense
    if switch:
        dense = previous.model_copy(update={"store": store, "collection": vcollection or None})
        cat.put_collection(
            coll.model_copy(update={"spec": coll.spec.model_copy(update={"dense": dense})})
        )
    report = {
        "chunks": chunks,
        "upserted": upserted,
        "store": full_key(store, "vector_store"),
        "collection": vcollection,
        "switched": switch,
        "previous": {"store": previous.store, "collection": previous.collection}
        if previous
        else None,
    }
    return {"report": report}
