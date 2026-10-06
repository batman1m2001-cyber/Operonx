"""Resolving resource keys inside ops.

Ops take resource *keys* (``"kb_catalog:main"``), never objects: inputs and
outputs are traced as JSON, and a key is what Studio can show and an operator
can repoint in ``resources.yaml``. A bare name gets the expected category, the
convention of operonx's ``VectorSearchOp``.
"""

from __future__ import annotations

from typing import Any, Dict

from operonx.core.media_store import MediaStore
from operonx.core.registry import ResourceHub
from operonx.providers.vector_stores.base import BaseVectorStore

from operonx_kb.stores.catalog.base import Catalog
from operonx_kb.stores.lexical.base import LexicalIndex

__all__ = [
    "resolve",
    "full_key",
    "catalog_of",
    "blobs_of",
    "vector_store_of",
    "lexical_of",
    "backend_settings",
    "llm_fingerprint",
]

_SECRET_HINTS = ("key", "token", "secret", "password", "url", "header")


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


def lexical_of(key: str) -> LexicalIndex:
    return _expect(key, "kb_lexical", LexicalIndex, "a KB lexical index")


def backend_settings(backend: Any) -> Dict[str, Any]:
    """A model backend's config as it shapes outputs: credentials and endpoints left
    out, so rotating a key or moving a gateway changes no fingerprint."""
    config = getattr(backend, "config", None)
    if config is None or not hasattr(config, "model_dump"):
        return {}
    return {
        k: v
        for k, v in config.model_dump(mode="json").items()
        if v is not None and not any(h in k.lower() for h in _SECRET_HINTS)
    }


def llm_fingerprint(name: str) -> str:
    """The fingerprint of the ``llm:<name>`` resource's model (PLAN E1)."""
    from operonx_kb.model.ids import fingerprint

    backend = resolve(f"llm:{name}", "llm")
    cls = type(backend)
    return fingerprint(f"{cls.__module__}.{cls.__qualname__}", "1", backend_settings(backend))
