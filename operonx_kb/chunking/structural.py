"""The structural chunker (the default).

The algorithm of docling's HybridChunker (``docs/concepts/chunking.md``), on our
element tree:

1. Every leaf is a unit, carrying its heading path. Headings and the title are
   context, not content: they reach the embedding through the heading path.
2. Consecutive units of the same section are packed greedily up to
   ``max_tokens`` (docling's ``merge_peers``); units of different sections
   never share a chunk, even when their headings read the same.
3. A unit over the budget is split at sentence boundaries (sentences packed
   up to the budget), and a sentence over the budget at word boundaries. Its
   pieces are chunks of their own, never packed with neighbouring units, so an
   edit inside it does not move the boundaries of the chunks around it.
4. Tables and figures are **evidence units** (arXiv 2604.00500): the table or
   figure, its caption, and the paragraph of the same section that refers to it
   by label ("Table 1"), as one chunk with non-contiguous spans. A table over
   the budget is split by row groups, each repeating the header row
   (docling's ``repeat_table_header``).
"""

from __future__ import annotations

import re
from typing import Dict, List, Optional, Set, Tuple

from operonx_kb.chunking.base import (
    ChunkDraft,
    Chunker,
    contiguous,
    heading_paths,
    leaves,
    section_of,
)
from operonx_kb.model.document import Element, Span
from operonx_kb.structure.build import VersionTree
from operonx_kb.text.sentences import sentence_spans

__all__ = ["StructuralChunker"]

_LABEL = re.compile(
    r"^((?:fig(?:ure)?|tab(?:le)?|chart|exhibit|listing|bảng|hình)\.?\s*[\dIVX]+[a-z]?)", re.I
)
_CONTEXT_KINDS = frozenset({"title", "heading"})


class StructuralChunker(Chunker):
    """Heading-scoped packing with evidence units for tables and figures.

    Args:
        max_tokens: Budget per chunk.
        min_tokens: A text chunk smaller than this joins its predecessor under the
            same heading path when the two fit the budget together.
        heading_context: Prefix the embedded text with the heading path.
        tokenizer: Counts tokens.
    """

    name = "structural"
    version = "1"

    def __init__(
        self,
        max_tokens: int = 400,
        min_tokens: int = 32,
        heading_context: bool = True,
        tokenizer=None,
    ):
        super().__init__(
            max_tokens=max_tokens, heading_context=heading_context, tokenizer=tokenizer
        )
        self.min_tokens = min_tokens

    def config(self):
        return {**super().config(), "min_tokens": self.min_tokens}

    # -- units ---------------------------------------------------------------

    def _pieces(self, canonical: str, span: Span) -> List[Span]:
        """``span`` cut to fit the budget: sentences, then words."""
        text = canonical[span[0] : span[1]]
        if self.tokenizer.count(text) <= self.max_tokens:
            return [span]
        out: List[Span] = []
        for s in sentence_spans(text, offset=span[0]):
            sentence = canonical[s[0] : s[1]]
            if self.tokenizer.count(sentence) <= self.max_tokens:
                out.append(s)
                continue
            start = None
            count = 0
            prev_end = 0
            for m in re.finditer(r"\S+", sentence):
                n = self.tokenizer.count(m.group())
                if start is not None and count + n > self.max_tokens:
                    out.append((start, s[0] + prev_end))
                    start, count = None, 0
                if start is None:
                    start = s[0] + m.start()
                count += n
                prev_end = m.end()
            if start is not None:
                out.append((start, s[0] + prev_end))
        return self._pack_spans(canonical, out)

    def _pack_spans(self, canonical: str, spans: List[Span]) -> List[Span]:
        out: List[Span] = []
        for s in spans:
            if out and self.tokenizer.count(canonical[out[-1][0] : s[1]]) <= self.max_tokens:
                out[-1] = (out[-1][0], s[1])
            else:
                out.append(s)
        return out

    def _table_pieces(self, canonical: str, table: Element) -> List[List[Span]]:
        """A table's span, or row groups each led by the header row."""
        if self.tokenizer.count(table.text) <= self.max_tokens:
            return [[table.span]]
        lines: List[Span] = []
        pos = table.span[0]
        for line in table.text.split("\n"):
            lines.append((pos, pos + len(line)))
            pos += len(line) + 1
        header = (lines[0][0], lines[1][1]) if len(lines) > 1 else lines[0]
        groups: List[List[Span]] = []
        current: Optional[Span] = None
        budget = self.max_tokens - self.tokenizer.count(canonical[header[0] : header[1]])
        for row in lines[2:]:
            if (
                current is not None
                and self.tokenizer.count(canonical[current[0] : row[1]]) > budget
            ):
                groups.append([header, current])
                current = None
            current = row if current is None else (current[0], row[1])
        if current is not None:
            groups.append([header, current])
        return groups or [[table.span]]

    # -- drafting ------------------------------------------------------------

    def draft(self, tree: VersionTree) -> List[ChunkDraft]:
        canonical = tree.canonical
        paths = heading_paths(tree)
        scopes = section_of(tree)
        units = [e for e in leaves(tree) if e.kind not in _CONTEXT_KINDS]
        by_id = tree.by_id()

        # Evidence units: graphic + caption + the paragraph that cites its label.
        evidence: Dict[str, List[Element]] = {}
        absorbed: Set[str] = set()
        for e in units:
            if e.kind not in ("table", "figure"):
                continue
            members = [e]
            caption = by_id.get(e.attrs.get("caption", ""))
            if caption is not None:
                members.append(caption)
                label = _LABEL.match(caption.text)
                if label:
                    ref = self._referrer(units, e, label.group(1), scopes, absorbed)
                    if ref is not None:
                        members.append(ref)
            evidence[e.id] = members
            absorbed.update(m.id for m in members[1:])

        drafts: List[ChunkDraft] = []
        pending: List[Tuple[List[Span], List[str]]] = []  # packed text units of one heading path
        pending_path: Optional[List[str]] = None
        pending_scope: Optional[str] = None
        pending_tokens = 0

        def flush() -> None:
            nonlocal pending, pending_tokens
            if pending:
                spans = contiguous([s for spans, _ in pending for s in spans], canonical)
                ids = [i for _, ids in pending for i in ids]
                drafts.append(
                    ChunkDraft(
                        spans=spans,
                        kind="text",
                        heading_path=list(pending_path or []),
                        element_ids=ids,
                        scope=pending_scope or "",
                    )
                )
            pending, pending_tokens = [], 0

        for e in units:
            if e.id in absorbed:
                continue
            path = paths.get(e.id, [])
            scope = scopes.get(e.id, "")
            if e.id in evidence:
                flush()
                members = evidence[e.id]
                extra = sorted((m.span for m in members[1:]), key=lambda s: s[0])
                kind = "evidence_unit" if len(members) > 1 else e.kind
                pieces = self._table_pieces(canonical, e) if e.kind == "table" else [[e.span]]
                for piece in pieces:
                    spans = sorted(piece + extra, key=lambda s: s[0])
                    drafts.append(
                        ChunkDraft(
                            spans=spans,
                            kind=kind,
                            heading_path=path,
                            element_ids=[m.id for m in members],
                            scope=scope,
                        )
                    )
                continue
            if scope != pending_scope:
                flush()
                pending_path, pending_scope = path, scope
            pieces = self._pieces(canonical, e.span)
            if len(pieces) > 1:
                # An oversize element is chunked on its own: its pieces never share a
                # chunk with a neighbour, so an edit inside it cannot shift the
                # boundaries of the chunks around it (measured: docs/bench/k1c.md).
                flush()
                pending_path, pending_scope = path, scope
                for piece in pieces:
                    pending.append(([piece], [e.id]))
                    flush()
                    pending_path, pending_scope = path, scope
                continue
            n = self.tokenizer.count(canonical[e.span[0] : e.span[1]])
            if pending and pending_tokens + n > self.max_tokens:
                flush()
                pending_path, pending_scope = path, scope
            pending.append(([e.span], [e.id]))
            pending_tokens += n
        flush()
        return self._merge_small(drafts, canonical)

    def _referrer(self, units, graphic, label, scopes, absorbed) -> Optional[Element]:
        """The paragraph in the graphic's section that mentions ``label`` (nearest first)."""
        scope = scopes.get(graphic.id)
        pattern = re.compile(r"\b" + re.escape(label).replace(r"\ ", r"\s*") + r"\b", re.I)
        candidates = [
            u
            for u in units
            if u.kind == "paragraph"
            and scopes.get(u.id) == scope
            and u.id not in absorbed
            and pattern.search(u.text)
        ]
        if not candidates:
            return None
        return min(candidates, key=lambda u: abs(u.span[0] - graphic.span[0]))

    def _merge_small(self, drafts: List[ChunkDraft], canonical: str) -> List[ChunkDraft]:
        out: List[ChunkDraft] = []
        for d in drafts:
            if out and d.kind == "text" and out[-1].kind == "text" and out[-1].scope == d.scope:
                prev = out[-1]
                small = min(
                    self.tokenizer.count("".join(canonical[s:e] for s, e in prev.spans)),
                    self.tokenizer.count("".join(canonical[s:e] for s, e in d.spans)),
                )
                joined = contiguous(prev.spans + d.spans, canonical)
                total = self.tokenizer.count("\n\n".join(canonical[s:e] for s, e in joined))
                if small < self.min_tokens and total <= self.max_tokens:
                    prev.spans = joined
                    prev.element_ids = prev.element_ids + d.element_ids
                    continue
            out.append(d)
        return out
