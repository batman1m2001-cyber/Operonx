"""operonx-kb: versioned documents with span-level provenance, on OperonX.

Read ``PLAN.md`` first. The model lives in :mod:`operonx_kb.model`, the pure
text and span functions in :mod:`operonx_kb.text`, and the structurer in
:mod:`operonx_kb.structure`.
"""

from operonx_kb.errors import (
    CatalogError,
    DocumentParseError,
    KBError,
    MissingExtraError,
    SpanInvariantError,
    UnsupportedFormatError,
)
from operonx_kb.model.collection import (
    ChunkerSpec,
    Collection,
    CollectionSpec,
    DenseIndexSpec,
    LayoutSpec,
)
from operonx_kb.model.document import (
    Chunk,
    Document,
    DocumentVersion,
    Element,
    Page,
    Region,
    VersionChunk,
)

__version__ = "0.1.0"

__all__ = [
    "Chunk",
    "ChunkerSpec",
    "Collection",
    "CollectionSpec",
    "DenseIndexSpec",
    "Document",
    "DocumentVersion",
    "Element",
    "LayoutSpec",
    "Page",
    "Region",
    "VersionChunk",
    "KBError",
    "SpanInvariantError",
    "UnsupportedFormatError",
    "DocumentParseError",
    "CatalogError",
    "MissingExtraError",
]
