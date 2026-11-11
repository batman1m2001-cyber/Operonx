"""The board: ``docs/brd/board.md``, one card per heading (LEAN_PROJECT_PLAN §5)::

    ### C4 · gold jump alert · doing
    req: NEWS-07 · after: C2
    files: src/news/rules.py, tests/news/test_rules.py
    done: tests/news/test_rules.py::test_gold_jump

Four fields (``req``, ``after``, ``files``, ``done``), four states (``todo``, ``doing``, ``needs you``,
``done``). What a card is *not* written is read: its lane from ``files``, its kind from ``req`` (a requirement
is *build*, none is *fix*), and whether it waits (an ``after`` card not done). Text under the fields is the
card's note: the question when it needs the owner. The file is the record, in git: the assistant moves a card
with :func:`move`, the owner reads it here or on the studio's Board.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Dict, List, Optional

__all__ = ["FIELDS", "FIXED", "PATH", "STATES", "BoardError", "Card", "check", "lane", "move", "new_names",
           "parse", "put", "read", "render", "view", "waits_on"]  # fmt: skip

PATH = "docs/brd/board.md"
STATES = ("todo", "doing", "needs you", "done")
FIELDS = ("req", "after", "files", "done")
#: The file names a feature holds (LEAN_PROJECT_PLAN §2); anything else is ``_private.py``.
FIXED = {"__init__", "routes", "jobs", "graph", "ops", "rules", "store", "source", "prompts", "agent"}
_HEAD = re.compile(r"^### (C\d+) · (.+?) · (todo|doing|needs you|done)\s*$")
_LANE = {"routes": "web", "ops": "core", "rules": "core", "graph": "core", "store": "data", "source": "data",
         "jobs": "data", "prompts": "AI", "agent": "AI"}  # fmt: skip
_TOP = (
    "# Board\n\nOne card per task (LEAN_PROJECT_PLAN §5). The assistant moves the cards; edit a card's fields, "
    "not its state.\n"
)


class BoardError(ValueError):
    pass


@dataclass
class Card:
    id: str
    title: str
    state: str = "todo"
    req: List[str] = field(default_factory=list)
    after: List[str] = field(default_factory=list)
    files: List[str] = field(default_factory=list)
    done: List[str] = field(default_factory=list)
    note: str = ""

    @property
    def kind(self) -> str:
        return "build" if self.req else "fix"


def lane(files: List[str]) -> str:
    """The lane, from the first file that is not a test: ``web/`` UI, ``routes`` web, ``ops``/``rules``/``graph``
    core, ``store``/``source``/``jobs`` data, ``prompts``/``agent`` AI, the rest platform."""
    for f in files:
        p = PurePosixPath(f)
        if p.parts[:1] == ("tests",):
            continue
        if p.parts[:1] == ("web",):
            return "UI"
        if p.parts[:1] == ("src",) and len(p.parts) > 2 and not p.parts[1].startswith("_"):
            return _LANE.get(p.stem, "core")
        return "platform"
    return "core"


def new_names(card: Card) -> List[str]:
    """The files a card adds to a feature outside the fixed names (``src/<feature>/<name>.py``)."""
    out = []
    for f in card.files:
        p = PurePosixPath(f)
        if p.parts[:1] == ("src",) and len(p.parts) == 3 and not p.parts[1].startswith("_") and p.suffix == ".py":
            if p.stem not in FIXED and not p.stem.startswith("_"):
                out.append(f)
    return out


def waits_on(card: Card, cards: List[Card]) -> List[str]:
    """The ``after`` cards not done yet."""
    done = {c.id for c in cards if c.state == "done"}
    return [a for a in card.after if a not in done]


def _list(value: str) -> List[str]:
    """``a, b`` → ``["a", "b"]``; ``none`` (or ``-``) is an empty field, as a hand-written card often says."""
    value = value.strip()
    if value.lower().startswith("none") or value in ("-", "—"):
        return []
    return [x.strip() for x in value.split(",") if x.strip()]


def parse(text: str) -> List[Card]:
    cards: List[Card] = []
    for n, line in enumerate(text.splitlines(), 1):
        m = _HEAD.match(line)
        if m:
            cards.append(Card(id=m.group(1), title=m.group(2).strip(), state=m.group(3)))
            continue
        if line.startswith("### "):
            raise BoardError(f"line {n}: a card is `### C<n> · <title> · <{'|'.join(STATES)}>`")
        if not cards or (not line.strip() and not cards[-1].note):
            continue
        c = cards[-1]
        bare = line[2:] if line.startswith(("- ", "* ")) else line  # a field line written as a list item
        parts = [p.split(":", 1) for p in bare.split(" · ")]
        if not c.note and all(len(p) == 2 and p[0].strip() in FIELDS for p in parts):
            for key, value in parts:
                setattr(c, key.strip(), _list(value))
        else:
            c.note = f"{c.note}\n{line}" if c.note else line
    for c in cards:
        c.note = c.note.strip()
    return cards


def render(cards: List[Card], top: str = _TOP) -> str:
    out = [top.rstrip("\n"), ""]
    for c in cards:
        out.append(f"### {c.id} · {c.title} · {c.state}")
        head = " · ".join(f"{k}: {', '.join(getattr(c, k))}" for k in ("req", "after") if getattr(c, k))
        out += [head] if head else []
        out += [f"{k}: {', '.join(getattr(c, k))}" for k in ("files", "done") if getattr(c, k)]
        out += ["", c.note] if c.note else []
        out.append("")
    return "\n".join(out)


def _top(text: str) -> str:
    i = text.find("\n### ")
    return text if i < 0 else text[: i + 1]


def read(repo: Path) -> Optional[List[Card]]:
    """The board's cards, or ``None`` when the project has no board."""
    f = Path(repo) / PATH
    return parse(f.read_text(encoding="utf-8")) if f.is_file() else None


def check(cards: List[Card], defined: Optional[set] = None) -> List[str]:
    """What is wrong with a board: a duplicate id, an ``after`` naming no card, a ``req`` not defined, a card
    done with no ``done`` test, a card doing whose ``after`` is not done."""
    errors: List[str] = []
    ids = [c.id for c in cards]
    for c in cards:
        if ids.count(c.id) > 1:
            errors.append(f"{c.id}: two cards have this id")
        errors += [f"{c.id}: after {a}, which is no card" for a in c.after if a not in ids]
        if defined is not None:
            errors += [f"{c.id}: req {r} is not a requirement" for r in c.req if r not in defined]
        if c.state == "done" and not c.done:
            errors.append(f"{c.id}: done, with no done test (a `done:` line under its heading)")
        if c.state == "doing" and waits_on(c, cards):
            errors.append(f"{c.id}: doing, but {', '.join(waits_on(c, cards))} is not done")
    return errors


def put(repo: Path, cid: str = "", title: str = "", **fields: List[str]) -> Card:
    """Add a card (no *cid*: the next id, state todo) or change one's title and fields (``req``, ``after``,
    ``files``, ``done``: each a list); the board is made when the project has none."""
    bad = set(fields) - set(FIELDS)
    if bad:
        raise BoardError(f"{', '.join(sorted(bad))}: a card's fields are {', '.join(FIELDS)}")
    f = Path(repo) / PATH
    text = f.read_text(encoding="utf-8") if f.is_file() else _TOP
    cards = parse(text)
    if cid:
        c = next((x for x in cards if x.id == cid), None)
        if c is None:
            raise BoardError(f"no card {cid} in {PATH}")
    else:
        if not title.strip():
            raise BoardError("a new card needs a title")
        c = Card(id=f"C{max((int(x.id[1:]) for x in cards), default=0) + 1}", title="")
        cards.append(c)
    c.title = " ".join(title.split()) or c.title
    for k, v in fields.items():
        setattr(c, k, [str(x).strip() for x in v if str(x).strip()])
    errors = [e for e in check(cards) if e.startswith(f"{c.id}:")]
    if errors:
        raise BoardError("; ".join(errors))
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(render(cards, _top(text)), encoding="utf-8")
    return c


def move(repo: Path, cid: str, to: str, note: Optional[str] = None) -> Card:
    """Move card *cid* to *to* and write the board. ``doing`` refuses while an ``after`` card is not done, and
    lands in ``needs you`` when the card adds a file outside the fixed names, until the owner says go (a move to
    ``doing`` with a *note*: their answer). ``done`` needs ``done`` tests (the caller runs them). *note*
    replaces the card's note (``""`` clears it)."""
    if to not in STATES:
        raise BoardError(f"{to!r}: a state is one of {', '.join(STATES)}")
    f = Path(repo) / PATH
    text = f.read_text(encoding="utf-8")
    cards = parse(text)
    c = next((x for x in cards if x.id == cid), None)
    if c is None:
        raise BoardError(f"no card {cid} in {PATH}")
    if to == "doing" and waits_on(c, cards):
        raise BoardError(f"{cid} waits on {', '.join(waits_on(c, cards))}")
    if to == "doing" and c.state != "needs you" and new_names(c):
        to = "needs you"
        note = f"Adds {', '.join(new_names(c))}, outside the fixed names: go?"
    if to == "done" and not c.done:
        raise BoardError(f"{cid} has no done test: give it one first (a `done:` line under its heading, "
                         "e.g. `done: tests/test_x.py::test_y`)")
    c.state = to
    if note is not None:
        c.note = note.strip()
    f.write_text(render(cards, _top(text)), encoding="utf-8")
    return c


def view(cards: List[Card]) -> Dict[str, object]:
    """The board as JSON: each card with its lane, kind and what it waits on; the ids per state."""
    rows = [{"id": c.id, "title": c.title, "state": c.state, "req": c.req, "after": c.after, "files": c.files,
             "done": c.done, "note": c.note, "kind": c.kind, "lane": lane(c.files), "waits": waits_on(c, cards)}
            for c in cards]  # fmt: skip
    return {"cards": rows, "columns": {s: [c.id for c in cards if c.state == s] for s in STATES}}
