"""A new version of a source: what changed, and which requirements it touches (plan §4.5).

Sections are matched by their heading path (numbering stripped, so a
renumbered section still matches), then by title alone. Text is compared
after :func:`~operonx_project.brd.readers.normalize`, so whitespace and
punctuation changes are not changes. The extraction then runs on the
changed and added sections only; requirements found in changed or removed
sections are marked in the matrix, and the cards serving them re-open.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Sequence, Tuple

from operonx_project.brd.readers import Section, SourceText, normalize
from operonx_project.brd.records import MATRIX, Requirement

__all__ = ["SourceDiff", "affected", "diff_sources", "mark_changed"]

_NUM = re.compile(r"^\s*\d+(?:\.\d+)*\.?\s+")


@dataclass
class SourceDiff:
    changed: List[Tuple[str, str]] = field(default_factory=list)  # (old section id, new section id)
    added: List[str] = field(default_factory=list)  # new section ids
    removed: List[str] = field(default_factory=list)  # old section ids
    same: int = 0

    @property
    def to_extract(self) -> List[str]:
        """The new version's sections the extraction must read again."""
        return [n for _, n in self.changed] + self.added


def _key(s: Section) -> Tuple[str, ...]:
    return tuple(normalize(_NUM.sub("", t)) for t in (s.path or [s.title]))


def diff_sources(old: SourceText, new: SourceText) -> SourceDiff:
    olds, news = old.all_sections(), new.all_sections()
    by_path: Dict[Tuple[str, ...], Section] = {}
    by_title: Dict[str, List[Section]] = {}
    for s in olds:
        by_path.setdefault(_key(s), s)
        by_title.setdefault(normalize(_NUM.sub("", s.title)), []).append(s)
    used: set = set()
    out = SourceDiff()
    for s in news:
        o = by_path.get(_key(s))
        if o is None or o.id in used:
            cands = [c for c in by_title.get(normalize(_NUM.sub("", s.title)), []) if c.id not in used]
            o = cands[0] if cands and s.title else None
        if o is None:
            out.added.append(s.id)
            continue
        used.add(o.id)
        if normalize(o.text) == normalize(s.text):
            out.same += 1
        else:
            out.changed.append((o.id, s.id))
    out.removed = [s.id for s in olds if s.id not in used]
    return out


def affected(diff: SourceDiff, reqs: Sequence[Requirement]) -> List[str]:
    """The requirements found in a section that changed or went away."""
    touched = {o for o, _ in diff.changed} | set(diff.removed)
    return [r.id for r in reqs if r.section in touched and r.status != "superseded"]


def mark_changed(brd: Path, ids: Sequence[str], source: str) -> List[str]:
    """Note in each matrix row that its source changed; returns the rows marked."""
    f = Path(brd) / MATRIX
    if not f.exists() or not ids:
        return []
    want = set(ids)
    note = f"⚠ source changed ({source}): re-check"
    marked: List[str] = []
    lines = f.read_text(encoding="utf-8").splitlines()
    for n, line in enumerate(lines):
        m = re.match(r"^\|\s*([A-Z][A-Z0-9-]*-[A-Z]{0,3}\d{2,4})\s*\|", line)
        if m and m.group(1) in want and note not in line:
            body = line.rstrip().rstrip("|").rstrip()
            lines[n] = f"{body} {note} |"
            marked.append(m.group(1))
    f.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return marked
