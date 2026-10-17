"""Run a milestone's cards: start what may start, integrate what passed, react.

The scheduler is event-driven. Each card run, each integration, each command
and each timer posts one event, and the loop sleeps on that queue between
them. Nothing polls: the only time-based wake is a rate-limit hold, set as a
timer for the moment it ends.

It moves cards, never the milestone. :meth:`Scheduler.run` returns *why it
stopped* (:class:`Outcome`), and the milestone's lifecycle (§6 of the plan)
decides what that means.
"""

from __future__ import annotations

import asyncio
import os
import signal
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List, Optional, Protocol

from operonx.core.loggings import LOGGER

from operonx_project.board import needs_replenish, pick, refresh
from operonx_project.model import Card, CardState, Stage
from operonx_project.store import Store

__all__ = [
    "CardOutcome",
    "CardRunner",
    "Integrated",
    "Integrator",
    "Outcome",
    "Replenisher",
    "Scheduler",
]


@dataclass
class CardOutcome:
    """What one run of a card ended with.

    ``status``: ``verified`` (passed its round's checks and review),
    ``failed``, ``waiting_human`` (its run parked on a question),
    ``requeue`` (no verdict: a rate limit, a drain; ``not_before`` holds new
    starts until then).
    """

    status: str
    reason: str = ""
    cost_usd: float = 0.0
    run_id: Optional[str] = None
    question: Optional[Dict[str, Any]] = None
    not_before: Optional[float] = None  # epoch seconds

    def __post_init__(self) -> None:
        if self.status not in ("verified", "failed", "waiting_human", "requeue"):
            raise ValueError(f"unknown card outcome {self.status!r}")


@dataclass
class Integrated:
    """``merged``, or ``conflict`` / ``regression`` (the card re-opens), or ``failed``."""

    status: str
    reason: str = ""

    def __post_init__(self) -> None:
        if self.status not in ("merged", "conflict", "regression", "failed"):
            raise ValueError(f"unknown integration result {self.status!r}")


class CardRunner(Protocol):
    async def run(self, card: Card, stage: Callable[[Stage], None]) -> CardOutcome: ...

    async def resume(self, card: Card, answer: Any, stage: Callable[[Stage], None]) -> CardOutcome: ...

    # optional: ``recover(card, stage)`` continues a run whose process died
    # (a card found ``running`` when the scheduler starts); without it, the
    # card's run starts again with ``run``.


class Integrator(Protocol):
    async def integrate(self, card: Card) -> Integrated: ...


#: Details up to *n* more cards for the milestone and returns them (pending,
#: not yet in the store), or [] when no rows are left. ``(cards, n) -> cards``.
Replenisher = Callable[[List[Card], int], Awaitable[List[Card]]]


class Outcome(str, Enum):
    COMPLETE = "complete"  # every card done
    WAITING = "waiting"  # nothing can run; at least one card waits for the owner
    STUCK = "stuck"  # nothing can run; something failed or is blocked
    PAUSED = "paused"
    CANCELLED = "cancelled"
    BUDGET = "budget"  # the milestone's budget (dollars or hours) ran out with work left
    RATE_LIMITED = "rate_limited"  # the coder's limit resets later than a hold is worth: see resume_at


@dataclass
class _Event:
    kind: str
    card: Optional[str] = None
    data: Any = None


@dataclass
class Stats:
    steps: int = 0
    starts: int = 0
    resumes: int = 0
    integrations: int = 0
    replenished: int = 0
    max_running: int = 0
    log: List[str] = field(default_factory=list)


def _kill_group(pgid: int) -> None:
    """Kill a coder's process group left by a dead engine: only when its
    leader is still a ``claude`` process (a pgid is reused after a reboot)."""
    try:
        cmdline = Path(f"/proc/{pgid}/cmdline").read_bytes().replace(b"\0", b" ").decode(errors="replace")
    except OSError:
        return
    if "claude" not in cmdline:
        return
    try:
        os.killpg(pgid, signal.SIGKILL)
        LOGGER.warning(f"killed the orphaned coder process group {pgid}")
    except (ProcessLookupError, PermissionError):
        pass


class Scheduler:
    def __init__(
        self,
        store: Store,
        milestone: str,
        runner: CardRunner,
        integrator: Integrator,
        replenisher: Optional[Replenisher] = None,
        rows_left: Callable[[], int] = lambda: 0,
        deadline: Optional[float] = None,  # epoch seconds: max_hours runs out; no new start after it
        hold_max_s: float = 120.0,  # a rate limit resetting later than this pauses the milestone instead
    ):
        self.store = store
        self.mid = milestone
        self.runner = runner
        self.integrator = integrator
        self.replenisher = replenisher
        self.rows_left = rows_left
        self.deadline = deadline
        self.hold_max_s = hold_max_s
        self.resume_at: Optional[float] = None  # set with Outcome.RATE_LIMITED
        self.policy = store.get_milestone(milestone).policy
        self.stats = Stats()
        self._events: Optional[asyncio.Queue] = None
        self._tasks: Dict[str, asyncio.Task] = {}
        self._integrating: Optional[str] = None
        self._paused = False
        self._cancelled = False
        self._hold_until = 0.0
        self._timer: Optional[asyncio.TimerHandle] = None
        self._pending_cmds: List[_Event] = []

    # ── commands (any coroutine of the same loop may call them) ────────────
    def answer(self, card_id: str, answer: Any) -> None:
        """Resume a card parked on a question with the owner's (validated) answer."""
        self._post(_Event("answer", card_id, answer))

    def pause(self) -> None:
        self._post(_Event("pause"))

    def unpause(self) -> None:
        self._post(_Event("unpause"))

    def cancel(self) -> None:
        self._post(_Event("cancel"))

    def _post(self, ev: _Event) -> None:
        if self._events is None:
            self._pending_cmds.append(ev)
        else:
            self._events.put_nowait(ev)

    # ── the loop ────────────────────────────────────────────────────────────
    async def run(self) -> Outcome:
        self._events = asyncio.Queue()
        for ev in self._pending_cmds:
            self._events.put_nowait(ev)
        self._pending_cmds.clear()
        self._recover()
        while True:
            self.stats.steps += 1
            self._apply_refresh()
            if self._can_start():
                await self._replenish()
                self._apply_refresh()
                self._start_ready()
            self._start_integration()
            if not self._tasks and self._events.empty() and self._timer is None:
                return self._outcome()
            ev = await self._events.get()
            await self._handle(ev)

    def _cards(self) -> List[Card]:
        return self.store.cards(self.mid)

    def _recover(self) -> None:
        """Cards left ``running`` by a process that died: their orphaned coder
        process group is killed, and their run goes on from the journal."""
        for card in self._cards():
            if card.state != CardState.RUNNING or card.id in self._tasks:
                continue
            pgid = card.detail.get("pgid")
            if pgid:
                _kill_group(int(pgid))
            again = getattr(self.runner, "recover", None) or self.runner.run
            self.store.note(self.mid, "card.recover", {"run_id": card.run_id, "pgid": pgid}, card=card.id)
            self._spawn(card.id, again(card, self._stage(card.id)))
            self.stats.resumes += 1

    def _apply_refresh(self) -> None:
        # to a fixed point: blocking a card can block the cards behind it
        for _ in range(len(self._cards()) + 1):
            moves = refresh(self._cards())
            if not moves:
                return
            for cid, (to, reason) in moves.items():
                self.store.move_card(self.mid, cid, to, reason)

    def over_time(self) -> bool:
        return self.deadline is not None and time.time() >= self.deadline

    def _over_budget(self) -> bool:
        return self.store.get_milestone(self.mid).cost_usd >= self.policy.budget.max_usd or self.over_time()

    def _rate_limited(self) -> bool:
        return self.resume_at is not None and time.time() < self.resume_at

    def _can_start(self) -> bool:
        return not (self._paused or self._cancelled or self._over_budget() or self._rate_limited()
                    or time.time() < self._hold_until)  # fmt: skip

    async def _replenish(self) -> None:
        if self.replenisher is None:
            return
        cards = self._cards()
        n = needs_replenish(cards, self.policy, self.rows_left())
        n = min(n, max(0, self.policy.budget.max_cards - len(cards)))
        if n <= 0:
            return
        new = await self.replenisher(cards, n)
        if new:
            self.store.add_cards(new[:n], actor="replenisher")
            self.stats.replenished += len(new[:n])

    def _start_ready(self) -> None:
        for card in pick(self._cards(), self.policy):
            self.store.move_card(self.mid, card.id, CardState.RUNNING)
            self._spawn(card.id, self.runner.run(self.store.get_card(self.mid, card.id), self._stage(card.id)))
            self.stats.starts += 1
        self._note_running()

    def _note_running(self) -> None:
        running = sum(1 for c in self._cards() if c.state == CardState.RUNNING)
        self.stats.max_running = max(self.stats.max_running, running)

    def _stage(self, cid: str) -> Callable[[Stage], None]:
        def set_stage(stage: Stage) -> None:
            self.store.set_stage(self.mid, cid, stage)

        return set_stage

    def _spawn(self, cid: str, coro: Awaitable[CardOutcome]) -> None:
        async def go() -> None:
            try:
                out = await coro
            except asyncio.CancelledError:
                self._events.put_nowait(_Event("card_cancelled", cid))
                raise
            except Exception as exc:  # noqa: BLE001 — a runner bug fails the card, not the milestone
                LOGGER.exception(f"card {cid}: the runner raised")
                out = CardOutcome("failed", f"the runner raised {type(exc).__name__}: {exc}")
            self._events.put_nowait(_Event("card_done", cid, out))

        self._tasks[cid] = asyncio.ensure_future(go())

    def _start_integration(self) -> None:
        if self._integrating is not None or self._cancelled:
            return
        verified = [c for c in self._cards() if c.state == CardState.VERIFIED]
        if not verified:
            return
        card = sorted(verified, key=lambda c: (c.priority, c.updated, c.id))[0]
        self._integrating = card.id
        key = f"integrate:{card.id}"

        async def go() -> None:
            try:
                res = await self.integrator.integrate(card)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                LOGGER.exception(f"card {card.id}: integration raised")
                res = Integrated("failed", f"integration raised {type(exc).__name__}: {exc}")
            self._events.put_nowait(_Event("integrated", card.id, res))

        self._tasks[key] = asyncio.ensure_future(go())
        self.stats.integrations += 1

    async def _handle(self, ev: _Event) -> None:
        if ev.kind == "card_done":
            self._tasks.pop(ev.card, None)
            self._card_done(ev.card, ev.data)
        elif ev.kind == "card_cancelled":
            self._tasks.pop(ev.card, None)
            card = self.store.get_card(self.mid, ev.card)
            if self._cancelled and card.state == CardState.RUNNING:
                self.store.move_card(self.mid, ev.card, CardState.CANCELLED, "the milestone was cancelled")
        elif ev.kind == "integrated":
            self._tasks.pop(f"integrate:{ev.card}", None)
            self._integrating = None
            self._integrated(ev.card, ev.data)
        elif ev.kind == "answer":
            card = self.store.get_card(self.mid, ev.card)
            if card.state != CardState.WAITING_HUMAN or self._cancelled:
                self.stats.log.append(f"answer for {ev.card} ignored: it is {card.state.value}")
                return
            self.store.move_card(self.mid, ev.card, CardState.RUNNING, "answered", actor="owner")
            self._spawn(
                ev.card, self.runner.resume(self.store.get_card(self.mid, ev.card), ev.data, self._stage(ev.card))
            )
            self.stats.resumes += 1
            self._note_running()
        elif ev.kind == "pause":
            self._paused = True
        elif ev.kind == "unpause":
            self._paused = False
        elif ev.kind == "cancel":
            self._cancel_all()
        elif ev.kind == "wake":
            self._timer = None  # a hold ended; the loop re-checks what may start

    def _card_done(self, cid: str, out: CardOutcome) -> None:
        if out.cost_usd:
            self.store.add_cost(self.mid, out.cost_usd, card=cid)
        card = self.store.get_card(self.mid, cid)
        if card.state != CardState.RUNNING:  # cancelled meanwhile
            return
        extra = {"run_id": out.run_id} if out.run_id else {}
        if out.status == "verified":
            self.store.move_card(self.mid, cid, CardState.VERIFIED, out.reason, **extra)
        elif out.status == "failed":
            self.store.move_card(self.mid, cid, CardState.FAILED, out.reason, **extra)
        elif out.status == "waiting_human":
            detail = dict(card.detail, question=out.question)
            self.store.move_card(
                self.mid, cid, CardState.WAITING_HUMAN, out.reason or "asked the owner", detail=detail, **extra
            )
        else:  # requeue
            self.store.move_card(self.mid, cid, CardState.READY, out.reason or "requeued", **extra)
            if out.not_before and out.not_before - time.time() > self.hold_max_s:
                # a long wait: running cards end their round, then the milestone pauses until the reset
                self.resume_at = max(self.resume_at or 0.0, out.not_before)
            elif out.not_before and out.not_before > self._hold_until:
                self._hold_until = out.not_before
                delay = max(0.0, out.not_before - time.time())
                if self._timer is not None:
                    self._timer.cancel()
                self._timer = asyncio.get_running_loop().call_later(delay, self._events.put_nowait, _Event("wake"))

    def _integrated(self, cid: str, res: Integrated) -> None:
        card = self.store.get_card(self.mid, cid)
        if card.state != CardState.VERIFIED:
            return
        if res.status == "merged":
            self.store.move_card(self.mid, cid, CardState.DONE, res.reason)
        elif res.status == "failed":
            self.store.move_card(self.mid, cid, CardState.FAILED, res.reason)
        elif card.reopens >= self.policy.budget.max_reopens:
            self.store.move_card(
                self.mid, cid, CardState.FAILED, f"{res.status} after {card.reopens} re-opens: {res.reason}"
            )
        else:
            detail = dict(card.detail, reopen={"why": res.status, "reason": res.reason})
            self.store.move_card(
                self.mid, cid, CardState.READY, f"re-opened: {res.status}", reopens=card.reopens + 1, detail=detail
            )

    def _cancel_all(self) -> None:
        self._cancelled = True
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None
        for task in list(self._tasks.values()):
            task.cancel()
        for card in self._cards():
            if card.state in (
                CardState.PENDING,
                CardState.READY,
                CardState.WAITING_HUMAN,
                CardState.VERIFIED,
                CardState.BLOCKED,
            ):
                self.store.move_card(
                    self.mid, card.id, CardState.CANCELLED, "the milestone was cancelled", actor="owner"
                )

    def _outcome(self) -> Outcome:
        cards = self._cards()
        if self._cancelled:
            return Outcome.CANCELLED
        live = [c for c in cards if c.state != CardState.CANCELLED]  # a replan's retired cards
        if live and all(c.state == CardState.DONE for c in live) and not self.rows_left():
            return Outcome.COMPLETE
        if self._paused:
            return Outcome.PAUSED
        if self._rate_limited():
            return Outcome.RATE_LIMITED
        open_ = [c for c in cards if c.state in (CardState.READY, CardState.PENDING)]
        if open_ and self._over_budget():
            return Outcome.BUDGET
        if any(c.state == CardState.WAITING_HUMAN for c in cards):
            return Outcome.WAITING
        if not cards and not self.rows_left():
            return Outcome.COMPLETE
        return Outcome.STUCK
