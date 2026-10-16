"""The board: which column a card is in, what may start now, when to replenish.

Push (W1) and pull (W2) share every rule but two: pull honours the Review and
QA columns' WIP limits before starting new work (finish before you start),
and pull asks the replenisher for more detailed cards when Ready runs low.
"""

from __future__ import annotations

from typing import Dict, List, Sequence

from operonx_project.dag import paths_overlap
from operonx_project.model import Card, CardState, Policy, Stage

__all__ = ["COLUMNS", "column_of", "needs_replenish", "pick", "refresh"]

#: Board columns, left to right.
COLUMNS = ("backlog", "ready", "in_progress", "review", "qa", "waiting", "done", "blocked")


def column_of(card: Card) -> str:
    s = card.state
    if s == CardState.PENDING:
        return "backlog"
    if s == CardState.READY:
        return "ready"
    if s == CardState.RUNNING:
        return {Stage.REVIEW: "review", Stage.QA: "qa"}.get(card.stage, "in_progress")
    if s == CardState.VERIFIED:
        return "qa"  # verified, waiting for the merge lock
    if s == CardState.WAITING_HUMAN:
        return "waiting"
    if s == CardState.DONE:
        return "done"
    return "blocked"  # blocked, failed, cancelled


def columns(cards: Sequence[Card]) -> Dict[str, List[Card]]:
    out: Dict[str, List[Card]] = {c: [] for c in COLUMNS}
    for card in cards:
        out[column_of(card)].append(card)
    return out


def refresh(cards: Sequence[Card]) -> Dict[str, tuple]:
    """The moves the dependency rules call for now: ``{card id: (state, reason)}``.

    - a pending card whose every dependency is done becomes ready;
    - a pending or ready card with a failed, cancelled or blocked dependency
      becomes blocked, naming it;
    - a blocked card whose dependencies are all back in play returns to pending.
    """
    by_id = {c.id: c for c in cards}
    dead = (CardState.FAILED, CardState.CANCELLED, CardState.BLOCKED)
    moves: Dict[str, tuple] = {}
    for c in cards:
        deps = [by_id[d] for d in c.deps if d in by_id]
        bad = [d for d in deps if d.state in dead]
        if c.state in (CardState.PENDING, CardState.READY) and bad:
            first = bad[0]
            moves[c.id] = (CardState.BLOCKED, f"{first.id} is {first.state.value}")
        elif c.state == CardState.PENDING and all(d.state == CardState.DONE for d in deps):
            moves[c.id] = (CardState.READY, "")
        elif c.state == CardState.BLOCKED and not bad:
            moves[c.id] = (CardState.PENDING, "dependencies back in play")
    return moves


def _holds_paths(card: Card) -> bool:
    # a verified card holds its paths until it merges: a card started from the
    # integration tip would not have its changes
    return card.state in (CardState.RUNNING, CardState.VERIFIED, CardState.WAITING_HUMAN)


def pick(cards: Sequence[Card], policy: Policy) -> List[Card]:
    """The ready cards to start now, in start order.

    A card starts when a coder slot is free (``max_parallel`` counts running
    cards), no card holding paths overlaps its own, and, under pull, the
    Review and QA columns are below their WIP limits.
    """
    cols = columns(cards)
    running = [c for c in cards if c.state == CardState.RUNNING]
    free = policy.max_parallel - len(running)
    if free <= 0:
        return []
    if policy.start == "pull":
        for col in ("review", "qa"):
            limit = policy.wip.get(col)
            if limit is not None and len(cols[col]) >= limit:
                return []
    holding = [c for c in cards if _holds_paths(c)]
    out: List[Card] = []
    for card in sorted(cols["ready"], key=lambda c: (c.priority, c.created, c.id)):
        if len(out) >= free:
            break
        if any(paths_overlap(card.paths, h.paths) for h in holding + out):
            continue
        out.append(card)
    return out


def needs_replenish(cards: Sequence[Card], policy: Policy, rows_left: int) -> int:
    """How many more cards the replenisher should detail now (0 = none).

    Only with ``horizon="ready_depth"``: when Ready and the detailed Backlog
    together hold fewer than ``depth`` cards and rows remain without a card.
    """
    if policy.horizon != "ready_depth" or rows_left <= 0:
        return 0
    queued = sum(1 for c in cards if c.state in (CardState.READY, CardState.PENDING))
    return max(0, policy.depth - queued)
