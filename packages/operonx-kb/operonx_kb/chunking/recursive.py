"""The recursive chunker: the separator-based baseline.

It ignores the element tree and splits the canonical text the way LangChain's
``RecursiveCharacterTextSplitter`` does — by blank lines, then lines, then
sentence ends, then spaces — and packs the pieces up to the budget. Chroma's
chunking study found it strong when tuned, so the structural chunker is
measured against it (track5 §7.4). Spans stay exact: every piece is an offset
range of the canonical text.
"""

from __future__ import annotations

import re
from bisect import bisect_right
from typing import List

from operonx_kb.chunking.base import ChunkDraft, Chunker, heading_paths, leaves
from operonx_kb.model.document import Span
from operonx_kb.structure.build import VersionTree

__all__ = ["RecursiveChunker"]

_SEPARATORS = ("\n\n", "\n", ". ", " ")


class RecursiveChunker(Chunker):
    """Separator-recursive splitting of the canonical text."""

    name = "recursive"
    version = "1"

    def _split(self, text: str, span: Span, level: int) -> List[Span]:
        piece = text[span[0] : span[1]]
        if self.tokenizer.count(piece) <= self.max_tokens or level >= len(_SEPARATORS):
            return [span]
        sep = _SEPARATORS[level]
        out: List[Span] = []
        start = span[0]
        for m in re.finditer(re.escape(sep), piece):
            end = span[0] + m.start() + (1 if sep == ". " else 0)
            if end > start:
                out.extend(self._split(text, (start, end), level + 1))
            start = span[0] + m.end()
        if start < span[1]:
            out.extend(self._split(text, (start, span[1]), level + 1))
        return out

    def draft(self, tree: VersionTree) -> List[ChunkDraft]:
        text = tree.canonical
        pieces = []
        for s, e in self._split(text, (0, len(text)), 0):
            while s < e and text[s].isspace():
                s += 1
            while e > s and text[e - 1].isspace():
                e -= 1
            if e > s:
                pieces.append((s, e))
        packed: List[Span] = []
        for p in pieces:
            if packed and self.tokenizer.count(text[packed[-1][0] : p[1]]) <= self.max_tokens:
                packed[-1] = (packed[-1][0], p[1])
            else:
                packed.append(p)
        paths = heading_paths(tree)
        starts = [(e.span[0], e.id) for e in leaves(tree)]
        keys = [s for s, _ in starts]
        drafts = []
        for span in packed:
            i = bisect_right(keys, span[0]) - 1
            path = paths.get(starts[i][1], []) if i >= 0 else []
            drafts.append(ChunkDraft(spans=[span], kind="text", heading_path=path))
        return drafts
