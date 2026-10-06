"""operonx-kb: versioned documents with span-level provenance, on OperonX.

Read ``PLAN.md`` first. The model lives in :mod:`operonx_kb.model`, the pure
text and span functions in :mod:`operonx_kb.text`, the structurer in
:mod:`operonx_kb.structure`, parsers in :mod:`operonx_kb.parsing` and
:mod:`operonx_kb.pdf`, the ops and graphs in :mod:`operonx_kb.ops` and
:mod:`operonx_kb.graphs`, and the library API in :class:`KnowledgeBase`.

Its resource categories (``kb_catalog``, ``kb_blob``) reach operonx through
``operonx.resources`` entry points, and importing the package registers them too.
"""

from operonx_kb import registry as _registry  # noqa: F401 — registers kb_* categories
from operonx_kb.errors import (
    CatalogError,
    DocumentParseError,
    KBError,
    MissingExtraError,
    SpanInvariantError,
    UnsupportedFormatError,
)
from operonx_kb.kb import DEFAULT_MODE, IngestError, KnowledgeBase, QueryError
from operonx_kb.model.collection import (
    ChunkerSpec,
    Collection,
    CollectionSpec,
    ContextualSpec,
    DenseIndexSpec,
    GraphSpec,
    LayoutSpec,
    OcrSpec,
    TreeSpec,
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

__version__ = "0.2.3"

__all__ = [
    "Chunk",
    "ChunkerSpec",
    "Collection",
    "CollectionSpec",
    "ContextualSpec",
    "DenseIndexSpec",
    "Document",
    "DocumentVersion",
    "Element",
    "LayoutSpec",
    "Page",
    "Region",
    "TreeSpec",
    "GraphSpec",
    "OcrSpec",
    "VersionChunk",
    "DEFAULT_MODE",
    "IngestError",
    "QueryError",
    "KnowledgeBase",
    "KBError",
    "SpanInvariantError",
    "UnsupportedFormatError",
    "DocumentParseError",
    "CatalogError",
    "MissingExtraError",
]
