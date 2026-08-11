"""Document store providers — the *store of record*.

Where the vector index is derived data you can rebuild, this is the
source of truth. ``DocFetchOp`` reads it to hydrate the ids that
``VectorSearchOp`` returns. See ``OP_TAXONOMY_REFACTOR_PLAN.md`` §5.8.

Light symbols are eager; backend classes are **lazy-loaded** via
module-level ``__getattr__`` so importing this package doesn't require
psycopg / motor / redis unless the backend is used.
"""

from operonx.providers.doc_stores._reorder import partition_by_ids, reorder_by_ids
from operonx.providers.doc_stores.base import BaseDocStore
from operonx.providers.doc_stores.config import DocStoreConfig, DocStoreType
from operonx.providers.doc_stores.factory import create_doc_store
from operonx.providers.doc_stores.memory import MemoryDocStore

_LAZY_BACKENDS = {
    "PostgresDocStore": "operonx.providers.doc_stores.postgres",
    "MongoDocStore": "operonx.providers.doc_stores.mongo",
    "RedisDocStore": "operonx.providers.doc_stores.redis",
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
    "BaseDocStore",
    "DocStoreConfig",
    "DocStoreType",
    "create_doc_store",
    "MemoryDocStore",
    "reorder_by_ids",
    "partition_by_ids",
    "PostgresDocStore",
    "MongoDocStore",
    "RedisDocStore",
]
