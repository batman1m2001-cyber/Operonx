"""Factory for creating vector store backends.

Each backend is **lazy-imported** inside its dispatch branch so that
``import operonx.providers.vector_stores`` doesn't pull in faiss /
psycopg / qdrant-client unless the user actually instantiates the
corresponding backend.
"""

from operonx.providers.vector_stores.base import BaseVectorStore
from operonx.providers.vector_stores.config import VectorStoreConfig, VectorStoreType


def create_vector_store(config: VectorStoreConfig) -> BaseVectorStore:
    """Create a vector store backend from config.

    Args:
        config: VectorStoreConfig whose api_type selects the backend.

    Returns:
        BaseVectorStore instance.

    Raises:
        ValueError: If api_type is unsupported.
        ImportError: With a pointer to the right ``operonx[<extra>]``
            install when an optional dependency is missing.
    """
    if config.api_type == VectorStoreType.FAISS:
        try:
            from operonx.providers.vector_stores.faiss import FaissVectorStore
        except ImportError as e:
            raise ImportError(_missing_extra_message("FaissVectorStore", "faiss", e)) from e
        return FaissVectorStore(config)
    if config.api_type == VectorStoreType.PGVECTOR:
        try:
            from operonx.providers.vector_stores.pgvector import PgVectorStore
        except ImportError as e:
            raise ImportError(_missing_extra_message("PgVectorStore", "pgvector", e)) from e
        return PgVectorStore(config)
    if config.api_type == VectorStoreType.QDRANT:
        try:
            from operonx.providers.vector_stores.qdrant import QdrantVectorStore
        except ImportError as e:
            raise ImportError(_missing_extra_message("QdrantVectorStore", "qdrant", e)) from e
        return QdrantVectorStore(config)
    raise ValueError(f"Unsupported vector store: {config.api_type}")


def _missing_extra_message(backend: str, extra: str, exc: ImportError) -> str:
    return (
        f"{backend} requires additional packages.\n"
        f"  Install with: pip install operonx[{extra}]\n"
        f"  Original error: {exc}"
    )
