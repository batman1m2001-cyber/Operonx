"""Resource categories of operonx-kb, registered with operonx's ``REGISTRY``.

``resources.yaml``::

    kb_catalog:main:
      api_type: sqlite
      path: .operonx/kb/catalog.db
    kb_blob:main:
      api_type: local
      root: .operonx/kb/blobs
    vector_store:kb:            # operonx's own category: the derived dense index
      api_type: faiss
      dim: 1024

operonx finds these categories through the ``operonx.resources`` entry points
in ``pyproject.toml`` (the entry point's name is the category and its value
one of the functions below), so a ``kb_catalog:`` key resolves in a process
that never imported ``operonx_kb``. Importing the package registers them too,
the way ``operonx.providers`` registers its own.
"""

from __future__ import annotations

from enum import Enum
from pathlib import Path
from typing import ClassVar

from operonx.core.media_store import LocalMediaStore, MediaStore
from operonx.core.registry import REGISTRY
from operonx.core.utils.yaml_model import YamlModel

from operonx_kb.stores.catalog.base import Catalog
from operonx_kb.stores.catalog.sqlite import SqliteCatalog

__all__ = ["CatalogConfig", "BlobStoreConfig", "register", "register_catalog", "register_blob"]


class CatalogType(str, Enum):
    SQLITE = "sqlite"


class CatalogConfig(YamlModel):
    """``kb_catalog:<name>`` — the store of record.

    Attributes:
        api_type: ``sqlite`` (``postgres`` is a later phase).
        path: SQLite file.
    """

    _category: ClassVar[str] = "kb_catalog"

    api_type: CatalogType = CatalogType.SQLITE
    path: str = ".operonx/kb/catalog.db"


class BlobType(str, Enum):
    LOCAL = "local"


class BlobStoreConfig(YamlModel):
    """``kb_blob:<name>`` — raw files and canonical texts by SHA-256.

    A KB blob store is operonx's :class:`~operonx.core.media_store.MediaStore`;
    ``local`` is :class:`~operonx.core.media_store.LocalMediaStore` used as is
    (content addressed, atomic writes, never expiring — unlike the ClickHouse
    trace media store).
    """

    _category: ClassVar[str] = "kb_blob"

    api_type: BlobType = BlobType.LOCAL
    root: str = ".operonx/kb/blobs"


def create_catalog(config: CatalogConfig) -> Catalog:
    return SqliteCatalog(Path(config.path))


def create_blob_store(config: BlobStoreConfig) -> MediaStore:
    return LocalMediaStore(Path(config.root))


def _register(config_class, factory) -> None:
    if REGISTRY.get_class(config_class._category) is None:
        REGISTRY.register(config_class, factory)


def register_catalog() -> None:
    """Entry point of category ``kb_catalog``."""
    _register(CatalogConfig, create_catalog)


def register_blob() -> None:
    """Entry point of category ``kb_blob``."""
    _register(BlobStoreConfig, create_blob_store)


def register() -> None:
    """Register every category of the package (idempotent)."""
    register_catalog()
    register_blob()


register()
