"""Vector store backend contract.

A vector store is a **derived index**, not a store of record: it holds
vectors, ids, and small filterable metadata — never document content.
Hydrate content from your primary database with ``DocFetchOp``. See
``OP_TAXONOMY_REFACTOR_PLAN.md`` §5.1 for the reasoning.
"""

from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union

__all__ = ["BaseVectorStore"]


class BaseVectorStore(ABC):
    """Abstract base class for vector similarity search backends.

    Attributes:
        bound: Thread-pool hint consumed by :class:`~operonx.providers.ops.VectorSearchOp`.
            Local index libraries (FAISS) are CPU-bound; network-backed
            stores are I/O-bound. Declared per-backend because the op
            cannot know which it got until the resource resolves.
    """

    __slots__ = []

    #: ``"cpu"`` for in-process index libraries, ``"io"`` for networked stores.
    bound: str = "io"

    @abstractmethod
    async def search(
        self,
        query_vector: Sequence[float],
        top_k: int = 10,
        filter: Optional[Union[Dict[str, Any], str]] = None,
        collection: Optional[str] = None,
    ) -> Tuple[List[Any], List[float], List[Dict[str, Any]]]:
        """Find the ``top_k`` nearest neighbours of ``query_vector``.

        Args:
            query_vector: Query embedding.
            top_k: Number of hits to return.
            filter: **Backend-native** filter — a dict for most backends,
                an expression string for Milvus. Never translated by
                operonx; each backend validates its own dialect and
                raises on shapes it does not recognise. A filter must
                never silently degrade to "no filter" — that is a
                tenant-isolation leak, not a warning.
            collection: Collection / table / index to search. ``None``
                selects the backend's configured default.

        Returns:
            ``(ids, scores, metadata)`` — three equal-length lists,
            index-aligned and ordered best-match first. ``metadata``
            holds only indexed filterable fields; entries are ``{}``
            for backends that store none.
        """

    @abstractmethod
    async def upsert(
        self,
        ids: Sequence[Any],
        vectors: Sequence[Sequence[float]],
        metadata: Optional[Sequence[Dict[str, Any]]] = None,
        collection: Optional[str] = None,
    ) -> None:
        """Insert or replace vectors by id. Exposed as ``VectorUpsertOp``.

        Args:
            ids: Primary keys, one per vector.
            vectors: Embeddings to store.
            metadata: Optional filterable fields, one dict per vector.
            collection: Target collection; ``None`` uses the default.
        """

    async def delete(
        self,
        ids: Optional[Sequence[Any]] = None,
        filter: Optional[Union[Dict[str, Any], str]] = None,
        collection: Optional[str] = None,
    ) -> Optional[int]:
        """Remove vectors by id, or every vector a filter matches.

        Without a delete, an index cannot follow its store of record: a
        document removed there stays findable here. Exposed as
        ``VectorDeleteOp``.

        Pass exactly one of ``ids`` or ``filter``. Neither, both, or an
        empty filter raise rather than guess: each of those could read
        as "delete everything", and a whole index is not something to
        lose to a missing argument. To empty a collection, drop it with
        the backend's own client.

        ``ids=[]`` deletes nothing — it is a batch that happened to be
        empty. Ids the index does not hold are not an error, so a
        cleanup pass that died halfway can simply run again.

        The checks live here and the work in :meth:`_delete`, so no
        backend can get them wrong.

        Args:
            ids: Primary keys to remove.
            filter: **Backend-native** filter, in the same dialect
                :meth:`search` takes. A backend that cannot filter
                raises.
            collection: Collection / table / index; ``None`` uses the
                default.

        Returns:
            How many vectors were removed, or ``None`` when the backend
            does not report it (Qdrant).

        Raises:
            ValueError: Neither or both of ``ids`` and ``filter``, an
                empty filter, or a filter the backend cannot apply.
        """
        if ids is not None and filter is not None:
            raise ValueError(
                "delete() takes ids= or filter=, not both. Delete by ids, or "
                "put the ids into the filter in the backend's own dialect."
            )
        if ids is None and filter is None:
            raise ValueError(
                "delete() needs ids= or filter=. Neither is not read as "
                '"delete everything"; to empty a collection, drop it with '
                "the backend's own client."
            )
        if filter is not None and not filter:
            raise ValueError(
                "delete() refuses an empty filter: it matches every vector. "
                "Pass a filter that selects what to remove."
            )
        if ids is not None:
            if isinstance(ids, (str, bytes)):
                raise TypeError(f"ids must be a list of ids, not {type(ids).__name__}.")
            ids = list(ids)
            if not ids:
                return 0
        return await self._delete(ids=ids, filter=filter, collection=collection)

    @abstractmethod
    async def _delete(
        self,
        ids: Optional[List[Any]],
        filter: Optional[Union[Dict[str, Any], str]],
        collection: Optional[str],
    ) -> Optional[int]:
        """Remove what :meth:`delete` has already checked.

        Exactly one of ``ids`` (a non-empty list) and ``filter`` (a
        non-empty native filter) is set. Validate the filter the way
        :meth:`search` does, and raise on a shape you do not recognise.

        Returns:
            How many vectors were removed, or ``None`` if the backend
            cannot tell.
        """
