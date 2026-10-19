"""The board in the BRD: ``docs/brd/board/``, one file per milestone.

The board is the project's plan: every milestone and every card, with what
the card does, the rows it serves, its criteria, its dependencies, the
files it may touch, and its status. It lives in git beside the
requirements (plan D1), so a clone carries it and a PR shows how it
changed. ``.operonx/project/`` keeps only what a running engine needs, and
is rebuilt from these files when it is missing (:func:`rebuild`).

The engine writes the files (:func:`write_board`) at the moments that
matter: a plan approved, a card ending, a milestone moving. It reads them
back (:func:`read_board`) to rebuild a store or to seed a new milestone
with an old one's cards (``start --from-board``). Store → files → store is
the contract (``tests/test_board_files.py``).

The format is Markdown a person reads in a PR. What a reader needs to
rebuild exactly and a person does not (the policy, timestamps) is in one
HTML comment per milestone, which Markdown does not render.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple

from operonx_project.model import Card, CardState, Milestone, MilestoneState, Policy, Stage

__all__ = ["BOARD_DIR", "BoardFile", "board_text", "index_text", "read_board", "rebuild", "write_board"]

#: The board's folder inside the BRD folder.
BOARD_DIR = "board"
_META = re.compile(r"^<!-- operonx-project: (\{.*\}) -->$")
_FIELD = re.compile(r"^- ([A-Z][a-z]+): ?(.*)$")
_CARD_HEAD = re.compile(r"^### (\S+) · (.*)$")
_MS_HEAD = re.compile(r"^# (\S+) · (.*)$")
_FILE = re.compile(r"^m\d+\.md$|^[a-z][\w-]*\.md$")


def _esc(text: Any) -> str:
    """One line: a cell or a bullet. ``|`` and newlines are escaped."""
    return str(text or "").replace("\\", "\\\\").replace("|", "\\|").replace("\n", "<br>")


def _unesc(text: str) -> str:
    out, i = [], 0
    while i < len(text):
        if text.startswith("<br>", i):
            out.append("\n")
            i += 4
        elif text[i] == "\\" and i + 1 < len(text) and text[i + 1] in "\\|":
            out.append(text[i + 1])
            i += 2
        else:
            out.append(text[i])
            i += 1
    return "".join(out)


def _list(items: Iterable[str]) -> str:
    return ", ".join(_esc(x).replace(",", "\\,") for x in items)


def _unlist(text: str) -> List[str]:
    parts = re.split(r"(?<!\\), ", text.strip()) if text.strip() else []
    return [_unesc(p.replace("\\,", ",")) for p in parts]


def _mode(policy: Policy) -> str:
    return "w1" if policy.horizon == "all" else "w2"


def board_text(ms: Milestone, cards: List[Card]) -> str:
    """One milestone's file."""
    b = ms.policy.budget
    lines = [
        f"# {ms.id} · {_esc(ms.title)}",
        "",
        "| Field | Value |",
        "|---|---|",
        f"| State | {ms.state.value} |",
        f"| Mode | {_mode(ms.policy)} |",
        f"| Rows | {_list(ms.rows) or 'every row not verified'} |",
        f"| Budget | ${b.max_usd:.2f}, {b.max_hours:g} h · spent ${ms.cost_usd:.2f} |",
        f"| Replans | {ms.replans} |",
        f"| Reason | {_esc(ms.reason)} |",
        "",
        "## Goal",
        "",
        *(ms.goal.splitlines() or ["—"]),
        "",
        "## Cards",
        "",
        "| Card | Title | Rows | Deps | Kind | Status |",
        "|---|---|---|---|---|---|",
    ]
    for c in cards:
        lines.append(f"| {c.id} | {_esc(c.title)} | {_list(c.rows)} | {_list(c.deps)} | {c.kind} | {c.state.value} |")
    for c in cards:
        lines += [
            "",
            f"### {c.id} · {_esc(c.title)}",
            "",
            f"- Intent: {_esc(c.intent)}",
            f"- Rows: {_list(c.rows)}",
            f"- Deps: {_list(c.deps)}",
            f"- Files: {_list(c.paths)}",
            f"- Kind: {c.kind}",
            f"- Risk: {c.risk}",
            f"- Priority: {c.priority}",
            f"- Epic: {_esc(c.epic)}",
            "- Criteria:",
            *[f"  - {_esc(x)}" for x in c.criteria],
            f"- Status: {c.state.value}",
            f"- Stage: {c.stage.value if c.stage else ''}",
            f"- Reopens: {c.reopens}",
            f"- Outcome: {_esc(c.reason)}",
            f"- Run: {c.run_id or ''}",
            f"- Cost: {c.cost_usd:.4f}",
        ]
    meta = {"policy": ms.policy.to_dict(), "created": ms.created, "updated": ms.updated,
            "times": {c.id: [c.created, c.updated] for c in cards}}  # fmt: skip
    lines += ["", f"<!-- operonx-project: {json.dumps(meta, sort_keys=True)} -->", ""]
    return "\n".join(lines)


def index_text(milestones: List[Tuple[Milestone, List[Card]]]) -> str:
    """``README.md``: every milestone in one table."""
    lines = [
        "# Board",
        "",
        "Every milestone of this project and its cards: the plan, and how far it is. The engine",
        "(`operonx-project`) writes these files; change a plan with `operonx-project replan`, not by hand.",
        "",
        "| Milestone | Title | State | Cards | Done | File |",
        "|---|---|---|---|---|---|",
    ]
    for ms, cards in milestones:
        done = sum(c.state == CardState.DONE for c in cards)
        link = f"[{ms.id}.md]({ms.id}.md)"
        lines.append(f"| {ms.id} | {_esc(ms.title)} | {ms.state.value} | {len(cards)} | {done} | {link} |")
    return "\n".join(lines) + "\n"


def write_board(brd: Path, milestones: List[Tuple[Milestone, List[Card]]]) -> List[Path]:
    """Write every milestone's file and the index under *brd*/board; returns what changed."""
    folder = Path(brd) / BOARD_DIR
    folder.mkdir(parents=True, exist_ok=True)
    changed: List[Path] = []
    want = {f"{ms.id}.md": board_text(ms, cards) for ms, cards in milestones}
    want["README.md"] = index_text(milestones)
    for name, text in want.items():
        path = folder / name
        if not path.exists() or path.read_text(encoding="utf-8") != text:
            path.write_text(text, encoding="utf-8")
            changed.append(path)
    return changed


@dataclass
class BoardFile:
    milestone: Milestone
    cards: List[Card] = field(default_factory=list)


def _parse(text: str, project: str) -> BoardFile:
    lines = text.splitlines()
    head = _MS_HEAD.match(lines[0]) if lines else None
    if head is None:
        raise ValueError("a board file starts with '# <milestone> · <title>'")
    mid, title = head.group(1), _unesc(head.group(2))
    fields: Dict[str, str] = {}
    meta: Dict[str, Any] = {}
    goal: List[str] = []
    cards: List[Dict[str, Any]] = []
    section = ""
    for line in lines[1:]:
        m = _META.match(line)
        if m:
            meta = json.loads(m.group(1))
            continue
        if line.startswith("## "):
            section = line[3:].strip()
            continue
        c = _CARD_HEAD.match(line)
        if c:
            section = "card"
            cards.append({"id": c.group(1), "title": _unesc(c.group(2)), "criteria": []})
            continue
        if section == "" and line.startswith("| ") and not line.startswith("| Field"):
            cells = re.split(r"(?<!\\)\|", line.strip().strip("|"))
            if len(cells) >= 2:
                fields[cells[0].strip()] = cells[1].strip()
        elif section == "Goal":
            goal.append(line)
        elif section == "card" and cards:
            if line.startswith("  - "):
                cards[-1]["criteria"].append(_unesc(line[4:]))
                continue
            f = _FIELD.match(line)
            if f and f.group(1) != "Criteria":
                cards[-1][f.group(1).lower()] = f.group(2)
    policy = Policy.from_dict(meta.get("policy"))
    rows = fields.get("Rows", "")
    goal_text = "\n".join(goal).strip("\n")
    ms = Milestone(id=mid, project=project, title=title, goal="" if goal_text == "—" else goal_text,
                   rows=[] if rows == "every row not verified" else _unlist(rows),
                   state=MilestoneState(fields.get("State", "created")), policy=policy,
                   reason=_unesc(fields.get("Reason", "")), replans=int(fields.get("Replans", "0") or 0),
                   created=float(meta.get("created") or 0), updated=float(meta.get("updated") or 0))  # fmt: skip
    spent = re.search(r"spent \$([\d.]+)", fields.get("Budget", ""))
    ms.cost_usd = float(spent.group(1)) if spent else 0.0
    times = meta.get("times") or {}
    out: List[Card] = []
    for c in cards:
        t = times.get(c["id"]) or [0.0, 0.0]
        out.append(Card(
            id=c["id"], milestone=mid, title=c["title"], intent=_unesc(c.get("intent", "")),
            rows=_unlist(c.get("rows", "")), criteria=c["criteria"], deps=_unlist(c.get("deps", "")),
            paths=_unlist(c.get("files", "")), kind=c.get("kind", "code") or "code", risk=c.get("risk", "low") or "low",
            priority=int(c.get("priority") or 0), epic=_unesc(c.get("epic", "")),
            state=CardState(c.get("status") or "pending"), stage=Stage(c["stage"]) if c.get("stage") else None,
            reason=_unesc(c.get("outcome", "")), reopens=int(c.get("reopens") or 0), run_id=c.get("run") or None,
            cost_usd=float(c.get("cost") or 0.0), created=float(t[0]), updated=float(t[1]),
        ))  # fmt: skip
    return BoardFile(milestone=ms, cards=out)


def read_board(brd: Path, project: str = "") -> List[BoardFile]:
    """Every milestone file under *brd*/board, oldest first."""
    folder = Path(brd) / BOARD_DIR
    if not folder.is_dir():
        return []
    got = [_parse(p.read_text(encoding="utf-8"), project) for p in folder.glob("*.md")
           if p.name != "README.md" and _FILE.match(p.name)]  # fmt: skip
    return sorted(got, key=lambda b: (b.milestone.created, b.milestone.id))


#: A milestone that was moving when its board was written comes back paused:
#: its worktrees and runs are not in git, so the owner says when it goes on.
_STILL = {MilestoneState.CREATED, MilestoneState.AWAITING_APPROVAL, MilestoneState.PAUSED, MilestoneState.DONE,
          MilestoneState.FAILED, MilestoneState.CANCELLED, MilestoneState.BUDGET_EXHAUSTED}  # fmt: skip
_CARD_BACK = {CardState.RUNNING: CardState.READY, CardState.WAITING_HUMAN: CardState.READY,
              CardState.VERIFIED: CardState.READY}  # fmt: skip


def rebuild(store: Any, brd: Path, project: str = "") -> List[str]:
    """Fill an empty *store* from the board files; returns the milestones made.

    A milestone that was working comes back ``paused`` (reason: rebuilt), and
    a card that was mid-flight comes back ``ready``: what it had done lived
    in a worktree, not in git. Its run id stays, as a link."""
    made: List[str] = []
    for bf in read_board(brd, project):
        ms = bf.milestone
        if ms.state not in _STILL:
            ms.reason = f"rebuilt from docs/brd/board (it was {ms.state.value}); unpause it to go on"
            ms.state = MilestoneState.PAUSED
        store.add_milestone(ms, actor="board")
        for c in bf.cards:
            if c.state in _CARD_BACK:
                c.reason = f"rebuilt from docs/brd/board (it was {c.state.value})"
                c.state, c.stage = _CARD_BACK[c.state], None
        store.add_cards(bf.cards, actor="board")
        store.ms_put(ms.id, "rebuilt", {"from": str(Path(brd) / BOARD_DIR)})
        made.append(ms.id)
    return made
