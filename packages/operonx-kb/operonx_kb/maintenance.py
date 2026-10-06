"""Verification and the pieces of delete and GC that are not index writes.

The delete and GC *pipelines* are graphs (:mod:`operonx_kb.graphs.maintenance`)
because they write to the vector store through ``VectorDeleteOp``. What lives
here is pure catalog and blob-store work they share, and :func:`verify`.

:func:`verify` checks a collection against its invariants: every raw and
canonical-text blob exists and hashes to its key, every element and chunk span
round-trips, and the index ledger holds exactly the active chunks.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

from operonx.core.media_store import MediaStore

from operonx_kb.errors import KBError, SpanInvariantError
from operonx_kb.model.ids import sha256_bytes
from operonx_kb.stores.catalog.base import Catalog
from operonx_kb.text.spans import check_chunks, check_elements

__all__ = ["VerifyReport", "verify", "verify_document_gone", "delete_unreferenced_blobs"]


@dataclass
class VerifyReport:
    """What :func:`verify` checked and what it found wrong."""

    documents: int = 0
    elements: int = 0
    chunks: int = 0
    index_entries: int = 0
    lexical_entries: int = 0
    problems: List[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.problems


def verify_document_gone(
    catalog: Catalog,
    blobs: MediaStore,
    document_id: str,
    blob_shas: List[str],
    indexes: List[Tuple[str, str]],
) -> None:
    """Assert a purged document left nothing: no catalog row, no ledger entry, no blob.

    Args:
        indexes: ``(store key, collection)`` of every index the document was written to.

    Raises:
        KBError: Naming what survived.
    """
    left = []
    if catalog.get_document(document_id) is not None:
        left.append("catalog document row")
    if catalog.list_versions(document_id):
        left.append("catalog versions")
    for store, collection in indexes:
        entries = catalog.index_entries(store, collection, document_id=document_id)
        if entries:
            left.append(f"{len(entries)} index entries in {store}")
    left.extend(f"blob {sha}" for sha in blob_shas if blobs.exists(sha))
    if left:
        raise KBError(f"purge of {document_id} left data behind: " + ", ".join(left))


def delete_unreferenced_blobs(
    catalog: Catalog, blobs: MediaStore, grace_seconds: float = 3600.0
) -> int:
    """Delete blobs no version references, keeping those younger than ``grace_seconds``.

    An ingest stores its raw bytes before it commits, so a blob of an ingest in
    flight is not referenced yet; the grace period keeps it.
    """
    refs = catalog.blob_refs()
    cutoff = time.time() - grace_seconds
    deleted = 0
    for sha, written_at in list(blobs.keys()):
        if sha not in refs and written_at <= cutoff:
            deleted += int(blobs.delete(sha))
    return deleted


def verify(
    catalog: Catalog,
    blobs: MediaStore,
    collection_id: str,
    store: Optional[str] = None,
    collection: str = "",
    lexical: Optional[str] = None,
    lexical_collection: str = "",
) -> VerifyReport:
    """Check a collection's blobs, spans and index ledgers against each other.

    Args:
        store: The dense index's full vector-store key; ``None`` skips the index check.
        collection: The vector store collection (``""`` for the default).
        lexical: The lexical index's full key; ``None`` skips it.
        lexical_collection: Its collection (``""`` for the default).
    """
    report = VerifyReport()
    active_all = set()
    for doc in catalog.list_documents(collection_id):
        if doc.active_version_id is None:
            continue
        report.documents += 1
        version = catalog.get_version(doc.active_version_id)
        raw = blobs.get(version.raw_sha)
        if raw is None or sha256_bytes(raw) != version.raw_sha:
            report.problems.append(f"{doc.key}: raw blob {version.raw_sha} missing or corrupt")
        data = blobs.get(version.text_sha)
        if data is None or sha256_bytes(data) != version.text_sha:
            report.problems.append(
                f"{doc.key}: canonical text blob {version.text_sha} missing or corrupt"
            )
            continue
        canonical = data.decode("utf-8")
        occurrences = catalog.version_chunks(version.id)
        chunks = catalog.get_chunks([o.chunk_id for o in occurrences])
        try:
            report.elements += check_elements(canonical, catalog.elements(version.id, canonical))
            report.chunks += check_chunks(
                canonical, occurrences, {cid: c.content_sha for cid, c in chunks.items()}
            )
        except SpanInvariantError as exc:
            report.problems.append(f"{doc.key}: {exc}")
        active_all |= {o.chunk_id for o in occurrences}
    for key, coll, attr, what in (
        (store, collection, "index_entries", "the index"),
        (lexical, lexical_collection, "lexical_entries", "the lexical index"),
    ):
        if key is None:
            continue
        ledger = set(catalog.index_entries(key, coll, collection_id=collection_id))
        setattr(report, attr, len(ledger))
        if active_all - ledger:
            report.problems.append(
                f"{len(active_all - ledger)} active chunks were never written to {what}"
            )
        if ledger - active_all:
            report.problems.append(
                f"{len(ledger - active_all)} entries of {what} belong to no active chunk (run gc)"
            )
    return report
