"""Vector store configuration."""

from enum import Enum
from typing import ClassVar, Dict, Optional

from operonx.core.utils import YamlModel


class VectorStoreType(Enum):
    """Supported vector store backends."""

    FAISS = "faiss"  # local index library, no server
    PGVECTOR = "pgvector"  # Postgres extension
    QDRANT = "qdrant"  # standalone vector DB


class VectorStoreMetric(Enum):
    """Similarity metric. Scores follow the metric; ordering is always
    best-match first regardless of whether the metric is a distance or a
    similarity."""

    IP = "ip"  # inner product — higher is better (use with normalised vectors)
    L2 = "l2"  # euclidean distance — lower is better
    COSINE = "cosine"  # cosine similarity — higher is better


class VectorStoreConfig(YamlModel):
    """Configuration for a vector similarity search backend.

    The index is a *derived index* — it stores vectors, ids, and small
    filterable metadata. Document content belongs in your store of
    record; fetch it with ``DocFetchOp``.

    Attributes:
        api_type: Which backend to build.
        metric: Similarity metric used by the index.
        dim: Embedding dimensionality. Required when creating an empty
            in-memory FAISS index; informational otherwise.
        collection: Default collection / table / index name, used when a
            call passes ``collection=None``.
        path: FAISS only — path to a persisted index file.
        collections: FAISS only — ``{name: path}`` for multiple indices,
            selected per call via ``collection=``. Pre-partitioning this
            way is how FAISS approximates filtering, which it otherwise
            does not support.
        dsn: pgvector only — Postgres connection string.
        table: pgvector only — table holding ``(id, embedding, …)``.
        url: Qdrant only — server endpoint.
        api_key: Qdrant only — auth token.
    """

    _category: ClassVar[str] = "vector_store"

    api_type: VectorStoreType = VectorStoreType.FAISS
    metric: VectorStoreMetric = VectorStoreMetric.IP
    dim: Optional[int] = None
    collection: Optional[str] = None

    # FAISS
    path: Optional[str] = None
    collections: Optional[Dict[str, str]] = None

    # pgvector
    dsn: Optional[str] = None
    table: Optional[str] = None

    # Qdrant
    url: Optional[str] = None
    api_key: Optional[str] = None

    @classmethod
    def default(cls) -> "VectorStoreConfig":
        """In-memory FAISS index — no server, no persistence."""
        return cls(
            api_type=VectorStoreType.FAISS,
            metric=VectorStoreMetric.IP,
            dim=1024,
        )
