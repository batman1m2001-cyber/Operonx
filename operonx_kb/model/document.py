"""The document model (track5 §5): pure pydantic, no I/O.

A :class:`Document` is a logical, stable identity. Each ingest of new bytes (or
of the same bytes through a changed pipeline) makes a :class:`DocumentVersion`,
immutable once committed. A version has one canonical text, an element tree
whose spans point into it, pages, and the occurrences of its chunks
(:class:`VersionChunk`). A :class:`Chunk` is content-addressed and shared by
every version that contains the same text.

**The invariant**: for every body element ``canonical[e.span[0]:e.span[1]] ==
e.text``; every chunk span lies inside the canonical text; a chunk's
``content_sha`` is the hash of its span texts. See
:func:`operonx_kb.text.spans.check_version`.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Literal, Optional, Tuple

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "Span",
    "ElementKind",
    "CONTAINER_KINDS",
    "FURNITURE_KINDS",
    "Region",
    "Element",
    "Page",
    "Document",
    "DocumentVersion",
    "VersionStatus",
    "Chunk",
    "ChunkKind",
    "VersionChunk",
    "utcnow",
]

#: ``(start, end)`` character offsets into a version's canonical text, end exclusive.
Span = Tuple[int, int]

ElementKind = Literal[
    "document",
    "section",
    "title",
    "heading",
    "paragraph",
    "list",
    "list_item",
    "table",
    "figure",
    "caption",
    "formula",
    "code",
    "footnote",
    "kv",
    "page_header",
    "page_footer",
]

#: Kinds whose span is derived from their descendants (first start to last end).
CONTAINER_KINDS = frozenset({"document", "section", "list"})
#: Kinds that live in the furniture layer: kept in the tree, absent from canonical text.
FURNITURE_KINDS = frozenset({"page_header", "page_footer"})

VersionStatus = Literal["staged", "committed", "failed", "superseded"]
ChunkKind = Literal["text", "table", "figure", "evidence_unit", "page"]


def utcnow() -> datetime:
    """Timezone-aware now, the only clock the model uses."""
    return datetime.now(timezone.utc)


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Region(_Model):
    """Where on a page.

    Attributes:
        page_no: 1-based page number.
        bbox: ``(x0, y0, x1, y1)`` normalised to ``[0, 1]``, origin top-left.
        char_span: The part of the owning element's span this region covers,
            when an element spans several regions (a paragraph across columns).
    """

    page_no: int
    bbox: Tuple[float, float, float, float]
    char_span: Optional[Span] = None


class Element(_Model):
    """A node of a version's element tree.

    Attributes:
        id: ``H(version_id, path)``, positional within one version.
        content_sha: ``H(kind, normalised text, structural attrs)``; equal across
            versions for an unchanged element, which is what the version diff uses.
        path: Dotted ordinal path from the root (``"0"`` is the root).
        span: Into the canonical text. ``None`` only for furniture.
        text: Exactly ``canonical[span[0]:span[1]]`` for body elements.
        regions: Page regions; empty for sources without pages (HTML, Markdown).
        attrs: Kind-specific data. Tables: ``rows`` (list of rows of cell text),
            ``header_rows``, ``cell_spans``; list items: ``ordered``, ``marker``;
            code: ``lang``; captions: ``target`` (element id); source anchors such
            as ``sheet`` or ``slide``.
    """

    id: str
    content_sha: str
    version_id: str
    parent_id: Optional[str] = None
    path: str
    ordinal: int
    depth: int
    kind: ElementKind
    layer: Literal["body", "furniture"] = "body"
    level: Optional[int] = None
    text: str
    span: Optional[Span] = None
    regions: List[Region] = Field(default_factory=list)
    attrs: Dict[str, Any] = Field(default_factory=dict)
    confidence: Optional[float] = None


class Page(_Model):
    """One page of a paginated source.

    Attributes:
        page_no: 1-based.
        width, height: In ``unit``; regions are normalised against them.
        text_layer: False when the page's text came from OCR.
    """

    version_id: str
    page_no: int
    width: float
    height: float
    unit: Literal["pt", "px"] = "pt"
    image_sha: Optional[str] = None
    text_layer: bool = True


class Document(_Model):
    """A logical document: stable identity across versions.

    ``active_version_id`` is ``None`` before the first commit and after a delete
    (tombstone), which makes the document invisible at once.

    Attributes:
        acl: Principals allowed to read it (``KBFilter.acl_any``); empty means
            no ACL filter matches it.
    """

    id: str
    collection_id: str
    key: str
    title: Optional[str] = None
    mime: str
    tags: List[str] = Field(default_factory=list)
    acl: List[str] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)
    active_version_id: Optional[str] = None
    deleted_at: Optional[datetime] = None
    created_at: datetime = Field(default_factory=utcnow)


class DocumentVersion(_Model):
    """One parse of one set of source bytes. Immutable once committed.

    Attributes:
        raw_sha: SHA-256 of the source bytes, also their blob key.
        text_sha: SHA-256 of the canonical text, also its blob key.
        pipeline_fp: Combined fingerprint of parser, layout, structurer,
            serializer and chunker.
        stats: Counts and timings (pages, elements, chunks, new/reused/removed).
    """

    id: str
    document_id: str
    ordinal: int
    raw_sha: str
    text_sha: str
    pipeline_fp: str
    status: VersionStatus = "staged"
    stats: Dict[str, Any] = Field(default_factory=dict)
    error: Optional[str] = None
    created_at: datetime = Field(default_factory=utcnow)


class Chunk(_Model):
    """A retrievable unit, identified by its content (stable across versions).

    Attributes:
        id: ``H(document_id, chunker_fp, content_sha, occurrence)``.
        content_sha: Hash of the chunk text (its span texts joined).
        text: The chunk text, what a user sees when it is cited.
        embed_text: What is embedded: the heading path then the text.
        embed_text_sha: Hash of ``embed_text``, the embedding-cache key with the
            embedder fingerprint.
    """

    id: str
    document_id: str
    content_sha: str
    kind: ChunkKind = "text"
    heading_path: List[str] = Field(default_factory=list)
    token_count: int
    text: str
    embed_text: str
    embed_text_sha: str


class VersionChunk(_Model):
    """Where a chunk sits in one version: offsets move between versions, ids do not.

    ``spans`` may be non-contiguous (an evidence unit: a table, its caption and
    the paragraph that refers to it).
    """

    version_id: str
    chunk_id: str
    ordinal: int
    spans: List[Span]
    element_ids: List[str]
    pages: List[int] = Field(default_factory=list)
