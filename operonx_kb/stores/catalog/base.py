"""The catalog: the store of record (track5 §12.1).

Everything an index holds can be rebuilt from here plus the blob store. The
contract is a set of repository methods plus one transactional
:meth:`Catalog.commit_version`, which is where a version becomes visible: it
inserts the version's rows and flips ``active_version_id`` in a single
transaction, so a crash at any point leaves the catalog consistent (§11.2).

There is no ORM and no query language here, on purpose — the same boundary
operonx draws around ``BaseDocStore``.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple

from operonx_kb.model.collection import Collection
from operonx_kb.model.document import Chunk, Document, DocumentVersion, Element, Page, VersionChunk

__all__ = ["Catalog", "CommitResult", "PurgeResult", "ActiveChunk"]


@dataclass
class CommitResult:
    """What a commit changed.

    Attributes:
        committed: False when this exact version was already the active one
            (an idempotent retry): nothing was written.
        previous_version_id: The version that was active before, now superseded.
        removed_chunk_ids: Chunks of the previous version that the new one no
            longer contains; their index entries are garbage after the flip.
        added_chunk_ids: Chunks of the new version the previous one did not have.
    """

    committed: bool
    previous_version_id: Optional[str] = None
    removed_chunk_ids: List[str] = field(default_factory=list)
    added_chunk_ids: List[str] = field(default_factory=list)


@dataclass
class PurgeResult:
    """What a purge removed."""

    chunk_ids: List[str] = field(default_factory=list)
    version_ids: List[str] = field(default_factory=list)
    orphan_blobs: List[str] = field(default_factory=list)


@dataclass
class ActiveChunk:
    """A chunk as the active version of a live document holds it: what hydration returns."""

    chunk: Chunk
    occurrence: VersionChunk
    document: Document


class Catalog(ABC):
    """The store of record. Implementations: :class:`~operonx_kb.stores.catalog.sqlite.SqliteCatalog`."""

    # collections -----------------------------------------------------------

    @abstractmethod
    def put_collection(self, collection: Collection) -> None:
        """Create or update a collection (its spec)."""

    @abstractmethod
    def get_collection(self, collection_id: str) -> Optional[Collection]: ...

    @abstractmethod
    def list_collections(self) -> List[Collection]: ...

    # documents and versions --------------------------------------------------

    @abstractmethod
    def get_document(self, document_id: str) -> Optional[Document]: ...

    @abstractmethod
    def list_documents(
        self, collection_id: str, include_deleted: bool = False
    ) -> List[Document]: ...

    @abstractmethod
    def get_version(self, version_id: str) -> Optional[DocumentVersion]: ...

    @abstractmethod
    def list_versions(self, document_id: str) -> List[DocumentVersion]: ...

    @abstractmethod
    def elements(self, version_id: str, canonical: str) -> List[Element]:
        """The version's element tree, texts filled in from ``canonical``."""

    @abstractmethod
    def pages(self, version_id: str) -> List[Page]: ...

    @abstractmethod
    def version_chunks(self, version_id: str) -> List[VersionChunk]:
        """Chunk occurrences of a version, in order."""

    @abstractmethod
    def get_chunks(self, chunk_ids: Sequence[str]) -> Dict[str, Chunk]: ...

    @abstractmethod
    def active_chunk_ids(
        self, collection_id: Optional[str] = None, document_id: Optional[str] = None
    ) -> Set[str]:
        """Chunks referenced by an active version of a live document."""

    @abstractmethod
    def active_chunks(self, collection_id: str, chunk_ids: Sequence[str]) -> Dict[str, ActiveChunk]:
        """The hydration gate (track5 §11.2): of ``chunk_ids``, those an active version of a
        live document of ``collection_id`` holds, with that occurrence and document.

        A chunk that is gone, superseded, tombstoned or in another collection is absent.
        """

    # writes ---------------------------------------------------------------------

    @abstractmethod
    def commit_version(
        self,
        document: Document,
        version: DocumentVersion,
        elements: Sequence[Element],
        pages: Sequence[Page],
        chunks: Sequence[Chunk],
        occurrences: Sequence[VersionChunk],
    ) -> CommitResult:
        """In one transaction: upsert the document, insert the version and its rows,
        make it active, and mark the previous active version superseded.

        Idempotent: committing the version that is already active writes nothing.

        Raises:
            CatalogError: The collection does not exist, or the version id is
                committed under another document.
        """

    @abstractmethod
    def tombstone(self, document_id: str) -> List[str]:
        """Mark a document deleted (invisible at once); return its formerly active chunk ids."""

    @abstractmethod
    def purge(self, document_id: str) -> PurgeResult:
        """Delete a document and every row derived from it; report blobs no version references any more."""

    @abstractmethod
    def blob_refs(self) -> Set[str]:
        """Every blob sha some version references (raw bytes and canonical text)."""

    # the derived-index ledger ----------------------------------------------------

    @abstractmethod
    def record_index_entries(
        self,
        store: str,
        collection: str,
        collection_id: str,
        entries: Sequence[Tuple[str, int, str]],
    ) -> None:
        """Record ``(chunk_id, vector_id, document_id)`` rows about to be written to a vector store.

        Called *before* the upsert, so the ledger always covers the index.

        Raises:
            CatalogError: A vector id is already recorded for a different chunk
                (a 63-bit key collision); writing it would overwrite that chunk.
        """

    @abstractmethod
    def forget_index_entries(self, store: str, collection: str, chunk_ids: Sequence[str]) -> int:
        """Drop ledger rows after their vectors were deleted; return how many."""

    @abstractmethod
    def index_entries(
        self,
        store: str,
        collection: str,
        *,
        collection_id: Optional[str] = None,
        document_id: Optional[str] = None,
    ) -> Dict[str, int]:
        """``chunk_id -> vector_id`` recorded for a vector store collection."""

    @abstractmethod
    def chunks_for_keys(
        self, store: str, collection: str, collection_id: str, keys: Sequence[int]
    ) -> Dict[int, str]:
        """``key -> chunk_id`` for the keys the ledger records in this store and
        collection for ``collection_id``; other keys are absent."""

    # caches and log ---------------------------------------------------------------

    @abstractmethod
    def get_embeddings(
        self, embedder_fp: str, text_shas: Iterable[str]
    ) -> Dict[str, List[float]]: ...

    @abstractmethod
    def put_embeddings(self, embedder_fp: str, vectors: Dict[str, List[float]]) -> None: ...

    @abstractmethod
    def log_ingest(
        self,
        collection_id: str,
        key: str,
        action: str,
        *,
        document_id: Optional[str] = None,
        version_id: Optional[str] = None,
        stats: Optional[dict] = None,
        error: Optional[str] = None,
    ) -> None: ...

    @abstractmethod
    def ingest_log(self, collection_id: str, key: Optional[str] = None) -> List[dict]: ...
