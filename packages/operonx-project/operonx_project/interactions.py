"""The owner's questions: one record each, from asked to delivered (plan §9).

An **interaction** is made for each thing that waits on the owner:

- a card parked on a question (its id is the run's ``interrupt_id``);
- a plan waiting for approval (one per plan version);
- an exhausted milestone budget.

Each has a ``Q-nn``. A card's own question (the implementer's, escalated
by triage) is also written to the BRD's ``open_questions.md``, on the
integration branch, and its answer becomes the entry's resolution (§4.3).

:meth:`Interactions.submit_answer` is the one way an answer comes in, from
the CLI, the studio or Telegram alike. It records the answer and queues
the command that acts on it in one transaction. A second answer, or one
to a question that is no longer current (a replan, a cancel, the card
moved on: its ``context_hash`` no longer matches), changes nothing and
says so. :meth:`Interactions.sync` makes, supersedes and closes the records;
:meth:`Interactions.tick` sends reminders and, at expiry, takes a safe
default when triage marked one.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from operonx.core.loggings import LOGGER

from operonx_project.model import CardState, MilestoneState
from operonx_project.store import Store

__all__ = ["Interactions", "Submitted", "label_answer"]

M = MilestoneState
OPEN, ANSWERED, DELIVERED, SUPERSEDED, REFUSED = "open", "answered", "delivered", "superseded", "refused"
_QTYPE = {"requirements": "ambiguity", "tradeoff": "ambiguity", "access": "gap", "authorization": "ambiguity"}


@dataclass
class Submitted:
    """What became of an answer: ``accepted``, ``already`` (answered before),
    ``stale`` (no longer current; ``current`` lists what is), ``unknown``,
    ``invalid`` (an option that does not exist, or words where a choice is needed)."""

    status: str
    message: str
    interaction: Optional[Dict[str, Any]] = None
    current: List[Dict[str, Any]] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.status == "accepted"


def _hash(*parts: Any) -> str:
    return hashlib.sha256(json.dumps(parts, sort_keys=True, default=str).encode()).hexdigest()[:16]


def _short(iid: str) -> str:
    return iid[:8] if re.fullmatch(r"[0-9a-f]{8,}", iid) else hashlib.sha256(iid.encode()).hexdigest()[:8]


def label_answer(options: List[Dict[str, Any]], text: str) -> Tuple[Optional[str], str]:
    """Free words → ``(option, words)``: ``"A"``, ``"a) and warn"``, ``"rewrite_tests. because …"`` or an
    option's exact label names an option; anything else is the owner's own words. The words always go on
    verbatim; the label is only a label."""
    opts = [o for o in options if isinstance(o, dict) and o.get("key")]
    keys = {str(o["key"]).lower(): str(o["key"]) for o in opts}
    labels = {str(o.get("label") or "").strip().lower(): str(o["key"]) for o in opts if o.get("label")}
    t = text.strip()
    if t.lower() in keys:
        return keys[t.lower()], ""
    if t.lower() in labels:
        return labels[t.lower()], ""
    # a key, then a separator: "a) …", and a whole key too (tipcalc m3/C5: the assistant answered
    # "rewrite_tests. The two held-out tests …", taken as words: the implementer got it and no test was rewritten)
    m = re.match(r"^([A-Za-z0-9_]+)\s*[\).:,\-—]\s*(.*)$", t, re.S)
    if m and m.group(1).lower() in keys:
        return keys[m.group(1).lower()], text
    return None, text


class Interactions:
    def __init__(
        self,
        store: Store,
        project: Any = None,  # a Project: with it, card questions are written to the BRD as Q-nn
        remind_after_s: float = 24 * 3600,
        expire_after_s: float = 72 * 3600,
        now: Callable[[], float] = time.time,
    ):
        self.store = store
        self.project = project
        self.remind_after_s = remind_after_s
        self.expire_after_s = expire_after_s
        self.now = now

    # ── what is current ────────────────────────────────────────────────────
    def _context(self, inter: Dict[str, Any]) -> Optional[str]:
        """The context hash the interaction would have now; ``None`` when what
        it asks about is gone (the card moved on, the plan was replaced)."""
        try:
            ms = self.store.get_milestone(inter["milestone"])
        except KeyError:
            return None
        if inter["kind"] == "card":
            card = self.store.get_card(ms.id, inter["card"])
            q = card.detail.get("question") or {}
            if card.state != CardState.WAITING_HUMAN or q.get("interrupt_id") != inter["id"] or ms.state == M.CANCELLED:
                return None
            return _hash(card.title, card.intent, card.criteria, card.paths, card.run_id, ms.replans, inter["id"])
        if inter["kind"] == "approval":
            n = ((self.store.ms_get(ms.id, "runs") or {}).get("plan") or {}).get("n")
            return _hash("plan", n, ms.replans) if ms.state == M.AWAITING_APPROVAL else None
        if inter["kind"] == "budget":
            b = ms.policy.budget
            return _hash("budget", b.max_usd, b.max_hours) if ms.state == M.BUDGET_EXHAUSTED else None
        return None

    def current(self, inter: Dict[str, Any]) -> bool:
        return self._context(inter) == inter["context_hash"]

    def open(self, mid: Optional[str] = None) -> List[Dict[str, Any]]:
        return [i for i in self.store.interactions(mid, [OPEN]) if self.current(i)]

    # ── making them ────────────────────────────────────────────────────────
    def _next_qid(self, brd: Optional[Path]) -> str:
        n = [int(i["qid"][2:]) for i in self.store.interactions() if (i.get("qid") or "").startswith("Q-")]
        if brd is not None:
            from operonx_project.brd.records import load_questions

            n += [int(q.id.split("-")[1]) for q in load_questions(brd)]
        return f"Q-{max(n, default=0) + 1:02d}"

    def _brd(self, mid: str) -> Optional[Path]:
        if self.project is None:
            return None
        ctx = self.store.ms_get(mid, "context") or {}
        if not ctx.get("brd"):
            return None
        d = self.project.git.integration_path(mid) / ctx["brd"]
        return d if d.exists() else None

    def _write_brd(self, mid: str, fn: Callable[[Path], Any], message: str) -> Any:
        """Change the BRD on the integration branch, committed, under the merge lock."""
        brd = self._brd(mid)
        if brd is None:
            return None
        git = self.project.git
        with git.merge_lock():
            out = fn(brd)
            integ = git.integration_path(mid)
            git.git("add", "-A", str(brd.relative_to(integ)), cwd=integ)
            git.commit(integ, message)
        return out

    def _add(self, iid: str, mid: str, card: Optional[str], kind: str, payload: Dict[str, Any], version: int) -> None:
        from operonx_project.brd.records import Question, add_questions

        base = {"id": iid, "short": _short(iid), "milestone": mid, "card": card, "kind": kind, "version": version,
                "payload": payload, "state": OPEN, "delivery": "pending", "messages": [], "shown": {},
                "created": self.now(), "expires_at": self.now() + self.expire_after_s, "flagged": 0,
                "updated": self.now()}  # fmt: skip
        base["context_hash"] = self._context(base) or ""
        qid = None
        if kind == "card" and payload.get("kind") == "human":
            q = Question("", _QTYPE.get(payload.get("category", ""), "ambiguity"),
                         f"{card}: {payload.get('problem') or payload.get('reason', '')}"[:160],
                         fact=payload.get("why") or payload.get("problem", ""),
                         interpretation=f"recommended: {payload['recommended']}" if payload.get("recommended") else "",
                         requirements=list(payload.get("rows") or []))  # fmt: skip
            added = self._write_brd(mid, lambda b: add_questions(b, [q]), f"{mid}/{card}: {q.topic[:60]}")
            qid = added[0].id if added else None
        base["qid"] = qid or self._next_qid(self._brd(mid))
        self.store.add_interaction(base)
        LOGGER.info(f"{mid}: {base['qid']} opened ({kind}{', ' + card if card else ''})")

    def sync(self) -> List[str]:
        """Open what waits on the owner, supersede what no longer does, and
        mark answers delivered once their command ran. Returns changed ids."""
        changed: List[str] = []
        have = {i["id"] for i in self.store.interactions()}
        for ms in self.store.milestones():
            for c in self.store.cards(ms.id):
                q = c.detail.get("question") or {}
                iid = q.get("interrupt_id")
                if q.get("kind") == "taken":  # waits for its taker's submit, not for the owner
                    continue
                if c.state == CardState.WAITING_HUMAN and iid and iid not in have:
                    rows = {"rows": c.rows, "title": c.title}
                    self._add(iid, ms.id, c.id, "card", {**q, **rows}, version=int(c.detail.get("runs", 1)))
                    changed.append(iid)
            if ms.state == M.AWAITING_APPROVAL:
                n = ((self.store.ms_get(ms.id, "runs") or {}).get("plan") or {}).get("n", 1)
                iid = f"plan:{ms.id}:{n}:{ms.replans}"
                if iid not in have:
                    rec = self.store.ms_get(ms.id, "plan") or {}
                    payload = {"problem": f"the plan of {ms.title} (v{n})", "plan": rec.get("plan") or {},
                               "problems": rec.get("problems") or [], "notes": rec.get("notes") or [],
                               "options": [{"key": "approve", "label": "Approve"},
                                           {"key": "revise", "label": "Revise (reply with what to change)"},
                                           {"key": "reject", "label": "Reject (cancel the milestone)"}]}  # fmt: skip
                    if rec.get("problems"):
                        payload["options"] = payload["options"][1:]  # cannot be approved as it is
                    self._add(iid, ms.id, None, "approval", payload, version=n)
                    changed.append(iid)
            if ms.state == M.BUDGET_EXHAUSTED:
                b = ms.policy.budget
                iid = f"budget:{ms.id}:{b.max_usd:g}:{b.max_hours:g}"
                if iid not in have:
                    hours = self.store.running_seconds(ms.id) / 3600
                    if hours >= b.max_hours:
                        what, extra = "hours", b.max_hours
                        problem = f"{ms.title} ran {hours:.1f} h of {b.max_hours:g} h with work left"
                        label = f"Extend by {extra:g} h"
                    else:
                        what, extra = "usd", b.max_usd
                        problem = f"{ms.title} spent ${ms.cost_usd:.2f} of ${b.max_usd:.2f} with work left"
                        label = f"Extend by ${extra:g}"
                    payload = {"problem": problem, "extra": extra, "what": what,
                               "options": [{"key": "extend", "label": label},
                                           {"key": "stop", "label": "Stop (cancel the milestone)"}]}  # fmt: skip
                    self._add(iid, ms.id, None, "budget", payload, version=1)
                    changed.append(iid)
        for inter in self.store.interactions(states=[OPEN]):
            if not self.current(inter):
                self.store.update_interaction(inter["id"], state=SUPERSEDED)
                changed.append(inter["id"])
        for inter in self.store.interactions(states=[ANSWERED]):
            cmd = self.store.command(inter["command"]) if inter.get("command") else None
            if cmd is None or cmd["status"] == "pending":
                continue
            if cmd["status"] == "done":
                self.store.update_interaction(inter["id"], state=DELIVERED)
                self._resolved(inter)
            else:
                self.store.update_interaction(inter["id"], state=REFUSED, shown={**inter["shown"],
                                              "refused": cmd.get("result") or ""})  # fmt: skip
            changed.append(inter["id"])
        return changed

    def _resolved(self, inter: Dict[str, Any]) -> None:
        if inter["kind"] != "card" or (inter["payload"] or {}).get("kind") != "human":
            return
        from operonx_project.brd.records import resolve_question

        a = inter.get("answer") or {}
        said = " — ".join(x for x in (str(a.get("choice") or ""), str(a.get("text") or "")) if x)
        when = time.strftime("%Y-%m-%d %H:%M", time.localtime(inter.get("answered_at") or self.now()))
        text = f"{said} ({inter.get('answered_by')}, {when})"
        self._write_brd(inter["milestone"], lambda b: resolve_question(b, inter["qid"], text),
                        f"{inter['milestone']}: {inter['qid']} resolved")  # fmt: skip

    # ── answers ────────────────────────────────────────────────────────────
    def find(self, ref: str, mid: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """An interaction by id, short id, ``Q-nn`` or card id (its newest)."""
        got = self.store.get_interaction(ref)
        if got is not None:
            return got
        rows = [i for i in self.store.interactions(mid) if i.get("qid") == ref or i.get("card") == ref]
        live = [i for i in rows if i["state"] == OPEN]
        return (live or rows)[-1] if rows else None

    def submit_answer(self, ref: str, answer: Any, actor: str = "owner", via: str = "cli",
                      mid: Optional[str] = None) -> Submitted:  # fmt: skip
        inter = self.find(ref, mid)
        if inter is None:
            return Submitted("unknown", f"no question {ref!r}")
        if inter["state"] in (ANSWERED, DELIVERED):
            a = inter.get("answer") or {}
            when = time.strftime("%H:%M", time.localtime(inter.get("answered_at") or 0))
            return Submitted("already", f"already answered ({a.get('choice') or 'in words'}, {when})", inter)
        if inter["state"] != OPEN or not self.current(inter):
            if inter["state"] == OPEN:
                self.store.update_interaction(inter["id"], state=SUPERSEDED)
            now_open = self.open(inter["milestone"])
            msg = "this question is no longer current" + (
                "; the current one: " + ", ".join(f"{i['qid']} ({i.get('card') or i['kind']})" for i in now_open)
                if now_open else "")  # fmt: skip
            return Submitted("stale", msg, inter, now_open)
        options = (inter["payload"] or {}).get("options") or []
        if isinstance(answer, dict):
            choice, text = answer.get("choice"), str(answer.get("text") or "")
        else:
            choice, text = label_answer(options, str(answer))
        keys = [o.get("key") for o in options if isinstance(o, dict)]
        if choice is not None and choice not in keys:
            return Submitted("invalid", f"{choice!r} is not an option of {inter['qid']}: {', '.join(map(str, keys))}",
                             inter)  # fmt: skip
        record = {"choice": choice, "text": text, "via": via}
        kind, payload, card = self._command(inter, choice, text)
        if kind is None:
            return Submitted("invalid", payload["why"], inter)
        seq = self.store.answer_interaction(inter["id"], record, actor, kind, payload, card)
        if seq is None:
            return self.submit_answer(ref, answer, actor, via, mid)  # answered meanwhile: say so
        how = choice or ("in your words, no option chosen (options: " + ", ".join(map(str, keys)) + ")"
                         if keys else "in your words")  # fmt: skip
        return Submitted("accepted", f"{inter['qid']}: taken ({how})",
                         self.store.get_interaction(inter["id"]))  # fmt: skip

    def _command(self, inter: Dict[str, Any], choice: Optional[str], text: str):
        k = inter["kind"]
        if k == "card":
            ans: Any = {"choice": choice, "text": text} if choice else text
            return "answer", {"answer": ans}, inter["card"]
        if k == "approval":
            if choice == "approve" or (choice is None and text.strip().lower() in ("ok", "yes", "approve", "go")):
                return "approve", {}, None
            if choice == "reject":
                return "cancel", {"why": "the owner rejected the plan"}, None
            if not text.strip():
                return None, {"why": "a revision needs your words: reply to the plan with what to change"}, None
            return "replan", {"notes": text}, None
        if k == "budget":
            if choice == "stop":
                return "cancel", {"why": "the owner stopped it at the budget"}, None
            p = inter["payload"] or {}
            m = re.search(r"\+?\$?(\d+(?:\.\d+)?)\s*(h|hours?)?\b", text or "", re.I)
            if m and choice is None:  # words: "+20", "$20", "+3h"
                return "retry", {"hours" if m.group(2) else "usd": float(m.group(1))}, None
            return "retry", {"hours" if p.get("what") == "hours" else "usd": p.get("extra")}, None
        return None, {"why": f"unknown kind {k}"}, None

    # ── time ───────────────────────────────────────────────────────────────
    def tick(self) -> List[Tuple[str, str]]:
        """Reminders and expiry: ``[(id, "remind" | "defaulted" | "flagged")]``."""
        out: List[Tuple[str, str]] = []
        now = self.now()
        for inter in self.open():
            p = inter["payload"] or {}
            if now >= (inter.get("expires_at") or 1e18):
                if p.get("safe_default") and p.get("recommended"):
                    got = self.submit_answer(inter["id"], {"choice": p["recommended"], "text": ""}, actor="default",
                                             via="expiry")  # fmt: skip
                    if got.ok:
                        out.append((inter["id"], "defaulted"))
                        continue
                if not inter.get("flagged"):
                    self.store.update_interaction(inter["id"], flagged=1)
                    self.store.note(inter["milestone"], "interaction.expired", {"id": inter["id"], "qid": inter["qid"]},
                                    card=inter.get("card"))  # fmt: skip
                    out.append((inter["id"], "flagged"))
            elif now >= inter["created"] + self.remind_after_s and not inter.get("reminded_at"):
                self.store.update_interaction(inter["id"], reminded_at=now)
                out.append((inter["id"], "remind"))
        return out
