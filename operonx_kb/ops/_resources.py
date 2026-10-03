"""Resolving resource keys inside ops.

Ops take resource *keys* (``"kb_catalog:main"``), never objects: inputs and
outputs are traced as JSON, and a key is what Studio can show and an operator
can repoint in ``resources.yaml``. A bare name gets the expected category, the
convention of operonx's ``VectorSearchOp``.
"""

from __future__ import annotations

from typing import Any

from operonx.core.media_store import MediaStore
from operonx.core.registry import ResourceHub
from operonx.providers.vector_stores.base import BaseVectorStore

from operonx_kb.stores.catalog.base import Catalog

__all__ = ["resolve", "full_key", "catalog_of", "blobs_of", "vector_store_of"]


def full_key(key: str, category: str) -> str:
    """``key`` with its category: a bare name is ``<category>:<name>``."""
    return key if ":" in key else f"{category}:{key}"


def resolve(key: str, category: str) -> Any:
    """The resource ``key``; a bare name is looked up as ``<category>:<name>``."""
    return ResourceHub.instance().get(full_key(key, category))


def _expect(key: str, category: str, kind: type, what: str) -> Any:
    value = resolve(key, category)
    if not isinstance(value, kind):
        raise TypeError(f"resource {key!r} is a {type(value).__name__}, not {what}")
    return value


def catalog_of(key: str) -> Catalog:
    return _expect(key, "kb_catalog", Catalog, "a KB catalog")


def blobs_of(key: str) -> MediaStore:
    return _expect(key, "kb_blob", MediaStore, "a MediaStore blob store")


def vector_store_of(key: str) -> BaseVectorStore:
    return _expect(key, "vector_store", BaseVectorStore, "an operonx vector store")
