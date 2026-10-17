"""The source register: ``<brd>/sources/README.md`` (plan §4.1).

Each source has a row: ``SRC-nn``, what it is, its kind, where it lives,
its SHA-256 and its role (*authoritative*, *approved* or *proposed*).
Sources are never edited; a new version is a new row. The Markdown table
is the record a person reads, and the engine reads it back.

A **private** source (a client's BRD) is identified only by its path and
hash and is never committed: its path goes into the repo's
``.git/info/exclude``, and its hash into the guard that refuses any commit
containing its bytes, under any name (``Runtime.private``).
"""

from __future__ import annotations

import datetime as _dt
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

from operonx_project.brd.readers import sha256

__all__ = ["ROLES", "Source", "add_owner_request", "load_register", "private_hashes", "register_source"]

ROLES = ("authoritative", "approved", "proposed")

_HEADER = "| ID | Source | Kind | Where it lives | Identity | Role |\n|---|---|---|---|---|---|\n"
_ROW = re.compile(r"^\|\s*(SRC-\d{2,3})\s*\|(.*)\|\s*$")
_SHA = re.compile(r"\b[0-9a-f]{64}\b")
_PATH = re.compile(r"`([^`]+)`")


@dataclass
class Source:
    id: str
    title: str
    kind: str
    where: str
    sha256: List[str] = field(default_factory=list)
    role: str = "authoritative"
    private: bool = False

    def row(self) -> str:
        ident = ", ".join(f"SHA-256 `{h}`" for h in self.sha256) or "git"
        role = f"**{self.role.capitalize()}**" + (", **private**: never committed" if self.private else "")
        cells = [self.id, self.title, self.kind, self.where, ident, role]
        return "| " + " | ".join(c.replace("|", "\\|").replace("\n", " ") for c in cells) + " |"

    @property
    def local_path(self) -> Optional[str]:
        """The file a row names in backticks (a private source's local path)."""
        m = _PATH.search(self.where)
        return m.group(1) if m else None


def _file(brd: Path) -> Path:
    return Path(brd) / "sources" / "README.md"


def _cells(rest: str) -> List[str]:
    return [c.strip().replace("\\|", "|") for c in re.split(r"(?<!\\)\|", rest)]


def load_register(brd: Path) -> List[Source]:
    f = _file(brd)
    if not f.exists():
        return []
    out: List[Source] = []
    for line in f.read_text(encoding="utf-8").splitlines():
        m = _ROW.match(line)
        if not m:
            continue
        cells = _cells(m.group(2))
        cells += [""] * (5 - len(cells))
        title, kind, where, ident, role = cells[:5]
        low = role.lower()
        r = next((x for x in ROLES if x in low), "authoritative")
        out.append(Source(m.group(1), title, kind, where, _SHA.findall(ident), r, "private" in low))
    return out


def private_hashes(brd: Path) -> Dict[str, str]:
    """sha256 → the source's title, for every private source: what the guard refuses."""
    return {h: s.title for s in load_register(brd) if s.private for h in s.sha256}


def _next_id(rows: List[Source]) -> str:
    n = max((int(s.id.split("-")[1]) for s in rows), default=0) + 1
    return f"SRC-{n:02d}"


def _write(brd: Path, rows: List[Source]) -> None:
    f = _file(brd)
    f.parent.mkdir(parents=True, exist_ok=True)
    if f.exists():
        text = f.read_text(encoding="utf-8")
        lines = text.splitlines()
        idx = [i for i, ln in enumerate(lines) if _ROW.match(ln)]
        if idx:
            new = lines[: idx[0]] + [s.row() for s in rows] + lines[idx[-1] + 1 :]
            f.write_text("\n".join(new) + "\n", encoding="utf-8")
            return
        f.write_text(text.rstrip("\n") + "\n\n" + _HEADER + "\n".join(s.row() for s in rows) + "\n", encoding="utf-8")
        return
    f.write_text(
        "# Sources\n\nEvery document the requirements are derived from. Sources are **immutable**: they are "
        "never edited to match the product. A new version of a source is a new row; the old row stays.\n\n"
        + _HEADER
        + "\n".join(s.row() for s in rows)
        + "\n",
        encoding="utf-8",
    )


def _exclude(repo: Path, path: Path) -> None:
    """Keep *path* out of git: a line in ``.git/info/exclude`` (shared by worktrees)."""
    try:
        common = subprocess.run(["git", "rev-parse", "--git-common-dir"], cwd=repo, capture_output=True,
                                text=True, check=True).stdout.strip()  # fmt: skip
    except (subprocess.CalledProcessError, FileNotFoundError):
        return
    git_dir = Path(common) if Path(common).is_absolute() else Path(repo) / common
    try:
        rel = "/" + str(Path(path).resolve().relative_to(Path(repo).resolve()))
    except ValueError:
        return  # outside the repo: git never sees it
    ex = git_dir / "info" / "exclude"
    ex.parent.mkdir(parents=True, exist_ok=True)
    lines = ex.read_text().splitlines() if ex.exists() else []
    if rel not in lines:
        ex.write_text("\n".join([*lines, rel]) + "\n")


def register_source(
    brd: Path,
    path: Path,
    role: str = "authoritative",
    title: Optional[str] = None,
    kind: Optional[str] = None,
    private: bool = False,
    repo: Optional[Path] = None,
    where: Optional[str] = None,
) -> Source:
    """Register *path* (a new row), or return the row that already has its
    hash: registering the same bytes twice is one source."""
    if role not in ROLES:
        raise ValueError(f"role must be one of {ROLES}, not {role!r}")
    path = Path(path)
    digest = sha256(path)
    rows = load_register(brd)
    for s in rows:
        if digest in s.sha256:
            return s
    size = path.stat().st_size
    kind = kind or f"{path.suffix.lstrip('.') or 'file'}, {size:,} bytes"
    if private:
        where = f"**Not in git.** Local path: `{path.resolve()}`"
        if repo is not None:
            _exclude(repo, path)
    elif where is None:
        try:
            where = f"`{path.resolve().relative_to(Path(brd).resolve())}`"
        except ValueError:
            where = f"`{path.resolve()}`"
    src = Source(_next_id(rows), title or path.name, kind, where, [digest], role, private)
    _write(brd, rows + [src])
    return src


def _slug(text: str, n: int = 6) -> str:
    words = re.findall(r"[a-z0-9]+", text.lower())[:n]
    return "-".join(words) or "request"


def add_owner_request(brd: Path, text: str, date: Optional[str] = None) -> Source:
    """A goal in a sentence, a chat message: kept verbatim as a source, approved."""
    date = date or _dt.date.today().isoformat()
    d = Path(brd) / "sources" / "owner_requests"
    d.mkdir(parents=True, exist_ok=True)
    f = d / f"{date}_{_slug(text)}.md"
    n = 2
    while f.exists() and f.read_text(encoding="utf-8") != text:
        f = d / f"{date}_{_slug(text)}-{n}.md"
        n += 1
    f.write_text(text, encoding="utf-8")
    return register_source(brd, f, role="approved", title=f"Owner request of {date}", kind="Markdown, verbatim")
