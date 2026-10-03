"""Span utilities and the span invariant check.

A span is ``(start, end)`` into a version's canonical text, end exclusive. A
citation is a span, an eval label resolves to a span, and a span maps back to
elements and then to page regions — so these functions are where provenance is
either kept or lost.
"""

from __future__ import annotations

from typing import Iterable, List, Optional, Sequence

from operonx_kb.errors import SpanInvariantError
from operonx_kb.model.document import CONTAINER_KINDS, Element, Region, Span, VersionChunk
from operonx_kb.model.ids import sha256_text

__all__ = [
    "is_valid",
    "length",
    "contains",
    "overlaps",
    "intersection",
    "merge_spans",
    "join_spans",
    "slice_spans",
    "chunk_text",
    "find_span",
    "elements_in_span",
    "regions_for_span",
    "check_elements",
    "check_chunks",
]

#: How a non-contiguous chunk's span texts are joined into its text.
CHUNK_JOIN = "\n\n"


def is_valid(span: Span, text_length: int) -> bool:
    """``0 <= start <= end <= text_length``."""
    start, end = span
    return 0 <= start <= end <= text_length


def length(span: Span) -> int:
    return span[1] - span[0]


def contains(outer: Span, inner: Span) -> bool:
    """Whether ``inner`` lies inside ``outer`` (a zero-length span at an edge counts)."""
    return outer[0] <= inner[0] and inner[1] <= outer[1]


def overlaps(a: Span, b: Span) -> bool:
    """Whether the spans share at least one character."""
    return a[0] < b[1] and b[0] < a[1]


def intersection(a: Span, b: Span) -> Optional[Span]:
    """The shared characters, or ``None``."""
    start, end = max(a[0], b[0]), min(a[1], b[1])
    return (start, end) if start < end else None


def merge_spans(spans: Iterable[Span], gap: int = 0) -> List[Span]:
    """Sort and merge spans that overlap or lie within ``gap`` characters."""
    out: List[Span] = []
    for start, end in sorted(spans):
        if out and start <= out[-1][1] + gap:
            out[-1] = (out[-1][0], max(out[-1][1], end))
        else:
            out.append((start, end))
    return out


def join_spans(spans: Sequence[Span]) -> Span:
    """The smallest single span covering all of ``spans``."""
    if not spans:
        raise ValueError("join_spans() needs at least one span")
    return (min(s[0] for s in spans), max(s[1] for s in spans))


def slice_spans(text: str, spans: Sequence[Span]) -> List[str]:
    """The text of each span."""
    return [text[s:e] for s, e in spans]


def chunk_text(canonical: str, spans: Sequence[Span]) -> str:
    """A chunk's text: its span texts joined by a blank line."""
    return CHUNK_JOIN.join(slice_spans(canonical, spans))


def find_span(canonical: str, quote: str, start: int = 0) -> Optional[Span]:
    """The first exact occurrence of ``quote`` at or after ``start``, or ``None``."""
    if not quote:
        return None
    at = canonical.find(quote, start)
    return (at, at + len(quote)) if at >= 0 else None


def elements_in_span(
    elements: Iterable[Element], span: Span, leaves_only: bool = True
) -> List[Element]:
    """Body elements whose span overlaps ``span``, in document order.

    With ``leaves_only`` (default) containers (document, section, list) are left
    out, so a citation maps to the paragraphs it quotes, not to their section.
    """
    hits = [
        e
        for e in elements
        if e.span is not None
        and overlaps(e.span, span)
        and not (leaves_only and e.kind in CONTAINER_KINDS)
    ]
    return sorted(hits, key=lambda e: (e.span[0], e.span[1]))


def regions_for_span(elements: Iterable[Element], span: Span) -> List[Region]:
    """The page regions that render ``span``: every region of every leaf element
    it overlaps, narrowed to a region's own ``char_span`` when it has one."""
    out: List[Region] = []
    for element in elements_in_span(elements, span):
        for region in element.regions:
            if region.char_span is None or overlaps(region.char_span, span):
                out.append(region)
    return out


def check_elements(canonical: str, elements: Iterable[Element]) -> int:
    """Assert the span invariant for every element; return how many were checked.

    Body elements must have a valid span with ``canonical[span] == text``;
    furniture must have no span; a child's span must lie inside its parent's.

    Raises:
        SpanInvariantError: Naming the first offending element, its span and both
            texts. It is a bug in the parser, structurer or serializer.
    """
    by_id = {}
    checked = 0
    for element in elements:
        by_id[element.id] = element
        checked += 1
        if element.layer == "furniture":
            if element.span is not None:
                raise SpanInvariantError(
                    "a furniture element has a span; furniture is not part of the canonical text",
                    {"element": element.id, "kind": element.kind, "span": element.span},
                )
            continue
        if element.span is None or not is_valid(element.span, len(canonical)):
            raise SpanInvariantError(
                "a body element has no span or one outside the canonical text",
                {
                    "element": element.id,
                    "kind": element.kind,
                    "span": element.span,
                    "len": len(canonical),
                },
            )
        actual = canonical[element.span[0] : element.span[1]]
        if actual != element.text:
            raise SpanInvariantError(
                "element text differs from canonical[span]: the structurer assigned a wrong span",
                {
                    "element": element.id,
                    "kind": element.kind,
                    "span": element.span,
                    "text": element.text,
                    "canonical[span]": actual,
                },
            )
        parent = by_id.get(element.parent_id) if element.parent_id else None
        if (
            parent is not None
            and parent.span is not None
            and not contains(parent.span, element.span)
        ):
            raise SpanInvariantError(
                "a child's span is outside its parent's span",
                {
                    "element": element.id,
                    "span": element.span,
                    "parent": parent.id,
                    "parent span": parent.span,
                },
            )
    return checked


def check_chunks(canonical: str, occurrences: Iterable[VersionChunk], content_shas: dict) -> int:
    """Assert every chunk occurrence's spans lie in ``canonical`` and hash to its chunk.

    Args:
        canonical: The version's canonical text.
        occurrences: The version's chunk occurrences.
        content_shas: ``chunk_id -> content_sha``.

    Raises:
        SpanInvariantError: On the first span out of range or hash mismatch.
    """
    checked = 0
    for occ in occurrences:
        checked += 1
        if not occ.spans:
            raise SpanInvariantError("a chunk occurrence has no spans", {"chunk": occ.chunk_id})
        for span in occ.spans:
            if not is_valid(span, len(canonical)) or length(span) == 0:
                raise SpanInvariantError(
                    "a chunk span is empty or outside the canonical text",
                    {"chunk": occ.chunk_id, "span": span, "len": len(canonical)},
                )
        expected = content_shas.get(occ.chunk_id)
        actual = sha256_text(chunk_text(canonical, occ.spans))
        if expected != actual:
            raise SpanInvariantError(
                "chunk content_sha differs from the hash of its span texts",
                {"chunk": occ.chunk_id, "spans": occ.spans, "expected": expected, "actual": actual},
            )
    return checked
