"""Sentence boundaries, as spans, for splitting oversize paragraphs.

Rule based and language neutral enough for English and Vietnamese: a sentence
ends at ``.``, ``!``, ``?`` or ``…`` (optionally followed by a closing quote or
bracket) when whitespace and then an uppercase letter, digit or opening
quote/bracket follow. Common abbreviations (``e.g.``, ``Dr.``, ``TP.``) and
decimal numbers do not end a sentence.
"""

from __future__ import annotations

import re
from typing import List

from operonx_kb.model.document import Span

__all__ = ["sentence_spans"]

_END = re.compile(r"[.!?…]+[\"'”’)\]]*(?=\s+[\"'“‘(\[]?[\w])", re.UNICODE)
_ABBREV = frozenset(
    "e.g i.e etc vs mr mrs ms dr prof sr jr st no fig figs eq tp q p pp vol ed approx".split()
)


def _is_abbreviation(text: str, end: int) -> bool:
    word = re.search(r"([\w.]+)\.$", text[:end].rstrip("\"'”’)]"))
    if not word:
        return False
    token = word.group(1).lower()
    return token in _ABBREV or len(token) == 1


def sentence_spans(text: str, offset: int = 0) -> List[Span]:
    """Spans of the sentences of ``text``, shifted by ``offset``, whitespace trimmed.

    The spans cover the text without gaps other than the whitespace between
    sentences, so joining them with single spaces gives back an inline text.
    """
    spans: List[Span] = []
    start = 0
    for match in _END.finditer(text):
        end = match.end()
        nxt = text[end:].lstrip()
        if (
            _is_abbreviation(text, end)
            or not nxt[:1].isupper()
            and not nxt[:1].isdigit()
            and nxt[:1] not in "\"'“‘(["
        ):
            continue
        spans.append((start, end))
        start = end + (len(text[end:]) - len(text[end:].lstrip()))
    if start < len(text.rstrip()):
        spans.append((start, len(text.rstrip())))
    out = []
    for s, e in spans:
        while s < e and text[s].isspace():
            s += 1
        if s < e:
            out.append((s + offset, e + offset))
    return out
