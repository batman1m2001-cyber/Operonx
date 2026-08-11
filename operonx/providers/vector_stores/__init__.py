"""Vector store providers for operonx workflows.

A vector store here is a **derived index**: vectors, ids, and small
filterable metadata. It never holds document content — hydrate that from
your store of record with ``DocFetchOp``. See
``OP_TAXONOMY_REFACTOR_PLAN.md`` §5.1.

Light symbols (``BaseVectorStore``, ``VectorStoreConfig``,
``VectorStoreType``, ``VectorStoreMetric``, ``create_vector_store``) are
imported eagerly — they have no optional dependencies. Backend classes
are **lazy-loaded** via module-level ``__getattr__`` so importing this
package doesn't require faiss / psycopg / qdrant-client unless the
corresponding backend is actually used.
"""

from operonx.providers.vector_stores.base import BaseVectorStore
from operonx.providers.vector_stores.config import (
    VectorStoreConfig,
    VectorStoreMetric,
    VectorStoreType,
)
from operonx.providers.vector_stores.factory import create_vector_store

_LAZY_BACKENDS = {
    "FaissVectorStore": "operonx.providers.vector_stores.faiss",
    "PgVectorStore": "operonx.providers.vector_stores.pgvector",
    "QdrantVectorStore": "operonx.providers.vector_stores.qdrant",
}


def __getattr__(name: str):
    """Lazy attribute loading for backend classes (PEP 562)."""
    if name in _LAZY_BACKENDS:
        import importlib

        module = importlib.import_module(_LAZY_BACKENDS[name])
        cls = getattr(module, name)
        globals()[name] = cls  # cache for subsequent access
        return cls
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "BaseVectorStore",
    "VectorStoreConfig",
    "VectorStoreType",
    "VectorStoreMetric",
    "create_vector_store",
    "FaissVectorStore",
    "PgVectorStore",
    "QdrantVectorStore",
]
