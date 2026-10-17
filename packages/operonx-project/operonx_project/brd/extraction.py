"""Check what a requirements session extracted, mechanically (plan §4.2).

What an agent tends to get wrong, each checked from the source's own text
and never from the agent's word:

- **quotes are real**: a quote must occur in the source after
  :func:`~operonx_project.brd.readers.normalize`; ``…`` joins parts that
  must occur in that order. A paraphrase is allowed only when marked.
  The engine records the section it found the quote in;
- **nothing is skipped**: every section with text yields a requirement or
  is marked descriptive with a reason. Coverage counts the sections where
  the quotes *were found*, not the sections the agent named;
- **IDs are stable and unique**: well formed, no duplicate, an ID used
  before is kept or marked superseded, a superseded ID is never reused.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Sequence

from operonx_project.brd.readers import Section, normalize
from operonx_project.brd.records import REQ_TYPES, Requirement

__all__ = ["ID_PATTERN", "Problem", "check_coverage", "check_ids", "check_quotes", "find_quote"]

#: BRD-BR-09, OWN-UI-03, and a source's own letter codes kept: F26 → BRD-F26
ID_PATTERN = re.compile(r"^[A-Z][A-Z0-9]*(?:-[A-Z0-9]+)*-[A-Z]{0,3}\d{2,4}$")
_ELLIPSIS = re.compile(r"\s*(?:…|\.\.\.|\[…\]|\[\.\.\.\])\s*")


@dataclass
class Problem:
    id: str  # a requirement id, or a section id for coverage
    kind: str  # fake_quote | uncovered | bad_id | duplicate_id | reused_id | dropped_id | bad_type
    message: str
    data: Dict = field(default_factory=dict)


def find_quote(quote: str, sections: Sequence[Section], prefer: str = "") -> Optional[str]:
    """The id of a section whose text holds *quote* (*prefer* when it does,
    else the first), or ``None``."""
    pieces = [p for p in _ELLIPSIS.split(quote) if normalize(p)]
    if not pieces:
        return None
    ordered = sorted(sections, key=lambda s: s.id != prefer) if prefer else sections
    for s in ordered:
        hay = normalize(f"{s.title}\n{s.text}", loose=s.ocr)
        parts = [normalize(p, loose=s.ocr) for p in pieces]
        pos = 0
        for p in parts:
            i = hay.find(p, pos)
            if i < 0:
                break
            pos = i + len(p)
        else:
            return s.id
    return None


def check_quotes(reqs: Iterable[Requirement], sections: Sequence[Section], min_chars: int = 8) -> List[Problem]:
    """Sets each requirement's ``section`` to where its quote was found."""
    out: List[Problem] = []
    for r in reqs:
        if r.type not in REQ_TYPES:
            out.append(Problem(r.id, "bad_type", f"{r.id}: type {r.type!r} is not one of {list(REQ_TYPES)}"))
        if r.paraphrase:
            near = next((s for s in sections if r.location and r.location.split()[0] == s.id), None)
            r.section = near.id if near else r.section
            continue
        if len(normalize(r.quote)) < min_chars:
            out.append(Problem(r.id, "fake_quote", f"{r.id}: the quote is empty or too short to find"))
            continue
        where = find_quote(r.quote, sections, prefer=(r.location or r.section).split(" ")[0])
        if where is None:
            out.append(Problem(r.id, "fake_quote", f"{r.id}: the quote does not occur in the source",
                               {"quote": r.quote[:300]}))  # fmt: skip
        else:
            r.section = where
    return out


def check_coverage(
    sections: Sequence[Section], reqs: Iterable[Requirement], descriptive: Dict[str, str]
) -> List[Problem]:
    """Every section with text has a requirement found in it, or a reason."""
    covered = {r.section for r in reqs if r.section and r.status != "superseded"}
    out: List[Problem] = []
    for s in sections:
        if not s.text.strip() or s.id in covered:
            continue
        reason = (descriptive.get(s.id) or "").strip()
        if not reason:
            out.append(Problem(s.id, "uncovered", f"section {s.location} yields no requirement and is not "
                                                  "marked descriptive"))  # fmt: skip
    return out


def check_ids(reqs: Sequence[Requirement], previous: Iterable[str] = (), retired: Iterable[str] = ()) -> List[Problem]:
    out: List[Problem] = []
    seen: Dict[str, int] = {}
    for r in reqs:
        seen[r.id] = seen.get(r.id, 0) + 1
        if not ID_PATTERN.match(r.id):
            msg = f"{r.id!r} is not a well-formed id (like BRD-BR-09, BRD-F26, OWN-UI-03)"
            out.append(Problem(r.id, "bad_id", msg))
    out += [Problem(i, "duplicate_id", f"{i} is used {n} times") for i, n in seen.items() if n > 1]
    retired = set(retired)
    for r in reqs:
        if r.id in retired and r.status != "superseded":
            out.append(Problem(r.id, "reused_id", f"{r.id} was retired; a retired id is never reused"))
    for old in previous:
        if old not in seen:
            out.append(Problem(old, "dropped_id", f"{old} existed before: keep it, or mark it superseded"))
    return out
