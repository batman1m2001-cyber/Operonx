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
        """Insert or replace vectors by id.

        Declared on the contract now so backends are written against the
        full surface, but no op exposes it yet — ``VectorUpsertOp``
        ships when the agent-memory work concretely needs it (plan §5.7).

        Args:
            ids: Primary keys, one per vector.
            vectors: Embeddings to store.
            metadata: Optional filterable fields, one dict per vector.
            collection: Target collection; ``None`` uses the default.
        """
