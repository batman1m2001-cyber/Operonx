"""The chunker contract and turning drafts into content-addressed chunks.

A chunker returns :class:`ChunkDraft`\\ s — spans into the canonical text plus
the elements they cover. :func:`materialize` gives each draft its stable id
(``H(document, chunker_fp, content_sha, occurrence)``) and its per-version
occurrence, which is what makes an unchanged paragraph keep its chunk id,
embedding and index entries across versions (track5 §5.3).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

from operonx_kb.model.document import CONTAINER_KINDS, Chunk, Element, Span, VersionChunk
from operonx_kb.model.ids import chunk_id, fingerprint, sha256_text
from operonx_kb.structure.build import VersionTree
from operonx_kb.text.spans import chunk_text, elements_in_span, merge_spans
from operonx_kb.text.tokenize import RegexTokenizer, Tokenizer

__all__ = ["ChunkDraft", "Chunker", "materialize", "heading_paths", "section_of", "embed_text"]


@dataclass
class ChunkDraft:
    """A chunk before it has an id."""

    spans: List[Span]
    kind: str = "text"
    heading_path: List[str] = field(default_factory=list)
    element_ids: List[str] = field(default_factory=list)
    #: The section the chunk belongs to; chunks of different sections never merge,
    #: even when their headings read the same.
    scope: str = ""


class Chunker(ABC):
    """Splits a version into chunks.

    Args:
        max_tokens: Budget per chunk.
        heading_context: Prefix the embedded text with the heading path.
        tokenizer: Counts tokens; default :class:`RegexTokenizer`.
    """

    name: str = ""
    version: str = "1"

    def __init__(
        self,
        max_tokens: int = 400,
        heading_context: bool = True,
        tokenizer: Optional[Tokenizer] = None,
    ):
        self.max_tokens = max_tokens
        self.heading_context = heading_context
        self.tokenizer = tokenizer or RegexTokenizer()

    def config(self) -> Dict[str, Any]:
        return {
            "max_tokens": self.max_tokens,
            "heading_context": self.heading_context,
            "tokenizer": self.tokenizer.fingerprint(),
        }

    def fingerprint(self) -> str:
        cls = type(self)
        return fingerprint(f"{cls.__module__}.{cls.__qualname__}", self.version, self.config())

    @abstractmethod
    def draft(self, tree: VersionTree) -> List[ChunkDraft]:
        """Chunk drafts in document order; spans must be non-empty and inside the canonical text."""


def section_of(tree: VersionTree) -> Dict[str, str]:
    """Element id → id of its nearest enclosing section (the root when there is none)."""
    by_id = tree.by_id()
    out: Dict[str, str] = {}
    for e in tree.elements:
        node = by_id.get(e.parent_id) if e.parent_id else None
        while node is not None and node.kind != "section" and node.parent_id:
            node = by_id.get(node.parent_id)
        out[e.id] = node.id if node is not None else e.id
    return out


def heading_paths(tree: VersionTree) -> Dict[str, List[str]]:
    """Element id → the heading texts of its enclosing sections (the title element first)."""
    by_id = tree.by_id()
    heading_of: Dict[str, str] = {}
    for e in tree.elements:
        if e.kind == "heading" and e.parent_id and by_id[e.parent_id].kind == "section":
            heading_of.setdefault(e.parent_id, e.text)
    out: Dict[str, List[str]] = {}
    title = next((e.text for e in tree.elements if e.kind == "title"), None)
    prefix = [title] if title else []
    for e in tree.elements:
        path: List[str] = []
        node = by_id.get(e.parent_id) if e.parent_id else None
        while node is not None:
            if node.id in heading_of:
                path.append(heading_of[node.id])
            node = by_id.get(node.parent_id) if node.parent_id else None
        out[e.id] = prefix + list(reversed(path))
    return out


def embed_text(heading_path: List[str], text: str, heading_context: bool) -> str:
    """What gets embedded: the heading path, a blank line, the chunk text."""
    if heading_context and heading_path:
        return " > ".join(heading_path) + "\n\n" + text
    return text


def leaves(tree: VersionTree) -> List[Element]:
    """Body elements that hold text of their own, in canonical order."""
    return [
        e
        for e in tree.elements
        if e.layer == "body"
        and e.kind not in CONTAINER_KINDS
        and e.span is not None
        and e.span[1] > e.span[0]
    ]


def materialize(
    tree: VersionTree,
    drafts: List[ChunkDraft],
    *,
    document_id: str,
    version_id: str,
    chunker: Chunker,
    contexts: Optional[Sequence[str]] = None,
) -> Tuple[List[Chunk], List[VersionChunk]]:
    """Ids, texts and occurrences for ``drafts``.

    Identical chunk texts in one document are told apart by their occurrence
    number (first, second, …), so an edit elsewhere does not change their ids.
    ``contexts`` holds, per draft, what its embedded text depends on besides its
    own text (its contextual enrichment's input, PLAN E3); it enters the id.
    """
    chunker_fp = chunker.fingerprint()
    seen: Counter = Counter()
    chunks: List[Chunk] = []
    occurrences: List[VersionChunk] = []
    pages_of = {e.id: sorted({r.page_no for r in e.regions}) for e in tree.elements}
    leaf_elements = leaves(tree)
    for ordinal, draft in enumerate(drafts):
        spans = [s for s in draft.spans if s[1] > s[0]]
        text = chunk_text(tree.canonical, spans)
        content_sha = sha256_text(text)
        occurrence = seen[content_sha]
        seen[content_sha] += 1
        cid = chunk_id(
            document_id, chunker_fp, content_sha, occurrence,
            contexts[ordinal] if contexts is not None else None,
        )  # fmt: skip
        element_ids = draft.element_ids or [
            e.id for span in spans for e in elements_in_span(leaf_elements, span)
        ]
        element_ids = list(dict.fromkeys(element_ids))
        etext = embed_text(draft.heading_path, text, chunker.heading_context)
        chunks.append(
            Chunk(
                id=cid,
                document_id=document_id,
                content_sha=content_sha,
                kind=draft.kind,
                heading_path=draft.heading_path,
                token_count=chunker.tokenizer.count(text),
                text=text,
                embed_text=etext,
                embed_text_sha=sha256_text(etext),
            )
        )
        occurrences.append(
            VersionChunk(
                version_id=version_id,
                chunk_id=cid,
                ordinal=ordinal,
                spans=spans,
                element_ids=element_ids,
                pages=sorted({p for eid in element_ids for p in pages_of.get(eid, [])}),
            )
        )
    return chunks, occurrences


def contiguous(spans: List[Span], canonical: str) -> List[Span]:
    """Merge spans separated only by whitespace and list or heading markup."""
    out: List[Span] = []
    for span in merge_spans(spans):
        if out:
            between = canonical[out[-1][1] : span[0]]
            if between.strip(" \n-*0123456789.)#") == "":
                out[-1] = (out[-1][0], span[1])
                continue
        out.append(span)
    return out
