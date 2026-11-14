"""One search over what a project knows: its board, BRD, plans, knowledge and guide.

An assistant asks before it reads a whole document: ``search(root, "retry tts")`` answers the sections
that match best, each as its heading, three lines and ``path:line``; ``read(root, path, heading)`` then
gives one section whole. It reads markdown only, and only where the project keeps what it knows:

- ``docs/brd/**/*.md``: the board, the requirements, ``knowledge.md`` (patterns, pitfalls, decisions);
- ``docs/*.md``: the plans;
- ``.operonx/guide/**/*.md`` and ``.operonx/catalog.md``: the operonx guide and the generated catalog;
- ``AGENTS.md``.

A section is a heading and the lines up to the next heading. The score is BM25 over the query's words,
a word in the heading counting three times, plurals and ``-ing`` folded; a card or entry id (``C29``,
``K3``) is a word like any other.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, List, Optional, Tuple

__all__ = ["Hit", "read", "search", "sections", "sources", "text"]

_GLOBS = ("docs/brd/**/*.md", "docs/*.md", ".operonx/guide/**/*.md", ".operonx/catalog.md", "AGENTS.md")
_HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
_WORD = re.compile(r"[a-z0-9][a-z0-9_]*", re.I)
_STOP = frozenset(
    "a an and are as at be by do does for from how i in is it of on or the this to was we what when where "
    "which who why with you".split()
)
_K1, _B, _IN_HEADING = 1.2, 0.75, 3


@dataclass
class Hit:
    """One section that matched: where it is, its heading and the lines worth showing."""

    path: str
    line: int
    heading: str
    lines: List[str]
    score: float


def sources(root: Path) -> List[Path]:
    """The markdown files a search reads, each once, in a stable order."""
    root = Path(root)
    seen, out = set(), []
    for glob in _GLOBS:
        for path in sorted(root.glob(glob)):
            if path.is_file() and path not in seen:
                seen.add(path)
                out.append(path)
    return out


def sections(path: Path) -> Iterator[Tuple[int, int, str, List[str]]]:
    """``(line, level, heading, body)`` per section of *path*; a heading inside a code fence is text."""
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    start, level, heading, body, fenced = 1, 0, path.name, [], False
    for i, line in enumerate(lines, 1):
        if line.lstrip().startswith("```"):
            fenced = not fenced
        m = None if fenced else _HEADING.match(line)
        if m:
            if body or level:
                yield start, level, heading, body
            start, level, heading, body = i, len(m.group(1)), m.group(2), []
        else:
            body.append(line)
    if body or level:
        yield start, level, heading, body


def _stem(w: str) -> str:
    """``retries`` and ``retry``, ``ops`` and ``op``, ``running`` and ``run``: one word."""
    if len(w) > 4 and w.endswith("ies"):
        return w[:-3] + "y"
    if len(w) > 5 and w.endswith("ing"):
        w = w[:-3]
        return w[:-1] if len(w) > 2 and w[-1] == w[-2] else w
    if len(w) > 3 and w.endswith("s") and not w.endswith(("ss", "us", "is")):
        return w[:-1]
    return w


def _words(text: str) -> List[str]:
    return [_stem(w) for w in (m.lower() for m in _WORD.findall(text)) if w not in _STOP]


def search(root: Path, query: str, limit: int = 8) -> List[Hit]:
    """The sections of *root*'s documents that match *query* best, best first."""
    root = Path(root)
    terms = list(dict.fromkeys(_words(query)))
    if not terms:
        return []
    docs = []
    for path in sources(root):
        rel = path.relative_to(root).as_posix()
        for line, _, heading, body in sections(path):
            counts = Counter(_words(heading) * _IN_HEADING + _words("\n".join(body)))
            if counts:
                docs.append((rel, line, heading, body, counts, sum(counts.values())))
    if not docs:
        return []
    avg = sum(d[5] for d in docs) / len(docs)
    df = {t: sum(1 for d in docs if t in d[4]) for t in terms}
    hits = []
    for rel, line, heading, body, counts, size in docs:
        score = 0.0
        for t in terms:
            tf = counts.get(t, 0)
            if tf:
                idf = math.log(1 + (len(docs) - df[t] + 0.5) / (df[t] + 0.5))
                score += idf * tf * (_K1 + 1) / (tf + _K1 * (1 - _B + _B * size / avg))
        if score > 0:
            hits.append(Hit(rel, line, heading, _snippet(body, terms), round(score, 3)))
    hits.sort(key=lambda h: (-h.score, h.path, h.line))
    return hits[:limit]


def _snippet(body: List[str], terms: List[str], n: int = 3) -> List[str]:
    """The *n* lines that hold the most query words, in their order; the first lines when none do."""
    text = [ln.strip() for ln in body if ln.strip() and not ln.lstrip().startswith("```")]
    scored = sorted(range(len(text)), key=lambda i: -sum(t in _words(text[i]) for t in terms))
    best = sorted(i for i in scored[:n] if any(t in _words(text[i]) for t in terms))
    picked = [text[i] for i in best] or text[:n]
    return [ln if len(ln) <= 200 else ln[:199] + "…" for ln in picked]


def read(root: Path, path: str, heading: Optional[str] = None) -> str:
    """One section of *path* (relative to *root*), its sub-sections with it; the whole file without
    *heading*. A heading matches exactly, else by the start of its text (``K3`` finds ``K3 · retry …``)."""
    root = Path(root).resolve()
    target = (root / path).resolve()
    if root not in target.parents or not target.is_file():
        raise KeyError(f"no file {path} in the project")
    lines = target.read_text(encoding="utf-8", errors="replace").splitlines()
    if not heading:
        return "\n".join(lines)
    want = heading.strip().lstrip("#").strip().lower()
    found = list(sections(target))
    pick = next((s for s in found if s[2].lower() == want), None) or next(
        (s for s in found if s[2].lower().startswith(want)), None
    )
    if pick is None:
        raise KeyError(f"no heading {heading!r} in {path}")
    start, level = pick[0], pick[1]
    end = next((s[0] for s in found if s[0] > start and 0 < s[1] <= level), len(lines) + 1)
    return "\n".join(lines[start - 1 : end - 1]).rstrip()


def text(hits: List[Hit]) -> str:
    """The hits as an assistant reads them: heading, three lines, ``path:line``."""
    if not hits:
        return "nothing matched"
    out = []
    for h in hits:
        out.append(f"{h.heading} — {h.path}:{h.line}")
        out += [f"  {ln}" for ln in h.lines]
    return "\n".join(out)
