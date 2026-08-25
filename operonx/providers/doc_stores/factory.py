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
    if config.api_type == DocStoreType.MEMORY:
        # No optional dependency — always importable.
        from operonx.providers.doc_stores.memory import MemoryDocStore

        return MemoryDocStore(config)
    if config.api_type == DocStoreType.POSTGRES:
        try:
            from operonx.providers.doc_stores.postgres import PostgresDocStore
        except ImportError as e:
            raise ImportError(_missing_extra_message("PostgresDocStore", "postgres", e)) from e
        return PostgresDocStore(config)
    # MONGO and REDIS are declared in DocStoreType but no backend module
    # exists yet. Reporting them as a missing *extra* sent users to
    # ``pip install operonx[mongo]`` — an extra that has never existed — so
    # the install appeared to be the fix for something no install provides.
    if config.api_type in (DocStoreType.MONGO, DocStoreType.REDIS):
        raise NotImplementedError(
            f"Doc store backend '{config.api_type.value}' is declared in "
            f"DocStoreType but not implemented. Available today: "
            f"{DocStoreType.MEMORY.value}, {DocStoreType.POSTGRES.value}."
        )
    raise ValueError(f"Unsupported doc store: {config.api_type}")


def _missing_extra_message(backend: str, extra: str, exc: ImportError) -> str:
    return (
        f"{backend} requires additional packages.\n"
        f"  Install with: pip install operonx[{extra}]\n"
        f"  Original error: {exc}"
    )
