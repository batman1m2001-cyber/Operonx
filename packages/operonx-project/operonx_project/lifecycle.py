"""A milestone's lifecycle (plan §6): intake → discover → requirements → plan →
approval → execute → final verification → delivery.

:meth:`Lifecycle.drive` advances a milestone until it needs the owner
(approval, a question, a pause) or ends. Each stage is journalled
(``discover_flow``, ``brd_flow``, ``plan_flow``, ``final_flow``, and each
card's ``task_flow``), so a drive that dies at any point goes on from the
journal when it is driven again. Owner commands (:meth:`Lifecycle.apply`)
come from the store's command queue, through the daemon; a command for a
milestone whose cards are running goes to its scheduler.

Every loop is bounded (§8.4): plan revisions (``plan_reviews``), replans
(``max_replans``), card re-opens (``max_reopens``), budgets. When a bound
is reached the milestone stops for the owner instead of trying again.
"""

from __future__ import annotations

import json
import re
import shutil
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from operonx import Operon
from operonx.core.loggings import LOGGER
from operonx.durable import SqliteJournal
from operonx.telemetry.consumers.local import LocalConsumer

from operonx_project.brd.board import write_board
from operonx_project.brd.records import load_requirements, matrix_status
from operonx_project.checks import Check
from operonx_project.coder.base import Coder
from operonx_project.flows.discover_flow import discover_flow
from operonx_project.flows.final_flow import final_flow
from operonx_project.flows.plan_flow import plan_flow
from operonx_project.flows.stage import BudgetSpent, stage_call, stage_request
from operonx_project.model import Card, CardState, MilestoneState
from operonx_project.plan import INIT_CARD, cards_from_plan, check_cards, covered_rows
from operonx_project.project import Project
from operonx_project.prompts import REPLENISH_SCHEMA, replenish_prompt
from operonx_project.runner import FlowRunner, GitIntegrator, InitRunner, Runners
from operonx_project.runtime import Runtime, register
from operonx_project.scheduler import Outcome, Scheduler
from operonx_project.store import MILESTONE_MOVES

#: final-verification gaps a fix card can close; others (the review did not finish) fail the milestone
FIXABLE = ("checks", "review", "brd")

__all__ = ["ACTIVE", "Lifecycle", "Refused", "scope_rows"]

M = MilestoneState
#: The states a daemon drives; the others wait for the owner, or are over.
ACTIVE = (M.DISCOVERING, M.REQUIREMENTS, M.PLANNING, M.RUNNING, M.VERIFYING)
_NOT_IN_SCOPE = ("implemented_verified", "out_of_scope_demo", "not_applicable")


class Refused(ValueError):
    """A command that does not apply to the milestone as it is."""


def scope_rows(brd: Path, only: Sequence[str] = ()) -> Dict[str, List[Dict[str, Any]]]:
    """The rows a milestone works on: every requirement not verified, not out
    of scope and not superseded (or only *only*). A row waiting on a
    question (``needs_clarification``) is held back with its ``Q-nn``."""
    status = matrix_status(brd)
    rows: List[Dict[str, Any]] = []
    held: List[Dict[str, Any]] = []
    for r in load_requirements(brd):
        if r.status == "superseded" or status.get(r.id) in _NOT_IN_SCOPE:
            continue
        if only and r.id not in only:
            continue
        item = {"id": r.id, "type": r.type, "reading": r.reading, "quote": r.quote[:300]}
        (held if r.status == "needs_clarification" else rows).append({**item, "question": r.question})
    return {"rows": rows, "held": held}


class Lifecycle:
    def __init__(
        self,
        project: Project,
        coder: Coder,
        models: Optional[Dict[str, Optional[str]]] = None,
        sandbox: bool = True,
        heldout_root: Optional[Path] = None,
        check_mode: str = "host",
        check_image: Optional[str] = None,
        call_max_usd: float = 2.0,
        python: Optional[str] = None,
        runtime: Optional[Dict[str, Any]] = None,
        hold_max_s: float = 120.0,  # a rate limit resetting later than this pauses the milestone (§10)
        trace_runs: bool = True,
    ):
        self.p = project
        # every flow run (a card's attempt, discovery, planning, the final
        # pass) is a run page in the studio: the project's own run store
        self.trace = LocalConsumer(config={"root": str(project.runs)}) if trace_runs else None
        self.hold_max_s = hold_max_s
        self.coder = coder
        self.models = models
        self.opts: Dict[str, Any] = {"sandbox": sandbox, "check_mode": check_mode, "check_image": check_image,
                                     "call_max_usd": call_max_usd, **(runtime or {})}  # fmt: skip
        if heldout_root is not None:
            self.opts["heldout_root"] = heldout_root
        if python:
            self.opts["python"] = python
        self.schedulers: Dict[str, Scheduler] = {}
        self._queued: Dict[str, List[tuple]] = {}
        self._rts: Dict[str, Runtime] = {}
        self._runners: Dict[str, FlowRunner] = {}
        self._engines: Dict[str, Operon] = {}

    @property
    def store(self):
        return self.p.store

    # ── the runtime of one milestone ────────────────────────────────────────
    def rt(self, mid: str) -> Runtime:
        """The milestone's runtime, its checks and brief read from its
        records: the same in any process that drives it."""
        rt = self._rts.get(mid)
        if rt is None:
            kw = dict(self.opts)
            if self.models:
                from operonx_project.runtime import DEFAULT_MODELS

                kw["models"] = {**DEFAULT_MODELS, **self.models}
            rt = Runtime(store=self.store, git=self.p.git, coder=self.coder, data_dir=self.p.state,
                         key=f"{self.p.git.project}.{mid}", **kw)  # fmt: skip
            self._rts[mid] = register(rt)
        survey = self.store.ms_get(mid, "survey") or {}
        base = self.store.ms_get(mid, "baseline") or {}
        rt.brief = survey.get("brief", "")
        rt.checks = [Check(**c) for c in base.get("checks") or []]
        rt.private = dict((self.store.ms_get(mid, "private") or {}).get("hashes") or {})
        return rt

    def _flow_runner(self, mid: str) -> FlowRunner:
        if mid not in self._runners:
            self._runners[mid] = FlowRunner(self.rt(mid), self.p.state / "runs.db", trace=self.trace)
        return self._runners[mid]

    def _engine(self, name: str) -> Operon:
        if name not in self._engines:
            flow, params = {
                "discover": (discover_flow, ("key", "mid")),
                "plan": (plan_flow, ("key", "mid", "notes")),
                "final": (final_flow, ("key", "mid")),
            }[name]
            self._engines[name] = Operon(flow, params={k: None for k in params},
                                         journal=SqliteJournal(str(self.p.state / f"{name}.db")),
                                         durability="sync", trace=self.trace)  # fmt: skip
        return self._engines[name]

    async def _stage(self, mid: str, name: str, inputs: Dict[str, Any]) -> Dict[str, Any]:
        """Run a stage flow, or go on with the run a dead process left."""
        runs = self.store.ms_get(mid, "runs") or {}
        cur = runs.get(name) or {"n": 0, "done": True}
        eng = self._engine(name)
        journal = SqliteJournal(str(self.p.state / f"{name}.db"))
        status = None
        if not cur["done"]:
            try:
                status = journal.read(cur["run_id"])[0].status
            except Exception:  # noqa: BLE001 — never written: start it
                status = None
        if status in ("running", "interrupted", "ok"):
            LOGGER.info(f"{mid}: {name} goes on from the journal ({cur['run_id']}, {status})")
            out = await (await eng.resume(cur["run_id"])).result()
        else:
            n = cur["n"] + 1
            cur = {"n": n, "run_id": f"{mid}.{name}.{n}", "done": False}
            self.store.ms_put(mid, "runs", {**runs, name: cur})
            out = await eng.run(inputs, run_id=cur["run_id"])
        if out.get("$errors"):
            # the run ended there: a retry or a replan starts the stage's next run, never this one's error again
            runs = self.store.ms_get(mid, "runs") or {}
            self.store.ms_put(mid, "runs", {**runs, name: {**cur, "done": True}})
            spent = [e for e in out["$errors"].values() if isinstance(e, dict) and e.get("type") == "BudgetSpent"]
            if spent:
                lines = str(spent[0].get("message") or "").strip().splitlines()
                raise BudgetSpent(lines[-1].split("BudgetSpent: ", 1)[-1] if lines else "the budget is spent")
            raise RuntimeError(f"{name} failed: {str(out['$errors'])[:1500]}")
        runs = self.store.ms_get(mid, "runs") or {}
        self.store.ms_put(mid, "runs", {**runs, name: {**cur, "done": True}})
        return out

    def _move(self, mid: str, to: MilestoneState, reason: str = "", actor: str = "engine") -> None:
        self.store.move_milestone(mid, to, reason, actor=actor)
        self.board(mid, f"{mid} {to.value}")

    def board(self, mid: str, why: str) -> Optional[str]:
        """Rewrite ``docs/brd/board/`` on the milestone's branch and commit it
        (plan D4): a plan approved, a card ended, the milestone moved. A
        milestone with no cards yet has no board to write. Never raises: the
        board file is a copy, and the store goes on without it."""
        try:
            if not self.store.cards(mid):
                return None
            rt = self.rt(mid)
            rel = (self.store.ms_get(mid, "intake") or {}).get("brd") or "docs/brd"
            board = [(m, self.store.cards(m.id)) for m in self.store.milestones()]
            with rt.git.merge_lock():
                integ = rt.git.ensure_integration(mid)
                if not write_board(integ / rel, board):
                    return None
                rt.git.git("add", "--", f"{rel}/board", cwd=integ)
                return rt.git.commit(integ, f"{mid}: board ({why})")
        except Exception:  # noqa: BLE001
            LOGGER.exception(f"{mid}: the board file could not be written")
            return None

    # ── drive ──────────────────────────────────────────────────────────────
    async def drive(self, mid: str) -> MilestoneState:
        """Advance the milestone until it waits for the owner or ends."""
        while True:
            ms = self.store.get_milestone(mid)
            s = ms.state
            if s not in ACTIVE:
                return s
            try:
                if s == M.DISCOVERING:
                    await self._stage(mid, "discover", {"key": self.rt(mid).key, "mid": mid})
                    self._move(mid, M.REQUIREMENTS)
                elif s == M.REQUIREMENTS:
                    await self._requirements(mid)
                    self._move(mid, M.PLANNING)
                elif s == M.PLANNING:
                    await self._plan(mid)
                elif s == M.RUNNING:
                    await self._execute(mid)
                elif s == M.VERIFYING:
                    await self._verify(mid)
            except BudgetSpent as exc:  # a stage stopped for money: retry --usd resumes it
                self.store.ms_put(mid, "exhausted_in", {"state": s.value})
                self._move(mid, M.BUDGET_EXHAUSTED, f"{s.value}: {exc}")
                return M.BUDGET_EXHAUSTED
            except Exception as exc:  # noqa: BLE001 — a stage that fails stops the milestone, said why
                LOGGER.exception(f"{mid}: {s.value} failed")
                if self.store.get_milestone(mid).state == s:
                    self._move(mid, M.FAILED, f"{s.value} failed: {type(exc).__name__}: {str(exc)[:1500]}")
                return self.store.get_milestone(mid).state

    # ── requirements ───────────────────────────────────────────────────────
    async def _requirements(self, mid: str) -> None:
        from operonx_project.brd.folder import init_brd
        from operonx_project.brd.register import register_source
        from operonx_project.brd.run import extract_source
        from operonx_project.flows.brd_flow import BrdRuntime

        rt = self.rt(mid)
        ms = self.store.get_milestone(mid)
        intake = self.store.ms_get(mid, "intake") or {}
        survey = self.store.ms_get(mid, "survey") or {}
        integ = rt.git.ensure_integration(mid)
        rel = intake.get("brd") or "docs/brd"
        brd = integ / rel
        sources = intake.get("sources") or []
        extracted = dict((self.store.ms_get(mid, "extracted") or {}))
        private = dict((self.store.ms_get(mid, "private") or {}).get("hashes") or {})
        if sources and not brd.exists():
            init_brd(integ, rel, project=rt.git.project)
        brt = None
        from operonx_project.brd.records import load_requirements

        scoped: List[str] = []  # sources whose rows are this milestone's scope (an owner request, plan D14)
        for s in sources:
            path = Path(s["path"])
            if s.get("into"):  # the source's place in the BRD (sources/owner_requests/…): copied there first
                dest = brd / s["into"]
                dest.parent.mkdir(parents=True, exist_ok=True)
                if not dest.exists():
                    shutil.copyfile(path, dest)
                path = dest
            before = {r.id for r in load_requirements(brd)} if brd.exists() else set()
            src = register_source(brd, path, role=s.get("role", "authoritative"), title=s.get("title"),
                                  private=bool(s.get("private")), repo=integ)  # fmt: skip
            if s.get("scope"):
                scoped.append(src.id)
            if src.private:
                private[src.sha256[0]] = path.name
                self.store.ms_put(mid, "private", {"hashes": private})
                rt.private = dict(private)
            if extracted.get(src.id) == src.sha256[0]:
                continue
            if brt is None:
                brt = BrdRuntime(
                    coder=self.coder,
                    data_dir=self.p.state / "brd" / mid,
                    key=f"{rt.key}.brd",
                    settings_dir=self.p.state,
                )
                if self.models:
                    brt.models = {**brt.models, **{k: v for k, v in self.models.items() if k in brt.models}}
            brt.taken = sorted(before)
            report = await extract_source(brt, brd, path, src.id, intake.get("prefix") or "REQ", src.title,
                                          s.get("name") or None, journal=self.p.state / "brd.db",
                                          run_id=f"{mid}.brd.{src.id}")  # fmt: skip
            self.store.add_cost(mid, float(report.get("cost_usd") or 0.0))
            got = {"source": src.id, "requirements": report.get("requirements"),
                   "problems": len(report.get("problems") or [])}  # fmt: skip
            self.store.note(mid, "requirements.extracted", got)
            clash = sorted(before & {r.id for r in load_requirements(brd) if r.source == src.id})
            if clash:  # the extractor reused an id: two requirements on one row would be silently merged
                raise RuntimeError(f"{src.id}'s requirements reuse ids the BRD already has: {', '.join(clash)}")
            extracted[src.id] = src.sha256[0]
            self.store.ms_put(mid, "extracted", extracted)
        if brd.exists():
            rt.git.git("add", "-A", rel, cwd=integ)
            rt.git.commit(integ, f"{mid}: requirements ({', '.join(sorted(extracted)) or 'the BRD folder'})")
            only = list(ms.rows)
            if scoped:  # an owner request: its own rows, and the milestone shows them
                only += [r.id for r in load_requirements(brd) if r.source in scoped and r.id not in only]
                if only != list(ms.rows):
                    self.store.update_milestone(mid, rows=only, actor="engine")
            scope = scope_rows(brd, only)
        else:
            scope = {"rows": [], "held": []}
        for h in scope["held"]:
            self.store.note(mid, "requirements.held", {"row": h["id"], "question": h.get("question")})
        self.store.ms_put(mid, "context", {
            "brief": survey.get("brief", ""),
            "greenfield": bool(survey.get("greenfield")),
            "rows": scope["rows"],
            "held": scope["held"],
            "brd": rel if brd.exists() else None,
            "constraints": intake.get("constraints", ""),
        })  # fmt: skip

    # ── plan and approval ──────────────────────────────────────────────────
    async def _plan(self, mid: str) -> None:
        if self.store.ms_get(mid, "rebuilt") and any(c.state != CardState.CANCELLED for c in self.store.cards(mid)):
            self.store.ms_put(mid, "rebuilt", {})  # once: a later replan plans as usual
            self._move(mid, M.RUNNING, "the board's cards go on (rebuilt from docs/brd/board)")
            return
        seed = self.store.ms_get(mid, "seeded") or {}
        if seed.get("cards"):
            # start --from-board (D6): another milestone's cards are the plan; no planner runs
            self.store.ms_put(mid, "plan", {"plan": {"cards": seed["cards"]}, "passed": True, "problems": [],
                                            "notes": [], "seeded_from": seed.get("from")})  # fmt: skip
            self.store.ms_put(mid, "seeded", {})
            self.store.note(mid, "plan.seeded", {"from": seed.get("from"), "cards": len(seed["cards"])})
            self._ask_approval(mid)
            return
        rt = self.rt(mid)
        notes = (self.store.ms_get(mid, "revise") or {}).get("notes", "")
        replan = self.store.ms_get(mid, "replan")
        if replan:
            notes = (notes + "\n\n" if notes else "") + replan["notes"]
        self.store.ms_put(mid, "plan", {})  # this round's plan replaces the last
        await self._stage(mid, "plan", {"key": rt.key, "mid": mid, "notes": notes})
        if not self.store.ms_get(mid, "plan"):
            raise RuntimeError("plan_flow ended without a plan")
        self._ask_approval(mid)

    def _ask_approval(self, mid: str) -> None:
        rec = self.store.ms_get(mid, "plan") or {}
        ms = self.store.get_milestone(mid)
        risky = [c.get("id") for c in rec["plan"].get("cards") or [] if c.get("risk") == "high"]
        if ms.policy.approve_plan == "auto" and rec.get("passed") and not risky:
            self._approve(mid, actor="policy")
            return
        why = "the plan waits for the owner"
        if not rec.get("passed"):
            why += f" with {len(rec.get('problems') or []) + len(rec.get('notes') or [])} unresolved note(s)"
        elif risky:
            why += f" (risky cards: {', '.join(risky)})"
        self._move(mid, M.AWAITING_APPROVAL, why)

    def _approve(self, mid: str, actor: str = "owner") -> List[Card]:
        rec = self.store.ms_get(mid, "plan") or {}
        if not rec:
            raise Refused(f"{mid} has no plan to approve")
        if rec.get("problems"):
            raise Refused("the plan still fails the engine's checks: " + "; ".join(rec["problems"]) +
                          ". Revise it (replan with notes).")  # fmt: skip
        ctx = self.store.ms_get(mid, "context") or {}
        have = {c.id for c in self.store.cards(mid)}
        plan = rec["plan"]
        init = plan.get("init") if ctx.get("greenfield") and INIT_CARD not in have else None
        cards = cards_from_plan(mid, plan.get("cards") or [], init)
        if INIT_CARD in have:
            for c in cards:
                if not c.deps:
                    c.deps = [INIT_CARD]
        self.store.add_cards(cards, actor="planner")
        old = self.store.ms_get(mid, "epics") or {}
        epics = {"epics": plan.get("epics") or old.get("epics") or [],
                 "architecture": plan.get("architecture") or old.get("architecture", "")}  # fmt: skip
        self.store.ms_put(mid, "epics", epics)
        for k in ("revise", "replan"):
            if self.store.ms_get(mid, k):
                self.store.ms_put(mid, k, {})
        self._move(mid, M.RUNNING, f"plan approved ({len(cards)} cards)", actor=actor)
        return cards

    # ── execute ────────────────────────────────────────────────────────────
    def rows_left(self, mid: str) -> List[Dict[str, Any]]:
        """Rows in scope that no card serves yet (a failed card still counts:
        its rows wait for a replan, not for another card by itself)."""
        ctx = self.store.ms_get(mid, "context") or {}
        cards = self.store.cards(mid)
        have = covered_rows(cards, include_failed=True)
        return [r for r in ctx.get("rows") or [] if r["id"] not in have]

    def _replenisher(self, mid: str):
        async def replenish(cards: List[Card], n: int) -> List[Card]:
            left = self.rows_left(mid)
            if not left:
                return []
            rt = self.rt(mid)
            ms = self.store.get_milestone(mid)
            ctx = self.store.ms_get(mid, "context") or {}
            epics = self.store.ms_get(mid, "epics") or {}
            summary = [{"id": c.id, "title": c.title, "state": c.state.value, "rows": c.rows, "paths": c.paths,
                        "epic": c.epic, "reason": c.reason[:200]} for c in cards]  # fmt: skip
            prompt = replenish_prompt(ms.goal, ctx.get("brief", ""), epics.get("epics") or [],
                                      epics.get("architecture", ""), summary, left, n)  # fmt: skip
            name = f"replenish-{len(cards)}"
            req = stage_request(rt, mid, "planner", "replenisher", prompt, rt.git.ensure_integration(mid), name,
                                schema=REPLENISH_SCHEMA)  # fmt: skip
            res = await stage_call(rt, mid, name, req, key=f"{rt.key}:{name}")
            if not res.ok or not isinstance(res.output, dict):
                LOGGER.warning(f"{mid}: the replenisher ended {res.status}: {res.error}")
                return []
            scope = [r["id"] for r in ctx.get("rows") or []]
            kept: List[Card] = []
            for c in cards_from_plan(mid, res.output.get("cards") or []):
                if not c.deps and INIT_CARD in {x.id for x in cards}:
                    c.deps = [INIT_CARD]
                bad = check_cards([c], scope, existing=[*cards, *kept], max_cards=ms.policy.budget.max_cards)
                if bad:
                    self.store.note(mid, "replenish.dropped", {"card": c.id, "problems": bad})
                    continue
                kept.append(c)
            return kept

        return replenish

    async def _execute(self, mid: str) -> None:
        rt = self.rt(mid)
        ms = self.store.get_milestone(mid)
        runners = Runners(self._flow_runner(mid), InitRunner(rt))
        sched = Scheduler(
            self.store,
            mid,
            runners,
            GitIntegrator(rt),
            replenisher=self._replenisher(mid) if ms.policy.horizon == "ready_depth" else None,
            rows_left=lambda: len(self.rows_left(mid)),
            deadline=time.time() + max(0.0, ms.policy.budget.max_hours * 3600 - self.store.running_seconds(mid)),
            hold_max_s=self.hold_max_s,
            on_card_end=lambda cid: self.board(mid, f"{cid} {self.store.get_card(mid, cid).state.value}"),
        )
        for cid, answer in self._queued.pop(mid, []):
            sched.answer(cid, answer)
        self.schedulers[mid] = sched
        try:
            out = await sched.run()
        finally:
            self.schedulers.pop(mid, None)
        self.store.note(mid, "execute.outcome", {"outcome": out.value, "starts": sched.stats.starts,
                                                 "replenished": sched.stats.replenished})  # fmt: skip
        cards = self.store.cards(mid)
        if out == Outcome.COMPLETE:
            self._move(mid, M.VERIFYING, "every card is done")
        elif out == Outcome.WAITING:
            waiting = [c for c in cards if c.state == CardState.WAITING_HUMAN]
            owner = [c.id for c in waiting if (c.detail.get("question") or {}).get("kind") != "taken"]
            takers = [f"{c.id} ({c.detail.get('taken', {}).get('actor')})" for c in waiting if c.id not in owner]
            why = "; ".join(
                x
                for x in (
                    f"waiting on the owner: {', '.join(owner)}" if owner else "",
                    f"waiting on a submit: {', '.join(takers)}" if takers else "",
                )
                if x
            )
            self._move(mid, M.WAITING_HUMAN, why)
        elif out == Outcome.PAUSED:
            self._move(mid, M.PAUSED, "paused by the owner", actor="owner")
        elif out == Outcome.CANCELLED:
            self._move(mid, M.CANCELLED, "cancelled by the owner", actor="owner")
        elif out == Outcome.RATE_LIMITED:
            when = time.strftime("%H:%M", time.localtime(sched.resume_at))
            self.store.update_milestone(mid, resume_at=sched.resume_at)
            self._move(mid, M.PAUSED, f"rate limited: the coder's limit resets at {when}; it goes on by itself")
            self.store.note(mid, "notice", {"text": f"⏸ {ms.title} ({mid}) paused: the coder's rate limit resets at "
                                                    f"{when}, and it goes on by itself then."})  # fmt: skip
        elif out == Outcome.BUDGET:
            if sched.over_time():
                hours = self.store.running_seconds(mid) / 3600
                self._move(mid, M.BUDGET_EXHAUSTED, f"ran {hours:.1f} h of {ms.policy.budget.max_hours:g} h "
                                                    "with work left")  # fmt: skip
            else:
                self._move(mid, M.BUDGET_EXHAUSTED, f"spent ${ms.policy.budget.max_usd:.2f} with work left")
        else:
            failed = [c for c in cards if c.state == CardState.FAILED]
            why = "; ".join(f"{c.id} failed: {c.reason[:200]}" for c in failed) or "nothing can run"
            if failed and ms.replans < ms.policy.budget.max_replans:
                self.replan(mid, f"cards failed for good: {why}")
            else:
                self._move(mid, M.FAILED, why)

    def replan(self, mid: str, why: str, actor: str = "engine") -> None:
        """Re-plan the part not started yet (§6): done cards are kept and never
        redone; cards not started are cancelled as replaced; at most
        ``max_replans`` per milestone."""
        ms = self.store.get_milestone(mid)
        if ms.replans >= ms.policy.budget.max_replans:
            raise Refused(f"{mid} has had its {ms.policy.budget.max_replans} replans")
        cards = self.store.cards(mid)
        done = [c for c in cards if c.state == CardState.DONE]
        failed = [c for c in cards if c.state == CardState.FAILED]
        for c in cards:
            if c.state in (CardState.PENDING, CardState.READY, CardState.BLOCKED):
                self.store.move_card(mid, c.id, CardState.CANCELLED, "replaced by a replan", actor=actor)
            elif c.state == CardState.FAILED:
                self.store.move_card(mid, c.id, CardState.CANCELLED, f"replaced by a replan; it had failed: "
                                     f"{c.reason[:300]}", actor=actor)  # fmt: skip
        notes = (
            f"This is a replan: {why}\nDone cards are kept and never redone (new cards may depend on them): "
            + json.dumps([{"id": c.id, "title": c.title, "rows": c.rows, "paths": c.paths} for c in done])
            + "\nFailed cards (do not reuse their ids; plan their work differently): "
            + json.dumps([{"id": c.id, "title": c.title, "rows": c.rows, "reason": c.reason[:300]} for c in failed])
            + "\nCard ids already used: "
            + ", ".join(c.id for c in cards)
        )
        self.store.ms_put(mid, "replan", {"notes": notes, "why": why})
        self.store.update_milestone(mid, replans=ms.replans + 1)
        # stopped before its requirements were read: the planner has no context record yet, so the
        # milestone starts over from discovery (the notes wait for the planner)
        to = M.PLANNING if self.store.ms_get(mid, "context") else M.DISCOVERING
        self._move(mid, to, f"replan {ms.replans + 1}: {why[:300]}", actor=actor)

    # ── verify and deliver ─────────────────────────────────────────────────
    async def _verify(self, mid: str) -> None:
        rt = self.rt(mid)
        ms = self.store.get_milestone(mid)
        await self._stage(mid, "final", {"key": rt.key, "mid": mid})
        rec = self.store.ms_get(mid, "final") or {"gaps": []}
        gaps = rec["gaps"]
        if not gaps:
            await self._retro(mid)
            self.deliver(mid)
            self._move(mid, M.DONE, f"delivered on {self.p.git.integration_branch(mid)}")
            return
        mine = [g for g in gaps if not g.get("card")]
        fixes = int(self.store.ms_get(mid, "final_fixes") or 0)
        if mine and (fixes >= ms.policy.budget.max_reopens or any(g["why"] not in FIXABLE for g in mine)):
            self._move(mid, M.FAILED, "final verification: " + " | ".join(g["reason"][:300] for g in mine))
            return
        if mine:
            fix = self._fix_card(mid, mine)
            self.store.ms_put(mid, "final_fixes", fixes + 1)
        by_card: Dict[str, List[str]] = {}
        for g in gaps:
            if not g.get("card"):
                continue
            by_card.setdefault(g["card"], []).append(f"[{g['why']}] {g['reason']}")
        over = []
        for cid, reasons in by_card.items():
            card = self.store.get_card(mid, cid)
            if card.reopens >= ms.policy.budget.max_reopens:
                over.append(cid)
                continue
            detail = dict(card.detail, reopen={"why": "final verification", "reason": "\n".join(reasons)})
            self.store.move_card(mid, cid, CardState.READY, "re-opened by final verification",
                                 reopens=card.reopens + 1, detail=detail)  # fmt: skip
        if over:
            self._move(mid, M.FAILED, f"final verification still finds gaps on {', '.join(over)} after "
                                      f"{ms.policy.budget.max_reopens} re-opens")  # fmt: skip
            return
        added = [f"added {fix.id}"] if mine else []
        self._move(mid, M.RUNNING, f"final verification {', '.join(added + [f're-opened {c}' for c in by_card])}")

    async def _retro(self, mid: str) -> None:
        """The retrospective (retro.py): its report, always; its lessons, one session, when the practices name
        a lessons file and the milestone had friction, committed to that file on the integration branch. A
        failure here is logged and noted: it never holds a delivery."""
        from operonx_project import retro
        from operonx_project.practices import PracticesError, load

        if self.store.ms_get(mid, "retro") is not None:
            return
        rt = self.rt(mid)
        rep = retro.report(self.store, mid)
        rec: Dict[str, Any] = {"report": rep, "lessons": [], "file": "", "commit": None}
        try:
            integ = rt.git.ensure_integration(mid)
            try:
                file = load(integ).lessons
            except PracticesError:
                file = ""
            if file and retro.friction(rep):
                existing = (integ / file).read_text(encoding="utf-8") if (integ / file).is_file() else ""
                req = stage_request(rt, mid, "planner", "retrospective", retro.lessons_prompt(rep, existing), integ,
                                    "retrospective", schema=retro.LESSONS_SCHEMA)  # fmt: skip
                res = await stage_call(rt, mid, "retrospective", req, key=f"{rt.key}:{mid}:retrospective")
                got = (res.output or {}).get("lessons") if res.ok and isinstance(res.output, dict) else None
                if got is None:
                    self.store.note(mid, "retro.failed", {"error": f"{res.status}: {res.error[:300]}"})
                elif retro.append(integ, file, mid, got):
                    rt.git.git("add", file, cwd=integ)
                    rec.update(lessons=retro.usable(got), file=file,
                               commit=rt.git.commit(integ, f"{mid}: lessons ({file})"))  # fmt: skip
        except Exception as e:  # noqa: BLE001 - a retrospective never holds a delivery
            LOGGER.warning(f"operonx-project: {mid}'s retrospective failed: {e}")
            self.store.note(mid, "retro.failed", {"error": str(e)[:300]})
        self.store.ms_put(mid, "retro", rec)
        self.store.note(mid, "retro", {"rounds": rep["rounds"], "cost_usd": rep["cost_usd"],
                                       "lessons": len(rec["lessons"]), "file": rec["file"]})  # fmt: skip

    def _fix_card(self, mid: str, gaps: List[Dict[str, Any]]) -> Card:
        """A card for what final verification found that no card owns: a check the milestone's
        own steps broke (tipcalc m2: the requirements commit grew the matrix a test pinned), or
        cards that pass apart and fail together. It starts from the milestone's baseline, not its
        own: a failure already there when it starts must still be fixed."""
        n = max((int(m.group(1)) for c in self.store.cards(mid) if (m := re.fullmatch(r"C(\d+)", c.id))),
                default=0) + 1  # fmt: skip
        found = "\n\n".join(f"[{g['why']}] {g['reason']}" for g in gaps)
        base = self.store.ms_get(mid, "baseline") or {}
        card = Card(id=f"C{n}", milestone=mid, title="Fix what final verification found", kind="code",
                    intent="Final verification of the whole milestone, on its integration branch, found this, "
                           "and no single card owns it. Fix it at its cause; change a test only where the test "
                           f"itself is wrong.\n\n{found[:6000]}",
                    criteria=["When the repo's checks run on the integration branch, they pass as they did "
                              "before the milestone, and each finding above is resolved."],
                    state=CardState.READY,
                    detail={"baseline": base.get("results") or [], "final_fix": found[:6000],
                            # its criteria are the repo's own checks: no tests of its own to write
                            "acceptance": {"dir": "", "files": {}, "commit": None},
                            "heldout": {"dir": "", "files": []}})  # fmt: skip
        self.store.add_cards([card], actor="engine")
        self.store.note(mid, "final.fix_card", {"card": card.id, "gaps": [g["why"] for g in gaps]})
        return card

    def deliver(self, mid: str) -> Path:
        """``DELIVERY.md`` on the integration branch, and the PR description
        beside the store (plan §6, step 10). Nothing is pushed: a push or a PR
        is the owner's approval."""
        from operonx_project.delivery import delivery_text, pr_text

        rt = self.rt(mid)
        integ = rt.git.ensure_integration(mid)
        text = delivery_text(self, mid)
        (integ / "DELIVERY.md").write_text(text, encoding="utf-8")
        rt.git.git("add", "DELIVERY.md", cwd=integ)
        sha = rt.git.commit(integ, f"{mid}: DELIVERY.md")
        out = self.p.state / "delivery" / mid
        out.mkdir(parents=True, exist_ok=True)
        (out / "PR.md").write_text(pr_text(self, mid), encoding="utf-8")
        self.store.note(mid, "delivered", {"commit": sha, "pr": str(out / "PR.md")})
        return integ / "DELIVERY.md"

    # ── a card worked by a person or an assistant (plan D7) ─────────────────
    def _taken(self, kind: str, mid: str, cid: Optional[str], payload: Dict[str, Any], actor: str,
               sched: Optional[Scheduler]) -> str:  # fmt: skip
        try:
            card = self.store.get_card(mid, str(cid))
        except KeyError:
            raise Refused(f"no card {cid!r} in {mid}") from None
        taken = card.detail.get("taken") or {}
        parked = card.state == CardState.WAITING_HUMAN and (card.detail.get("question") or {}).get("kind") == "taken"
        if kind == "take":
            who = str(payload.get("actor") or actor)
            if taken:
                if taken.get("actor") == who:
                    return f"{card.id} is already yours"
                raise Refused(f"{card.id} is taken by {taken.get('actor')}")
            if card.state not in (CardState.READY, CardState.PENDING):
                raise Refused(f"{card.id} is {card.state.value}: only a card not started can be taken")
            done = {c.id for c in self.store.cards(mid) if c.state == CardState.DONE}
            waits = ", ".join(sorted(set(card.deps) - done)) if card.state == CardState.PENDING else ""
            first = min([c.priority for c in self.store.cards(mid)] + [0]) - 1
            self.store.update_card(mid, card.id, actor=actor, priority=first,
                                   detail=dict(card.detail, taken={"actor": who, "at": time.time()}))  # fmt: skip
            self.store.note(mid, "card.taken", {"actor": who}, card=card.id)
            if sched is not None:
                sched.wake()
            elif self.store.get_milestone(mid).state == M.WAITING_HUMAN:
                self._move(mid, M.RUNNING, f"{card.id} taken by {who}", actor=actor)
            if waits:  # taken ahead: when its dependencies are done it starts, and waits for its taker
                return f"{card.id} taken by {who}: it waits for {waits}, then for their submit"
            return f"{card.id} taken by {who}: it starts, then waits for their submit"
        if not taken:
            raise Refused(f"{card.id} is not taken")
        if kind == "release":
            self.store.update_card(mid, card.id, actor=actor, detail={k: v for k, v in card.detail.items()
                                                                      if k != "taken"})  # fmt: skip
            self.store.note(mid, "card.released", {"actor": actor}, card=card.id)
            if parked:
                self._answer(mid, card.id, {"released": True}, actor, sched)
            return f"{card.id} released: the engine's implementer works it"
        if not parked:
            raise Refused(
                f"{card.id} is {card.state.value}"
                + (f" ({card.stage.value})" if card.stage else "")
                + ": it is not waiting for a submit"
            )
        self.store.note(mid, "card.submitted", {"actor": actor, "summary": payload.get("summary", "")}, card=card.id)
        self._answer(mid, card.id, {"submitted": True, "summary": str(payload.get("summary") or "")}, actor, sched)
        return f"{card.id} submitted: checks, review and held-out tests run now"

    def _answer(self, mid: str, cid: str, answer: Any, actor: str, sched: Optional[Scheduler]) -> None:
        if sched is not None:
            sched.answer(cid, answer)
            return
        self._queued.setdefault(mid, []).append((cid, answer))
        if self.store.get_milestone(mid).state == M.WAITING_HUMAN:
            self._move(mid, M.RUNNING, f"{cid} resumed", actor=actor)

    # ── linked milestones (plan D17) ───────────────────────────────────────
    def unmet_needs(self, mid: str) -> List[str]:
        from operonx_project.links import unmet

        needs = (self.store.ms_get(mid, "needs") or {}).get("needs") or []
        return unmet(needs, self.p.git.project, self.p.state) if needs else []

    def check_dependencies(self) -> List[str]:
        """Start each milestone whose needs are all delivered now; the ids started."""
        started = []
        for ms in self.store.milestones():
            if ms.state != M.WAITING_DEPENDENCY:
                continue
            waits = self.unmet_needs(ms.id)
            if not waits:
                needs = (self.store.ms_get(ms.id, "needs") or {}).get("needs") or []
                self._move(ms.id, M.DISCOVERING, f"started: {', '.join(needs)} delivered")
                started.append(ms.id)
            elif ("waits for " + ", ".join(waits)) != ms.reason:  # a need moved on: the Board says where it is
                self.store.update_milestone(ms.id, reason="waits for " + ", ".join(waits))
        return started

    # ── owner commands ─────────────────────────────────────────────────────
    async def apply(self, cmd: Dict[str, Any]) -> str:
        """Run one queued command; raises :class:`Refused` when it does not apply."""
        kind, mid = cmd["kind"], cmd["milestone"]
        payload = cmd.get("payload") or {}
        actor = cmd.get("actor") or "owner"
        ms = self.store.get_milestone(mid)
        sched = self.schedulers.get(mid)
        if kind == "start":
            if ms.state != M.CREATED:
                raise Refused(f"{mid} is {ms.state.value}, not created")
            waits = self.unmet_needs(mid)
            if waits:
                self._move(mid, M.WAITING_DEPENDENCY, "waits for " + ", ".join(waits), actor=actor)
                return "waiting for " + ", ".join(waits)
            self._move(mid, M.DISCOVERING, "started", actor=actor)
            return "started"
        if kind == "approve":
            if ms.state != M.AWAITING_APPROVAL:
                raise Refused(f"{mid} is {ms.state.value}: there is no plan waiting for approval")
            cards = self._approve(mid, actor=actor)
            return f"approved: {len(cards)} cards"
        if kind == "replan":
            notes = str(payload.get("notes") or "")
            if ms.state == M.AWAITING_APPROVAL:
                self.store.ms_put(mid, "revise", {"notes": notes})
                self._move(mid, M.PLANNING, "the owner asked for a revision", actor=actor)
                return "revising the plan"
            if ms.state in (M.FAILED, M.WAITING_HUMAN, M.PAUSED):
                self.replan(mid, f"the owner asked: {notes}", actor=actor)
                return "replanning"
            raise Refused(f"{mid} is {ms.state.value}: pause it first")
        if kind == "answer":
            cid = cmd.get("card") or payload.get("card")
            card = self.store.get_card(mid, cid)
            if card.state != CardState.WAITING_HUMAN:
                raise Refused(f"{cid} is {card.state.value}, not waiting for an answer")
            if sched is not None:
                sched.answer(cid, payload.get("answer"))
            else:
                self._queued.setdefault(mid, []).append((cid, payload.get("answer")))
                if ms.state == M.WAITING_HUMAN:
                    self._move(mid, M.RUNNING, f"{cid} answered", actor=actor)
            return f"{cid} answered"
        if kind in ("take", "submit", "release"):
            return self._taken(kind, mid, cmd.get("card") or payload.get("card"), payload, actor, sched)
        if kind == "pause":
            if sched is not None:
                sched.pause()
                return "pausing: running cards finish their round"
            if ms.state == M.PAUSED and ms.resume_at:  # paused by a rate limit: now the owner holds it
                self.store.update_milestone(mid, resume_at=None, reason="paused by the owner", actor=actor)
                return "paused (it no longer goes on by itself at the rate limit's reset)"
            if M.PAUSED not in MILESTONE_MOVES[ms.state]:
                raise Refused(f"{mid} is {ms.state.value}")
            self._move(mid, M.PAUSED, "paused by the owner", actor=actor)
            return "paused"
        if kind == "unpause":
            if sched is not None:
                sched.unpause()
                return "unpaused"
            if ms.state != M.PAUSED:
                raise Refused(f"{mid} is {ms.state.value}, not paused")
            if ms.resume_at:
                self.store.update_milestone(mid, resume_at=None, actor=actor)
            if self.store.ms_get(mid, "rebuilt") and not self.store.ms_get(mid, "survey"):
                # rebuilt from the board files: the repo's brief and checks are found again first,
                # then its cards go on (no new plan: _plan sees the cards)
                self._move(mid, M.DISCOVERING, "unpaused: rebuilt from the board, discovering again", actor=actor)
                return "unpaused (discovering the repo again, then its cards go on)"
            self._move(mid, self.store.prior_state(mid) or M.RUNNING, "unpaused", actor=actor)
            return "unpaused"
        if kind == "cancel":
            if sched is not None:
                sched.cancel()
                return "cancelling"
            if ms.state in (M.DONE, M.CANCELLED):
                raise Refused(f"{mid} is {ms.state.value}")
            for c in self.store.cards(mid):
                if c.state in (CardState.PENDING, CardState.READY, CardState.WAITING_HUMAN, CardState.VERIFIED,
                               CardState.BLOCKED, CardState.RUNNING):  # fmt: skip
                    self.store.move_card(mid, c.id, CardState.CANCELLED, "the milestone was cancelled", actor=actor)
            self._move(mid, M.CANCELLED, "cancelled by the owner", actor=actor)
            return "cancelled"
        if kind == "retry":
            if ms.state == M.BUDGET_EXHAUSTED:
                policy = ms.policy
                hours = self.store.running_seconds(mid) / 3600
                out_of_time = hours >= policy.budget.max_hours
                if payload.get("hours") or (out_of_time and not payload.get("usd")):
                    extra_h = float(payload.get("hours") or policy.budget.max_hours)
                    policy.budget.max_hours = hours + extra_h
                    said = f"{extra_h:g} more hours"
                else:
                    extra = float(payload.get("usd") or policy.budget.max_usd)
                    policy.budget.max_usd = ms.cost_usd + extra
                    said = f"${extra:.2f} more"
                self.store.update_milestone(mid, policy=policy, actor=actor)
                back = (self.store.ms_get(mid, "exhausted_in") or {}).get("state")
                self.store.ms_put(mid, "exhausted_in", {})
                self._move(mid, M(back) if back else M.RUNNING, f"budget extended: {said}", actor=actor)
                return f"budget extended: {said}"
            if ms.state != M.FAILED:
                raise Refused(f"{mid} is {ms.state.value}, not failed")
            for c in self.store.cards(mid):
                if c.state == CardState.FAILED:
                    self.store.move_card(mid, c.id, CardState.READY, "retried by the owner", actor=actor)
            cards = self.store.cards(mid)
            to = M.RUNNING if cards else M.PLANNING
            self._move(mid, to, "retried by the owner", actor=actor)
            return f"retrying ({to.value})"
        raise Refused(f"unknown command {kind!r}")
