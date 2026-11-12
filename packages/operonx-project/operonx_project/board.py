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

The board is cut into cycles (CYCLES_PLAN): a cycle is a plan, its phases are its cards::

    ## FF · One flow per feature · open
    plan: docs/FEATURE_FLOWS_PLAN.md · opened: 2026-11-03

Cards before any ``##``, or under ``## Backlog``, are the Backlog. :func:`close` refuses while a card of the
cycle is not done; a closed cycle keeps its cards' heading lines only.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path, PurePosixPath
from typing import Dict, List, Optional, Tuple

__all__ = ["BACKLOG", "FIELDS", "FIXED", "PATH", "STATES", "BoardError", "Card", "Cycle", "check", "close", "lane",
           "move", "new_names", "open_cycle", "parse", "parse_all", "put", "read", "read_all", "render", "view",
           "waits_on"]  # fmt: skip

PATH = "docs/brd/board.md"
STATES = ("todo", "doing", "needs you", "done")
FIELDS = ("req", "after", "files", "done")
#: The file names a feature holds (LEAN_PROJECT_PLAN §2); anything else is ``_private.py``.
FIXED = {"__init__", "routes", "jobs", "graph", "ops", "rules", "store", "source", "prompts", "agent"}
_HEAD = re.compile(r"^### (C\d+) · (.+?) · (todo|doing|needs you|done)\s*$")
_CYCLE = re.compile(r"^## ([A-Z][A-Z0-9]*) · (.+?) · (open|closed)\s*$")
_CYCLE_ID = re.compile(r"^[A-Z][A-Z0-9]*$")
BACKLOG = "Backlog"
CYCLE_FIELDS = ("plan", "opened", "closed")
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
    cycle: str = ""  # the cycle's id; "" is the Backlog

    @property
    def kind(self) -> str:
        return "build" if self.req else "fix"


@dataclass
class Cycle:
    id: str
    title: str
    state: str = "open"
    plan: str = ""
    opened: str = ""
    closed: str = ""
    note: str = ""


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


def parse_all(text: str) -> Tuple[List[Cycle], List[Card]]:
    """The cycles and the cards, in file order; each card's ``cycle`` is its section's id (``""``: the Backlog)."""
    cycles: List[Cycle] = []
    cards: List[Card] = []
    at = ""  # the section: a cycle's id, or "" (the Backlog)
    last = None  # what the next lines belong to: a cycle (before its first card) or a card
    for n, line in enumerate(text.splitlines(), 1):
        m = _HEAD.match(line)
        if m:
            last = Card(id=m.group(1), title=m.group(2).strip(), state=m.group(3), cycle=at)
            cards.append(last)
            continue
        if line.startswith("### "):
            raise BoardError(f"line {n}: a card is `### C<n> · <title> · <{'|'.join(STATES)}>`")
        if line.startswith("## "):
            m = _CYCLE.match(line)
            if line.strip() == f"## {BACKLOG}":
                at, last = "", None
            elif m:
                if any(c.id == m.group(1) for c in cycles):
                    raise BoardError(f"line {n}: two cycles are {m.group(1)}")
                last = Cycle(id=m.group(1), title=m.group(2).strip(), state=m.group(3))
                cycles.append(last)
                at = last.id
            else:
                raise BoardError(f"line {n}: a cycle is `## <ID> · <title> · open|closed`, or `## {BACKLOG}`")
            continue
        if last is None or (not line.strip() and not last.note):
            continue
        bare = line[2:] if line.startswith(("- ", "* ")) else line  # a field line written as a list item
        parts = [p.split(":", 1) for p in bare.split(" · ")]
        names = FIELDS if isinstance(last, Card) else CYCLE_FIELDS
        if not last.note and all(len(p) == 2 and p[0].strip() in names for p in parts):
            for key, value in parts:
                setattr(last, key.strip(), _list(value) if isinstance(last, Card) else value.strip())
        else:
            last.note = f"{last.note}\n{line}" if last.note else line
    for x in [*cycles, *cards]:
        x.note = x.note.strip()
    return cycles, cards


def parse(text: str) -> List[Card]:
    return parse_all(text)[1]


def _card_lines(c: Card, short: bool = False) -> List[str]:
    out = [f"### {c.id} · {c.title} · {c.state}"]
    if not short:
        head = " · ".join(f"{k}: {', '.join(getattr(c, k))}" for k in ("req", "after") if getattr(c, k))
        out += [head] if head else []
        out += [f"{k}: {', '.join(getattr(c, k))}" for k in ("files", "done") if getattr(c, k)]
        out += ["", c.note] if c.note else []
    out.append("")
    return out


def render(cards: List[Card], top: str = _TOP, cycles: Optional[List[Cycle]] = None) -> str:
    """The file: the top, then the open cycles, the Backlog, the closed cycles (their cards' headings only).
    With no cycle the cards follow the top as they always did."""
    cycles = cycles or []
    out = [top.rstrip("\n"), ""]
    if cycles:  # a section lists its cards by id, so a card moved between sections lands in its place
        cards = sorted(cards, key=lambda c: int(c.id[1:]))
    known = {y.id for y in cycles}
    backlog = [c for c in cards if c.cycle not in known]
    if not cycles:
        for c in backlog:
            out += _card_lines(c)
        return "\n".join(out)
    for y in [y for y in cycles if y.state == "open"] + [y for y in cycles if y.state == "closed"]:
        if y.state == "closed" and backlog is not None:
            out += [f"## {BACKLOG}", ""] + [x for c in backlog for x in _card_lines(c)]
            backlog = None
        out.append(f"## {y.id} · {y.title} · {y.state}")
        meta = " · ".join(f"{k}: {getattr(y, k)}" for k in CYCLE_FIELDS if getattr(y, k))
        out += [meta] if meta else []
        out += ["", y.note] if y.note else []
        out.append("")
        for c in cards:
            if c.cycle == y.id:
                out += _card_lines(c, short=y.state == "closed")
    if backlog is not None:
        out += [f"## {BACKLOG}", ""] + [x for c in backlog for x in _card_lines(c)]
    return "\n".join(out)


def _top(text: str) -> str:
    m = re.search(r"^##", text, re.M)
    return text if m is None else text[: m.start()]


def read_all(repo: Path) -> Optional[Tuple[List[Cycle], List[Card]]]:
    """The board's cycles and cards, or ``None`` when the project has no board."""
    f = Path(repo) / PATH
    return parse_all(f.read_text(encoding="utf-8")) if f.is_file() else None


def read(repo: Path) -> Optional[List[Card]]:
    """The board's cards, or ``None`` when the project has no board."""
    got = read_all(repo)
    return None if got is None else got[1]


def _load(repo: Path) -> Tuple[Path, str, List[Cycle], List[Card]]:
    f = Path(repo) / PATH
    text = f.read_text(encoding="utf-8") if f.is_file() else _TOP
    return (f, text, *parse_all(text))


def _save(f: Path, text: str, cycles: List[Cycle], cards: List[Card]) -> None:
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(render(cards, _top(text), cycles), encoding="utf-8")


def _closed(cycles: List[Cycle]) -> set:
    return {y.id for y in cycles if y.state == "closed"}


def check(cards: List[Card], defined: Optional[set] = None, cycles: Optional[List[Cycle]] = None) -> List[str]:
    """What is wrong with a board: a duplicate id, an ``after`` naming no card, a ``req`` not defined, a card
    done with no ``done`` test, a card doing whose ``after`` is not done. A closed cycle's cards are its record:
    only their ids are checked."""
    errors: List[str] = []
    ids = [c.id for c in cards]
    closed = _closed(cycles or [])
    for c in cards:
        if ids.count(c.id) > 1:
            errors.append(f"{c.id}: two cards have this id")
        if c.cycle in closed:
            continue
        errors += [f"{c.id}: after {a}, which is no card" for a in c.after if a not in ids]
        if defined is not None:
            errors += [f"{c.id}: req {r} is not a requirement" for r in c.req if r not in defined]
        if c.state == "done" and not c.done:
            errors.append(f"{c.id}: done, with no done test (a `done:` line under its heading)")
        if c.state == "doing" and waits_on(c, cards):
            errors.append(f"{c.id}: doing, but {', '.join(waits_on(c, cards))} is not done")
    return errors


def _open_cycle_id(cycles: List[Cycle], cycle: str) -> str:
    """*cycle* as a section id: ``""`` or ``Backlog`` is the Backlog; anything else must be an open cycle."""
    if cycle in ("", BACKLOG):
        return ""
    y = next((y for y in cycles if y.id == cycle), None)
    if y is None:
        raise BoardError(f"no cycle {cycle} in {PATH} (open one: cycle open {cycle} TITLE)")
    if y.state != "open":
        raise BoardError(f"cycle {cycle} is closed")
    return y.id


def put(repo: Path, cid: str = "", title: str = "", cycle: Optional[str] = None, **fields: List[str]) -> Card:
    """Add a card (no *cid*: the next id, state todo) or change one's title and fields (``req``, ``after``,
    ``files``, ``done``: each a list); *cycle* puts it in an open cycle (``""`` or ``Backlog``: the Backlog; a new
    card goes to the Backlog when none is given). The board is made when the project has none."""
    bad = set(fields) - set(FIELDS)
    if bad:
        raise BoardError(f"{', '.join(sorted(bad))}: a card's fields are {', '.join(FIELDS)}")
    f, text, cycles, cards = _load(repo)
    if cid:
        c = next((x for x in cards if x.id == cid), None)
        if c is None:
            raise BoardError(f"no card {cid} in {PATH}")
        if c.cycle in _closed(cycles):
            raise BoardError(f"{cid} is in the closed cycle {c.cycle}")
    else:
        if not title.strip():
            raise BoardError("a new card needs a title")
        c = Card(id=f"C{max((int(x.id[1:]) for x in cards), default=0) + 1}", title="")
        cards.append(c)
    c.title = " ".join(title.split()) or c.title
    if cycle is not None:
        c.cycle = _open_cycle_id(cycles, cycle)
    for k, v in fields.items():
        setattr(c, k, [str(x).strip() for x in v if str(x).strip()])
    errors = [e for e in check(cards, cycles=cycles) if e.startswith(f"{c.id}:")]
    if errors:
        raise BoardError("; ".join(errors))
    _save(f, text, cycles, cards)
    return c


def open_cycle(repo: Path, cid: str, title: str, plan: str = "") -> Cycle:
    """Open a cycle: ``## <cid> · <title> · open``, its plan doc and today's date. Its cards come with
    :func:`put` (``cycle=cid``)."""
    if not _CYCLE_ID.match(cid or "") or cid == BACKLOG:
        raise BoardError(f"{cid!r}: a cycle's id is capitals and digits (FF, K2)")
    if not " ".join(title.split()):
        raise BoardError("a cycle needs a title")
    f, text, cycles, cards = _load(repo)
    if any(y.id == cid for y in cycles):
        raise BoardError(f"there is a cycle {cid} already")
    y = Cycle(id=cid, title=" ".join(title.split()), plan=plan.strip(), opened=date.today().isoformat())
    cycles.append(y)
    _save(f, text, cycles, cards)
    return y


def close(repo: Path, cid: str) -> Cycle:
    """Close cycle *cid*. Refused while one of its cards is not done: move each to the Backlog or to another open
    cycle first (``put CARD --cycle Backlog``), so no card is left behind in a closed cycle."""
    f, text, cycles, cards = _load(repo)
    y = next((y for y in cycles if y.id == cid), None)
    if y is None:
        raise BoardError(f"no cycle {cid} in {PATH}")
    if y.state == "closed":
        raise BoardError(f"cycle {cid} is closed already")
    left = [c.id for c in cards if c.cycle == cid and c.state != "done"]
    if left:
        raise BoardError(f"cycle {cid} has cards not done: {', '.join(left)}. Finish them, or move each to the "
                         f"Backlog or another open cycle (put {left[0]} --cycle {BACKLOG})")
    y.state, y.closed = "closed", date.today().isoformat()
    _save(f, text, cycles, cards)
    return y


def move(repo: Path, cid: str, to: str, note: Optional[str] = None) -> Card:
    """Move card *cid* to *to* and write the board. ``doing`` refuses while an ``after`` card is not done, and
    lands in ``needs you`` when the card adds a file outside the fixed names, until the owner says go (a move to
    ``doing`` with a *note*: their answer). ``done`` needs ``done`` tests (the caller runs them). *note*
    replaces the card's note (``""`` clears it)."""
    if to not in STATES:
        raise BoardError(f"{to!r}: a state is one of {', '.join(STATES)}")
    if not (Path(repo) / PATH).is_file():
        raise BoardError(f"no board yet ({PATH})")
    f, text, cycles, cards = _load(repo)
    c = next((x for x in cards if x.id == cid), None)
    if c is None:
        raise BoardError(f"no card {cid} in {PATH}")
    if c.cycle in _closed(cycles):
        raise BoardError(f"{cid} is in the closed cycle {c.cycle}")
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
    _save(f, text, cycles, cards)
    return c


def view(cards: List[Card], cycles: Optional[List[Cycle]] = None) -> Dict[str, object]:
    """The board as JSON: each card with its cycle, lane, kind and what it waits on; the cycles with their cards
    and how many are done; the ids per state, of the cards not in a closed cycle."""
    cycles = cycles or []
    closed = _closed(cycles)
    rows = [{"id": c.id, "title": c.title, "state": c.state, "req": c.req, "after": c.after, "files": c.files,
             "done": c.done, "note": c.note, "kind": c.kind, "lane": lane(c.files), "waits": waits_on(c, cards),
             "cycle": c.cycle}
            for c in cards]  # fmt: skip
    ys = [{"id": y.id, "title": y.title, "state": y.state, "plan": y.plan, "opened": y.opened, "closed": y.closed,
           "note": y.note, "cards": [c.id for c in cards if c.cycle == y.id],
           "done": sum(1 for c in cards if c.cycle == y.id and c.state == "done")}
          for y in cycles]  # fmt: skip
    live = [c for c in cards if c.cycle not in closed]
    return {"cards": rows, "cycles": ys, "columns": {s: [c.id for c in live if c.state == s] for s in STATES}}
