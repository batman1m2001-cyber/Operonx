"""Factory for creating document store backends.

Each backend is **lazy-imported** inside its dispatch branch so that
``import operonx.providers.doc_stores`` doesn't pull in psycopg / motor /
redis unless the corresponding backend is actually instantiated.
"""

from operonx.providers.doc_stores.base import BaseDocStore
from operonx.providers.doc_stores.config import DocStoreConfig, DocStoreType


def create_doc_store(config: DocStoreConfig) -> BaseDocStore:
    """Create a document store backend from config.

    Args:
        config: DocStoreConfig whose api_type selects the backend.

    Returns:
        BaseDocStore instance.

    Raises:
        ValueError: If api_type is unsupported.
        ImportError: With a pointer to the right ``operonx[<extra>]``
            install when an optional dependency is missing.
    """
    if config.api_type == DocStoreType.POSTGRES:
        try:
            from operonx.providers.doc_stores.postgres import PostgresDocStore
        except ImportError as e:
            raise ImportError(_missing_extra_message("PostgresDocStore", "postgres", e)) from e
        return PostgresDocStore(config)
    if config.api_type == DocStoreType.MONGO:
        try:
            from operonx.providers.doc_stores.mongo import MongoDocStore
        except ImportError as e:
            raise ImportError(_missing_extra_message("MongoDocStore", "mongo", e)) from e
        return MongoDocStore(config)
    if config.api_type == DocStoreType.REDIS:
        try:
            from operonx.providers.doc_stores.redis import RedisDocStore
        except ImportError as e:
            raise ImportError(_missing_extra_message("RedisDocStore", "redis", e)) from e
        return RedisDocStore(config)
    raise ValueError(f"Unsupported doc store: {config.api_type}")


def _missing_extra_message(backend: str, extra: str, exc: ImportError) -> str:
    return (
        f"{backend} requires additional packages.\n"
        f"  Install with: pip install operonx[{extra}]\n"
        f"  Original error: {exc}"
    )
