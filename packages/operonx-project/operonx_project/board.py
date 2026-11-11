"""The board: which column a card is in."""

from __future__ import annotations

from typing import Dict, List, Sequence

from operonx_project.model import Card, CardState, Stage

__all__ = ["COLUMNS", "column_of", "columns"]

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
