"""A plan, as the planner answers it: its cards, and the engine's own checks on it.

The planner answers :data:`~operonx_project.prompts.PLAN_SCHEMA`; this
module turns that into :class:`~operonx_project.model.Card` objects and
checks it mechanically (plan §6, step 5): the graph is acyclic and every
dependency exists; every target row is covered (by a card's criterion
under W1, by an epic under W2, whose cards come later); every criterion
names a row of its card; paths stay inside the repo; at most ``max_cards``
cards. The reviewer is told what these checks found, and which cards'
paths overlap without a dependency between them.

A greenfield milestone gets card ``C0``, kind ``init``: the engine runs
``operonx init`` with the template the planner chose, and every card that
depends on nothing depends on it.
"""

from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set

from operonx_project.dag import DagError, paths_overlap, validate
from operonx_project.model import Card, CardState, Policy

__all__ = [
    "INIT_CARD",
    "cards_from_plan",
    "check_cards",
    "check_plan",
    "covered_rows",
    "criterion_row",
    "init_card",
    "overlaps",
]

INIT_CARD = "C0"
_ID = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,31}$")
_CRIT = re.compile(r"^\[([^\]]*)\]\s*")
_KINDS = ("code", "test", "docs", "config", "init")


def criterion_row(text: str) -> str:
    """The row a criterion string is tied to: ``"[R-01] WHEN …"`` → ``R-01``."""
    m = _CRIT.match(text)
    return m.group(1).strip() if m else ""


def _criterion(c: Any) -> str:
    if isinstance(c, dict):
        row, text = str(c.get("row") or "").strip(), str(c.get("text") or "").strip()
        return f"[{row}] {text}" if row else text
    return str(c)


def init_card(mid: str, init: Dict[str, Any]) -> Card:
    return Card(
        id=INIT_CARD,
        milestone=mid,
        title=f"create the project with `operonx init --template {init.get('template')}`",
        intent=init.get("why") or "the standard operonx layout every other card builds on",
        kind="init",
        paths=["**"],
        priority=-1,
        detail={"init": {k: init.get(k) for k in ("template", "toolchain", "name")}},
    )


def cards_from_plan(mid: str, items: Sequence[Dict[str, Any]], init: Optional[Dict[str, Any]] = None) -> List[Card]:
    """Cards from the planner's answer; with *init*, card ``C0`` first, and
    every card without dependencies depending on it."""
    out: List[Card] = []
    for n, c in enumerate(items):
        out.append(
            Card(
                id=str(c.get("id") or f"C{n + 1}").strip(),
                milestone=mid,
                title=str(c.get("title") or "").strip(),
                intent=str(c.get("intent") or "").strip(),
                rows=[str(r).strip() for r in c.get("rows") or [] if str(r).strip()],
                criteria=[_criterion(x) for x in c.get("criteria") or []],
                deps=[str(d).strip() for d in c.get("deps") or []],
                paths=[str(p).strip() for p in c.get("paths") or [] if str(p).strip()],
                kind=str(c.get("kind") or "code"),
                risk=str(c.get("risk") or "low"),
                priority=int(c.get("priority") or 0),
                epic=str(c.get("epic") or ""),
                detail={k: c[k] for k in ("docs", "estimate") if c.get(k)},
            )
        )
    if init:
        for c in out:
            if not c.deps and c.id != INIT_CARD:
                c.deps = [INIT_CARD]
        out.insert(0, init_card(mid, init))
    return out


def _bad_path(p: str) -> bool:
    return p.startswith("/") or p.startswith("~") or ".." in p.split("/") or bool(re.match(r"^[A-Za-z]:", p))


def check_cards(
    cards: Sequence[Card], scope: Iterable[str], existing: Sequence[Card] = (), max_cards: int = 0
) -> List[str]:
    """Problems with *cards*, alone or added to *existing* (replenished cards)."""
    problems: List[str] = []
    scope = set(scope)
    have = {c.id for c in existing}
    for c in cards:
        if not _ID.match(c.id):
            problems.append(f"card id {c.id!r} must be a letter then letters, digits, '-' or '_'")
        if c.id in have:
            problems.append(f"card id {c.id} is already used")
        if not c.title:
            problems.append(f"{c.id} has no title")
        if c.kind not in _KINDS:
            problems.append(f"{c.id}: kind {c.kind!r} is not one of {_KINDS[:-1]}")
        if c.risk not in ("low", "high"):
            problems.append(f"{c.id}: risk {c.risk!r} is not low or high")
        if c.kind == "init":
            continue
        if c.kind == "code" and not c.criteria:
            problems.append(f"{c.id} has no acceptance criterion")
        if c.kind == "code" and not c.paths:
            problems.append(f"{c.id} declares no paths")
        for p in c.paths:
            if _bad_path(p):
                problems.append(f"{c.id}: path {p!r} is outside the repo")
        if scope:
            for r in c.rows:
                if r not in scope:
                    problems.append(f"{c.id} serves {r}, which is not in the milestone's scope")
            for crit in c.criteria:
                r = criterion_row(crit)
                if not r:
                    problems.append(f"{c.id}: a criterion is tied to no row: {crit[:80]!r}")
                elif r not in c.rows:
                    problems.append(f"{c.id}: a criterion is tied to {r}, which the card does not serve")
            for r in c.rows:
                if not any(criterion_row(x) == r for x in c.criteria) and c.kind == "code":
                    problems.append(f"{c.id} serves {r} with no criterion for it")
    live = [c for c in existing if c.state != CardState.CANCELLED]
    try:
        validate(live + list(cards), max_cards)
    except DagError as exc:
        problems += exc.problems
    return problems


def covered_rows(cards: Iterable[Card], include_failed: bool = False) -> Set[str]:
    """Rows some card serves with a criterion: a card that is not cancelled
    (nor failed, unless *include_failed*)."""
    out: Set[str] = set()
    for c in cards:
        if c.state == CardState.CANCELLED or (c.state == CardState.FAILED and not include_failed):
            continue
        out |= {criterion_row(x) for x in c.criteria} & set(c.rows)
    return out


def check_plan(plan: Dict[str, Any], cards: Sequence[Card], scope: Sequence[str], policy: Policy,
               greenfield: bool = False, existing: Sequence[Card] = ()) -> List[str]:  # fmt: skip
    """The mechanical checks of plan §6, step 5, on the planner's answer."""
    problems = check_cards(cards, scope, existing, policy.budget.max_cards)
    if greenfield and not plan.get("init"):
        problems.append("the repository is empty: the plan must choose `init` (template, toolchain, name)")
    if not cards or all(c.kind == "init" for c in cards):
        problems.append("the plan has no card")
    if not scope:
        return problems
    if policy.horizon == "all":
        missing = [r for r in scope if r not in covered_rows(list(existing) + list(cards))]
        if missing:
            problems.append(f"rows no card's criterion covers: {', '.join(missing)}")
    else:
        epics = plan.get("epics") or []
        if not epics:
            problems.append("a ready_depth plan needs its epics")
        in_epics = {str(r) for e in epics for r in e.get("rows") or []}
        missing = [r for r in scope if r not in in_epics]
        if missing:
            problems.append(f"rows in no epic: {', '.join(missing)}")
        ids = {str(e.get("id")) for e in epics}
        for e in epics:
            for d in e.get("deps") or []:
                if str(d) not in ids:
                    problems.append(f"epic {e.get('id')} depends on unknown epic {d!r}")
        for c in cards:
            if c.epic and ids and c.epic not in ids:
                problems.append(f"{c.id} is in unknown epic {c.epic!r}")
    return problems


def _upstream(cards: Sequence[Card], cid: str) -> Set[str]:
    by = {c.id: c for c in cards}
    seen: Set[str] = set()
    todo = list(by[cid].deps) if cid in by else []
    while todo:
        d = todo.pop()
        if d in seen or d not in by:
            continue
        seen.add(d)
        todo += by[d].deps
    return seen


def overlaps(cards: Sequence[Card]) -> List[str]:
    """Pairs of cards whose paths could meet with no dependency either way:
    they would never run together, but the order between them is luck."""
    out: List[str] = []
    work = [c for c in cards if c.kind != "init"]
    for i, a in enumerate(work):
        for b in work[i + 1 :]:
            if (
                paths_overlap(a.paths, b.paths)
                and b.id not in _upstream(cards, a.id)
                and a.id not in _upstream(cards, b.id)
            ):
                out.append(f"{a.id} and {b.id} ({', '.join(a.paths)} / {', '.join(b.paths)})")
    return out
