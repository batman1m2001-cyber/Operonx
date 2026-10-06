"""The tree index of a version (track5 §9.5, PLAN E5): sections with summaries.

A node is a span of the version's canonical text with a title and a summary.
Its chunks are the version's chunks inside that span, so the tree needs no
index of its own and is versioned with everything else.
"""

from __future__ import annotations

from typing import List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from operonx_kb.model.document import Span

__all__ = ["TreeNode", "NodeSource"]

#: Where a node comes from: the document itself, a heading of the element tree,
#: or a section a model synthesized for a document without headings.
NodeSource = Literal["document", "heading", "toc"]


class TreeNode(BaseModel):
    """One node of a version's tree.

    Attributes:
        id: ``H(version_id, path)``.
        path: Dotted ordinal path from the root (``"0"``).
        parent_id: ``None`` for the root (the document).
        depth: 0 for the root.
        title: The heading, the synthesized title, or the document's title.
        span: Into the canonical text.
        pages: Pages the span covers (empty for sources without pages).
        summary: What the node is about, written by the tree's model.
        summary_sha: The input hash its summary was written from (its cache key).
    """

    model_config = ConfigDict(extra="forbid")

    id: str
    version_id: str
    path: str
    parent_id: Optional[str] = None
    ordinal: int
    depth: int
    title: str
    span: Span
    pages: List[int] = Field(default_factory=list)
    source: NodeSource
    summary: str = ""
    summary_sha: Optional[str] = None
