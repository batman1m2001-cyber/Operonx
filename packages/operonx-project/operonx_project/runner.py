"""The scheduler's card runner and integrator, on ``task_flow`` and git.

:class:`FlowRunner` runs each card as a journalled ``task_flow`` run. A card
parked on a question holds nothing: its run is in the journal, and
:meth:`FlowRunner.resume` continues it with the answer, from any engine
instance and any process. :meth:`FlowRunner.recover` continues a run whose
process died (``kill -9``): the rounds that had ended are replayed, the
round that was going runs again.

:class:`GitIntegrator` merges a verified card into the integration branch
under the merge lock, runs the full checks there, and undoes the merge if
they regress against the integration baseline (plan §8.2). A conflict or a
regression re-opens the card; its next run starts with a rebase round.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Dict, Optional, Union

from operonx import Operon
from operonx.core.loggings import LOGGER
from operonx.durable import SqliteJournal

from operonx_project.checks import CheckResult, compare, run_checks
from operonx_project.flows.task_flow import task_flow
from operonx_project.model import Card, Stage
from operonx_project.runtime import Runtime, register
from operonx_project.scheduler import CardOutcome, Integrated

__all__ = ["FlowRunner", "GitIntegrator"]


def _outcome(out: Dict[str, Any], run_id: str) -> CardOutcome:
    parked = out.get("$interrupted") or []
    if parked:
        q = parked[0]
        payload = dict(q.get("payload") or {})
        question = {"interrupt_id": q["interrupt_id"], **payload}
        return CardOutcome("waiting_human", payload.get("reason", "asked the owner"), run_id=run_id, question=question)
    errors = out.get("$errors")
    if errors:
        return CardOutcome("failed", f"task_flow failed: {str(errors)[:800]}", run_id=run_id)
    o = (out.get("$cells") or {}).get("outcome") or {}
    status = o.get("status")
    if status not in ("verified", "failed", "requeue"):
        return CardOutcome("failed", f"task_flow ended without an outcome: {o!r}", run_id=run_id)
    return CardOutcome(status, o.get("reason", ""), run_id=run_id, not_before=o.get("not_before"))


class FlowRunner:
    """Costs go on the card and the milestone as each coder call ends, not on
    the outcome: a crash then loses no spend, and the scheduler's budget
    check sees it at once."""

    def __init__(self, rt: Runtime, journal: Union[str, Path], durability: str = "sync"):
        self.rt = register(rt)
        self.journal_path = Path(journal)
        self.journal_path.parent.mkdir(parents=True, exist_ok=True)
        self.engine = Operon(
            task_flow,
            params={"key": None, "mid": None, "cid": None, "reopen": None},
            journal=SqliteJournal(str(self.journal_path)),
            durability=durability,
        )

    def _inputs(self, card: Card, reopen: Dict[str, Any]) -> Dict[str, Any]:
        return {"key": self.rt.key, "mid": card.milestone, "cid": card.id, "reopen": reopen}

    async def run(self, card: Card, stage: Callable[[Stage], None]) -> CardOutcome:
        """A new run of the card: its first, or after a rate limit or a re-open."""
        mid, cid = card.milestone, card.id
        runs = int(card.detail.get("runs", 0)) + 1
        run_id = f"{mid}.{cid}.{runs}"
        reopen = dict(card.detail.get("reopen") or {})
        self.rt.set_detail(mid, cid, runs=runs, reopen=None)
        self.rt.store.update_card(mid, cid, run_id=run_id)
        self.rt.stages[(mid, cid)] = stage
        try:
            out = await self.engine.run(self._inputs(card, reopen), run_id=run_id)
        finally:
            self.rt.stages.pop((mid, cid), None)
        return _outcome(out, run_id)

    async def resume(self, card: Card, answer: Any, stage: Callable[[Stage], None]) -> CardOutcome:
        """The owner's answer to the question the card's run is parked on."""
        q = card.detail.get("question") or {}
        iid = q.get("interrupt_id")
        if not card.run_id or not iid:
            return CardOutcome("failed", "no parked run to resume", run_id=card.run_id)
        self.rt.stages[(card.milestone, card.id)] = stage
        try:
            handle = await self.engine.resume(card.run_id, answers={iid: answer})
            out = await handle.result()
        finally:
            self.rt.stages.pop((card.milestone, card.id), None)
        return _outcome(out, card.run_id)

    async def recover(self, card: Card, stage: Callable[[Stage], None]) -> CardOutcome:
        """Continue a run whose process died mid-round."""
        if not card.run_id:
            return await self.run(card, stage)
        self.rt.stages[(card.milestone, card.id)] = stage
        try:
            handle = await self.engine.resume(card.run_id)
            out = await handle.result()
        finally:
            self.rt.stages.pop((card.milestone, card.id), None)
        return _outcome(out, card.run_id)


class GitIntegrator:
    """Merge, check, keep or undo: one card at a time under the merge lock."""

    def __init__(self, rt: Runtime):
        self.rt = rt

    def _baseline_key(self, mid: str) -> str:
        return f"integration-baseline:{self.rt.key}:{mid}"

    async def integrate(self, card: Card) -> Integrated:
        rt, mid = self.rt, card.milestone
        with rt.git.merge_lock():
            integ = rt.git.ensure_integration(mid)
            before = rt.git.head(integ)
            saved = rt.store.checkpoint(self._baseline_key(mid))
            if saved is None or saved.get("sha") != before:
                rows = [r.to_dict() for r in await run_checks(rt.checks, integ, rt.env)]
                saved = {"sha": before, "results": rows}
                rt.store.save_checkpoint(self._baseline_key(mid), saved)
            m = rt.git.merge_card(mid, card.id, message=f"{mid}: merge card {card.id} ({card.title})", lock=False)
            if m.status == "conflict":
                return Integrated("conflict", f"conflict with the integration branch in {', '.join(m.files)}")
            if m.status == "empty":
                return Integrated("merged", "nothing to merge")
            results = await run_checks(rt.checks, integ, rt.env)
            cmp = compare([CheckResult(**r) for r in saved["results"]], results)
            if not cmp.ok:
                rt.git.reset_integration(mid, before)
                tails = "\n".join(f"$ {r.name}\n{r.tail[-1500:]}" for r in results if not r.ok)
                rt.store.note(mid, "integration.regression", {"new": cmp.new, "merge": m.sha}, card=card.id)
                LOGGER.warning(f"card {card.id}: merge regressed {cmp.new}; undone")
                return Integrated("regression", f"after the merge these checks fail: {', '.join(cmp.new)}\n{tails}")
            rt.store.save_checkpoint(self._baseline_key(mid), {"sha": m.sha, "results": [r.to_dict() for r in results]})
            rt.store.note(mid, "integration.merged", {"sha": m.sha}, card=card.id)
            return Integrated("merged", f"merged at {m.sha[:10] if m.sha else '?'}")


def runner_for(rt: Runtime, journal: Optional[Union[str, Path]] = None) -> FlowRunner:
    """The runner with its journal beside the project's store."""
    return FlowRunner(rt, journal or Path(rt.data_dir) / "runs.db")
