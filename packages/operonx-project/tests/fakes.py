"""Test doubles for the scheduler: a scripted card runner and integrator.

They stand in for task_flow and gitops (P2/P3). Each run checks the
scheduler's invariants at the moment a card starts.
"""

from __future__ import annotations

import asyncio
from typing import Any, Callable, Dict, List, Optional

from operonx_project.dag import paths_overlap
from operonx_project.model import Card, CardState, Stage
from operonx_project.scheduler import CardOutcome, Integrated
from operonx_project.store import Store


class Violation(AssertionError):
    pass


class ScriptedRunner:
    """Each card plays its script: a list of outcomes, one per run (the last repeats).

    An outcome is a status string, a CardOutcome, or a callable(card, answer) → CardOutcome.
    """

    def __init__(
        self,
        store: Store,
        mid: str,
        scripts: Dict[str, List[Any]],
        durations: Optional[Dict[str, float]] = None,
        max_parallel: int = 2,
    ):
        self.store, self.mid = store, mid
        self.scripts = scripts
        self.durations = durations or {}
        self.max_parallel = max_parallel
        self.runs: Dict[str, int] = {}
        self.answers: Dict[str, List[Any]] = {}
        self.started: List[str] = []

    def _check(self, card: Card) -> None:
        cards = {c.id: c for c in self.store.cards(self.mid)}
        for d in card.deps:
            if cards[d].state != CardState.DONE:
                raise Violation(f"{card.id} started while its dependency {d} is {cards[d].state.value}")
        running = [c for c in cards.values() if c.state == CardState.RUNNING]
        if len(running) > self.max_parallel:
            raise Violation(f"{len(running)} cards running, max_parallel={self.max_parallel}")
        for other in cards.values():
            if other.id != card.id and other.state in (CardState.RUNNING, CardState.VERIFIED, CardState.WAITING_HUMAN):
                if paths_overlap(card.paths, other.paths):
                    raise Violation(f"{card.id} started while {other.id} holds overlapping paths")

    async def _play(self, card: Card, stage: Callable[[Stage], None], answer: Any = None) -> CardOutcome:
        self._check(card)
        self.started.append(card.id)
        n = self.runs.get(card.id, 0)
        self.runs[card.id] = n + 1
        script = self.scripts.get(card.id, ["verified"])
        step = script[min(n, len(script) - 1)]
        dur = self.durations.get(card.id, 0.0)
        stage(Stage.IMPLEMENT)
        await asyncio.sleep(dur / 3)
        stage(Stage.REVIEW)
        await asyncio.sleep(dur / 3)
        stage(Stage.QA)
        await asyncio.sleep(dur / 3)
        if callable(step):
            return step(card, answer)
        if isinstance(step, CardOutcome):
            return step
        return CardOutcome(step, cost_usd=0.5)

    async def run(self, card, stage):
        return await self._play(card, stage)

    async def resume(self, card, answer, stage):
        self.answers.setdefault(card.id, []).append(answer)
        return await self._play(card, stage, answer)


class ScriptedIntegrator:
    def __init__(self, scripts: Optional[Dict[str, List[str]]] = None):
        self.scripts = scripts or {}
        self.calls: Dict[str, int] = {}
        self.order: List[str] = []

    async def integrate(self, card: Card) -> Integrated:
        n = self.calls.get(card.id, 0)
        self.calls[card.id] = n + 1
        self.order.append(card.id)
        script = self.scripts.get(card.id, ["merged"])
        await asyncio.sleep(0)
        return Integrated(script[min(n, len(script) - 1)], "scripted")
