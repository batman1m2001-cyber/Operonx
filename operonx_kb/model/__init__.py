"""The document model: pure pydantic, no I/O (track5 §5)."""

from operonx_kb.model.collection import (
    ChunkerSpec,
    Collection,
    CollectionSpec,
    DenseIndexSpec,
    LayoutSpec,
)
from operonx_kb.model.document import (
    CONTAINER_KINDS,
    FURNITURE_KINDS,
    Chunk,
    Document,
    DocumentVersion,
    Element,
    ElementKind,
    Page,
    Region,
    Span,
    VersionChunk,
)

__all__ = [
    "CONTAINER_KINDS",
    "FURNITURE_KINDS",
    "Chunk",
    "ChunkerSpec",
    "Collection",
    "CollectionSpec",
    "DenseIndexSpec",
    "Document",
    "DocumentVersion",
    "Element",
    "ElementKind",
    "LayoutSpec",
    "Page",
    "Region",
    "Span",
    "VersionChunk",
]
