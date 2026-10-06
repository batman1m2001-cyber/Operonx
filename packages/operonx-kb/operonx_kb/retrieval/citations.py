"""Citation verification and resolution (track5 §10.3-10.4, PLAN R6).

The answer model cites by span: every citation is ``{"source": n, "quote": …}``
with a quote copied from source ``n``. A citation is **verified** only when its
quote is found in that source's canonical text; it is then resolved to the
canonical span it occupies, the elements under that span and their page
regions (page + bbox), so a viewer can highlight the exact box.

Matching is exact up to Unicode composition (NFC) and whitespace: runs of
whitespace match runs of whitespace, so a quote that re-wraps a line still
matches, but a paraphrase, an ellipsis or a changed word does not. Two
differences are tolerated (:data:`TOLERANCES`, measured in ``docs/bench/k2.md``):
the kind of quotation mark, and the case of the first letter. A citation records
which it needed (``tolerated``), and always shows the canonical text at its span. What does
not verify is **dropped and reported**, never shown: its ``[n]`` markers leave
the answer text when no verified quote is left for source ``n``, and the
sentences left without a marker are listed in ``unsupported_sentences``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from operonx_kb.model.document import Element, Span
from operonx_kb.text.normalize import clean_chars
from operonx_kb.text.sentences import sentence_spans
from operonx_kb.text.spans import elements_in_span, regions_for_span

__all__ = [
    "TOLERANCES",
    "QuoteMatch",
    "match_quote",
    "find_quote",
    "verify_citations",
    "answer_sentences",
    "MARKER",
]

#: An answer's citation marker: ``[1]``, or several in one bracket ``[1, 3]``.
MARKER = re.compile(r"\[(\d+(?:\s*,\s*\d+)*)\]")
_WS = re.compile(r"\s+")
_EDGE_QUOTES = "\"'“”‘’«»「」"


def _collapsed(text: str) -> Tuple[str, List[int]]:
    """``text`` with whitespace runs collapsed to one space, and for each character of
    the result the offset of the character it came from. (Canonical text is NFC
    already: the serializer normalises every character it writes.)"""
    out: List[str] = []
    origin: List[int] = []
    for m in re.finditer(r"\s+|\S", text):
        out.append(" " if m.group().isspace() else m.group())
        origin.append(m.start())
    return "".join(out), origin


#: Differences a quote may have from its source and still verify (PLAN R6): the kind of
#: quotation mark (a model writing JSON turns ``"`` into ``'``), and the case of the first
#: letter (a sentence quoted from its middle starts with a capital, or the reverse).
#: The citation then shows the canonical text at the span, never the model's string.
TOLERANCES = ("quote_marks", "first_letter_case")
_QUOTE_MARKS = str.maketrans(
    {c: '"' for c in "'\u2018\u2019\u201a\u201b\u201c\u201d\u201e\u201f\u00ab\u00bb\u2039\u203a"}
)


@dataclass(frozen=True)
class QuoteMatch:
    """Where a quote is in the canonical text, and which tolerances it needed."""

    span: Span
    tolerated: Tuple[str, ...] = ()


def _find(spans: Sequence[Span], canonical: str, needle: str, marks: bool) -> Optional[Span]:
    if marks:
        needle = needle.translate(_QUOTE_MARKS)
    for start, end in spans:
        hay, origin = _collapsed(canonical[start:end])
        if marks:
            hay = hay.translate(_QUOTE_MARKS)  # one character for one: offsets hold
        at = hay.find(needle)
        if at >= 0:
            return (start + origin[at], start + origin[at + len(needle) - 1] + 1)
    return None


def match_quote(
    canonical: str, spans: Sequence[Span], quote: str, tolerances: Sequence[str] = TOLERANCES
) -> Optional[QuoteMatch]:
    """The canonical span of ``quote`` inside one of ``spans``, or ``None``.

    Whitespace-insensitive, and the quote is cleaned like the canonical text (NFC,
    invisible characters dropped, :func:`~operonx_kb.text.normalize.clean_chars`); the
    quote's own surrounding quotation marks and spaces are ignored. A quote must lie
    within one span: two pieces of text the source shows apart are not one quotation.
    An exact match is tried first; then each of ``tolerances`` (:data:`TOLERANCES`),
    and the match says which it needed.
    """
    needle = _WS.sub(" ", clean_chars(quote)).strip().strip(_EDGE_QUOTES).strip()
    if not needle:
        return None
    unknown = set(tolerances) - set(TOLERANCES)
    if unknown:
        raise ValueError(f"unknown quote tolerances {sorted(unknown)}; known: {list(TOLERANCES)}")
    marks = "quote_marks" in tolerances
    first = "first_letter_case" in tolerances and needle[0].isalpha()
    variants = [(needle, False, ())]
    if marks:
        variants.append((needle, True, ("quote_marks",)))
    if first:
        swapped = needle[0].swapcase() + needle[1:]
        variants.append((swapped, False, ("first_letter_case",)))
        if marks:
            variants.append((swapped, True, ("quote_marks", "first_letter_case")))
    for text, use_marks, tolerated in variants:
        span = _find(spans, canonical, text, use_marks)
        if span is not None:
            return QuoteMatch(span, tolerated)
    return None


def find_quote(
    canonical: str, spans: Sequence[Span], quote: str, tolerances: Sequence[str] = TOLERANCES
) -> Optional[Span]:
    """:func:`match_quote`'s span, or ``None``."""
    found = match_quote(canonical, spans, quote, tolerances)
    return found.span if found else None


def _markers(text: str) -> List[Tuple[int, int, List[int]]]:
    return [
        (m.start(), m.end(), [int(x) for x in re.split(r"\s*,\s*", m.group(1))])
        for m in MARKER.finditer(text)
    ]


def verify_citations(
    answer: str,
    citations: Sequence[Any],
    sources: Sequence[Mapping[str, Any]],
    canonicals: Mapping[str, str],
    elements: Mapping[str, Sequence[Element]],
    tolerances: Sequence[str] = TOLERANCES,
) -> Dict[str, Any]:
    """Check every citation against its source; resolve the verified ones.

    Args:
        answer: The answer text with ``[n]`` markers (``n`` names a source).
        citations: ``[{"source": n, "quote": …}, …]`` as the model gave them.
        sources: The context's sources (:meth:`~operonx_kb.retrieval.context.Source.as_dict`).
        canonicals: ``version_id -> canonical text`` of the sources' versions.
        elements: ``version_id -> element tree`` of the same versions.
        tolerances: Which :data:`TOLERANCES` a quote may use (``()``: exact only).

    Returns:
        ``{"text", "citations", "dropped", "unsupported_sentences", "stats"}``. ``text``
        holds only the markers of sources with a verified citation.
    """
    by_n = {int(s["n"]): s for s in sources}
    verified: List[Dict[str, Any]] = []
    dropped: List[Dict[str, Any]] = []
    for raw in citations or []:
        if not isinstance(raw, Mapping) or not isinstance(raw.get("quote"), str):
            dropped.append({"citation": raw, "reason": "not a {source, quote} object"})
            continue
        try:
            n = int(raw.get("source"))
        except (TypeError, ValueError):
            dropped.append({"citation": raw, "reason": "source is not a number"})
            continue
        source = by_n.get(n)
        if source is None:
            dropped.append({"citation": raw, "reason": f"there is no source [{n}]"})
            continue
        canonical = canonicals[source["version_id"]]
        found = match_quote(
            canonical, [tuple(s) for s in source["spans"]], raw["quote"], tolerances
        )
        if found is None:
            dropped.append({"citation": raw, "reason": f"the quote is not in source [{n}]"})
            continue
        span = found.span
        tree = elements[source["version_id"]]
        regions = regions_for_span(tree, span)
        verified.append(
            {
                "marker": n,
                "source": n,
                "quote": canonical[span[0] : span[1]],
                "document_id": source["document_id"],
                "key": source["key"],
                "title": source.get("title"),
                "version_id": source["version_id"],
                "span": list(span),
                "element_ids": [e.id for e in elements_in_span(tree, span)],
                "pages": sorted({r.page_no for r in regions}),
                "regions": [{"page_no": r.page_no, "bbox": list(r.bbox)} for r in regions],
                "support": "verified",
                "tolerated": list(found.tolerated),
            }
        )
    supported = {c["source"] for c in verified}
    text = _keep_markers(answer or "", supported)
    sentences = answer_sentences(text)
    unsupported = [i for i, (s, e) in enumerate(sentences) if not _markers(text[s:e])]
    cited = len(verified) + len(dropped)
    stats = {
        "citations": cited,
        "verified": len(verified),
        "dropped": len(dropped),
        "precision": round(len(verified) / cited, 4) if cited else None,
        "tolerated": sum(1 for c in verified if c["tolerated"]),
        "sentences": len(sentences),
        "unsupported": len(unsupported),
    }
    return {
        "text": text,
        "citations": verified,
        "dropped": dropped,
        "unsupported_sentences": unsupported,
        "stats": stats,
    }


_LEADING_MARKERS = re.compile(r"(?:\[\d+(?:\s*,\s*\d+)*\][.,;:]?\s*)+")


def answer_sentences(text: str) -> List[Span]:
    """The sentences of an answer. Markers written after the full stop (``… changes. [1]``)
    belong to the sentence before them, not to the next one or to one of their own."""
    out: List[Span] = []
    for start, end in sentence_spans(text):
        lead = _LEADING_MARKERS.match(text, start)
        if out and lead:
            out[-1] = (out[-1][0], len(text[: lead.end()].rstrip()))
            start = lead.end()
        if start < end and re.search(r"\w", MARKER.sub("", text[start:end])):
            out.append((start, end))
        elif out:
            out[-1] = (out[-1][0], max(out[-1][1], end))
    return out


def _keep_markers(text: str, supported: set) -> str:
    """``text`` with each marker reduced to its supported sources (gone if none is)."""
    out, last = [], 0
    for start, end, numbers in _markers(text):
        kept = [n for n in numbers if n in supported]
        out.append(text[last:start].rstrip(" ") if not kept else text[last:start])
        if kept:
            out.append("[" + ", ".join(str(n) for n in dict.fromkeys(kept)) + "]")
        last = end
    out.append(text[last:])
    return "".join(out)
