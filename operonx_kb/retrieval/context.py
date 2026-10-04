"""The context builder (track5 §10.1, PLAN R7): hits become numbered sources.

Hits are taken in rank order. Each is widened by ``neighbours`` chunks before
and after it in its version, when they belong to the same section (same
heading path), and joined with any source of the same version it touches, so
one passage is never shown twice. Sources are packed into a token budget and
numbered ``[1..n]``.

A source's text is exactly the canonical text it covers: its spans are merged
where only whitespace separates them, and the pieces that stay apart are
joined by a blank line. Citation quotes are checked against these spans
(:mod:`operonx_kb.retrieval.citations`), never against a prompt rendering.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Sequence

from operonx_kb.model.document import Span, VersionChunk
from operonx_kb.text.spans import CHUNK_JOIN, merge_spans
from operonx_kb.text.tokenize import RegexTokenizer, Tokenizer

__all__ = ["Source", "build_sources", "render_sources", "merge_touching"]


@dataclass
class Source:
    """One numbered passage of the context.

    Attributes:
        n: Its number in the prompt (``[n]``).
        spans: Canonical spans it covers, in order, disjoint.
        text: ``CHUNK_JOIN.join(canonical[s:e] for s, e in spans)``.
        chunk_ids: The chunks it is made of (the hit's first).
        hit_ranks: Ranks of the hits it holds.
    """

    n: int
    document_id: str
    key: str
    title: Optional[str]
    version_id: str
    heading_path: List[str]
    pages: List[int]
    spans: List[Span]
    text: str
    chunk_ids: List[str] = field(default_factory=list)
    hit_ranks: List[int] = field(default_factory=list)

    def as_dict(self) -> Dict[str, Any]:
        out = asdict(self)
        out["spans"] = [list(s) for s in self.spans]
        return out


def merge_touching(spans: Sequence[Span], canonical: str) -> List[Span]:
    """Merge spans that overlap or are separated by whitespace only."""
    out: List[Span] = []
    for span in merge_spans(spans):
        if out and not canonical[out[-1][1] : span[0]].strip():
            out[-1] = (out[-1][0], max(out[-1][1], span[1]))
        else:
            out.append(span)
    return out


def _touches(a: Sequence[Span], b: Sequence[Span], canonical: str) -> bool:
    """Whether a span of ``a`` overlaps a span of ``b`` or meets it across whitespace."""
    for s1, e1 in a:
        for s2, e2 in b:
            lo, hi = (e1, s2) if e1 <= s2 else (e2, s1)
            if s1 < e2 and s2 < e1 or lo <= hi and not canonical[lo:hi].strip():
                return True
    return False


def _text(canonical: str, spans: Sequence[Span]) -> str:
    return CHUNK_JOIN.join(canonical[s:e] for s, e in spans)


def build_sources(
    hits: Sequence[Mapping[str, Any]],
    occurrences: Mapping[str, Sequence[VersionChunk]],
    heading_paths: Mapping[str, List[str]],
    canonicals: Mapping[str, str],
    *,
    neighbours: int = 1,
    budget_tokens: int = 1500,
    tokenizer: Optional[Tokenizer] = None,
) -> List[Source]:
    """Numbered sources for ``hits`` (hydrated hits, best first).

    Args:
        occurrences: ``version_id -> its chunk occurrences`` for every hit's version.
        heading_paths: ``chunk_id -> heading path`` for those occurrences.
        canonicals: ``version_id -> canonical text``.
        neighbours: Chunks to add on each side of a hit, within its section.
        budget_tokens: The most tokens the sources may hold together. A source that
            would pass it is tried without its neighbours, then skipped.
    """
    tokenizer = tokenizer or RegexTokenizer()
    sources: List[Source] = []
    used = 0
    for hit in hits:
        version = hit["version_id"]
        canonical = canonicals[version]
        occ = sorted(occurrences[version], key=lambda o: o.ordinal)
        at = next((i for i, o in enumerate(occ) if o.chunk_id == hit["chunk_id"]), None)
        if at is None:
            continue
        section = heading_paths.get(hit["chunk_id"], [])
        window = [
            o
            for o in occ[max(0, at - neighbours) : at + neighbours + 1]
            if heading_paths.get(o.chunk_id, []) == section
        ]
        for group in (window, [occ[at]]):  # with its neighbours, else alone
            group_spans = [tuple(sp) for o in group for sp in o.spans]
            touched = next(
                (
                    src
                    for src in sources
                    if src.version_id == version and _touches(src.spans, group_spans, canonical)
                ),
                None,
            )
            base = touched.spans if touched is not None else []
            spans = merge_touching(base + group_spans, canonical)
            text = _text(canonical, spans)
            cost = tokenizer.count(text) - (tokenizer.count(touched.text) if touched else 0)
            if used + cost > budget_tokens:
                continue
            used += cost
            ids = [hit["chunk_id"]] + [o.chunk_id for o in group]
            pages = {p for o in group for p in o.pages}
            if touched is not None:
                touched.spans, touched.text = spans, text
                touched.chunk_ids = list(dict.fromkeys(touched.chunk_ids + ids))
                touched.pages = sorted(set(touched.pages) | pages)
                touched.hit_ranks.append(hit.get("rank", 0))
            else:
                sources.append(
                    Source(
                        n=len(sources) + 1,
                        document_id=hit["document_id"],
                        key=hit["key"],
                        title=hit.get("title"),
                        version_id=version,
                        heading_path=list(section),
                        pages=sorted(pages),
                        spans=spans,
                        text=text,
                        chunk_ids=list(dict.fromkeys(ids)),
                        hit_ranks=[hit.get("rank", 0)],
                    )
                )
            break
    return sources


def render_sources(sources: Sequence[Mapping[str, Any]]) -> str:
    """The sources as the answer prompt shows them::

    [1] Leave policy › Annual leave (p. 3)
    Every employee has twelve days …
    """
    blocks = []
    for s in sources:
        label = " › ".join([s.get("title") or s["key"], *s.get("heading_path", [])[-2:]])
        pages = s.get("pages") or []
        where = f" (p. {', '.join(str(p) for p in pages)})" if pages else ""
        blocks.append(f"[{s['n']}] {label}{where}\n{s['text']}")
    return "\n\n".join(blocks)
