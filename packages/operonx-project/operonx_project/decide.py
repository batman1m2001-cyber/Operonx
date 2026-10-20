"""What a round's facts mean: the decision table of plan §7, as one pure function.

A round gathers facts (the coder's status, the guard, the checks, the
review, the held-out tests, the counters so far) and :func:`decide` maps
them to what happens next. Nothing here touches git, a coder or the store,
so every row of the table is a unit test.

The table is the termination proof. Each outcome is *verified*, *ask*,
*failed* or *requeue*, or it is a ``fix``/``revise`` that consumes one of a
finite set of counters: rounds (``max_rounds``), consecutive same-failure
rounds (3, then one revision, then ask), arbitrations (one per finding),
empty-diff retries (one) and the card's budget.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional, Tuple

__all__ = [
    "ESCALATION_CATEGORIES",
    "SAME_FAILURE_LIMIT",
    "Decision",
    "RoundFacts",
    "decide",
    "question_fingerprint",
]

#: Rounds in a row with the same failure signature before the approach is revised.
SAME_FAILURE_LIMIT = 3
#: Times in a row the held-out tests fail by the same names, the checks and the review passing, before the owner
#: is asked: a held-out test no implementation can pass loops to the round limit otherwise (tipcalc m3/C5: six
#: rounds, $1.50, on two tests that expected a direct op call to record its error).
HELDOUT_SAME_LIMIT = 2

#: What may reach the owner (plan §9.1); anything else triage decides itself.
ESCALATION_CATEGORIES = (
    "requirements",  # ambiguous or conflicting requirements
    "tradeoff",  # architecture, public API, data migration
    "access",  # credentials or access that are missing
    "authorization",  # anything irreversible or outward-facing
    "repeated_failure",
    "budget",
)


@dataclass
class RoundFacts:
    """Everything :func:`decide` looks at. Built by ``round_``; plain data."""

    round: int  # this round's number, from 1
    max_rounds: int
    kind: str = "code"  # the card's kind; an empty diff matters for every kind but init (the engine's own)
    coder_status: str = "ok"  # the implementer's CoderResult.status
    coder_error: str = ""
    resets_at: Optional[float] = None
    # the implementer asked for a decision, and what triage made of it
    needs_human: Optional[Dict[str, Any]] = None
    triage: Optional[Dict[str, Any]] = None  # {action: answer|assume|escalate, answer, category, message}
    guard_blockers: List[str] = field(default_factory=list)
    empty_diff: bool = False  # the card has no change at all after this round
    empty_retries: int = 0  # earlier rounds that ended with an empty diff
    checks_ok: bool = True
    new_failures: List[str] = field(default_factory=list)  # check names / test ids failing anew
    failure_tail: str = ""
    same_failure: int = 0  # consecutive rounds, this one included, with this failure signature
    revised: bool = False  # the approach was already revised once
    # review: findings that block (blocker/major, citation verified, not waived)
    findings: List[Dict[str, Any]] = field(default_factory=list)
    # repeated findings the arbiter upheld: still unresolved after a fix and an arbitration
    upheld: List[Dict[str, Any]] = field(default_factory=list)
    heldout_ok: Optional[bool] = None  # None: not run (off, or the round did not get that far)
    heldout_failures: List[str] = field(default_factory=list)  # names and messages only
    heldout_same: int = 0  # held-out runs in a row, this one included, failing by these same names
    cost_usd: float = 0.0  # the card's spend so far
    card_max_usd: float = 5.0


@dataclass
class Decision:
    """``next``: ``verified``, ``fix``, ``revise`` (a new implementer session),
    ``ask``, ``failed`` or ``requeue``. ``feedback`` is what the next round's
    implementer is told; ``question`` what the owner is asked."""

    next: str
    reason: str
    feedback: str = ""
    question: Optional[Dict[str, Any]] = None
    not_before: Optional[float] = None
    counts_empty: bool = False  # this round's empty diff is the one allowed retry

    def __post_init__(self) -> None:
        if self.next not in ("verified", "fix", "revise", "ask", "failed", "requeue"):
            raise ValueError(f"unknown decision {self.next!r}")

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def question_fingerprint(card: str, category: str, text: str) -> str:
    """The same question is never asked twice (plan §9.1): task, category and
    the question's words, normalized."""
    basis = f"{card}\n{category}\n{' '.join(text.lower().split())}"
    return hashlib.sha256(basis.encode()).hexdigest()[:16]


def _options(options: List[Any], recommended: Optional[str]) -> Tuple[List[Dict[str, Any]], Optional[str]]:
    """Every option a ``key`` and a ``label``: a triage's options came as ``{"option": "A: …"}``,
    which the Board drew as buttons named "undefined" that answered nothing (tipcalc rebuild/C3).
    A recommendation that names no key ("A: replace the tests") goes to the option it letters."""
    out: List[Dict[str, Any]] = []
    for i, o in enumerate(options or []):
        o = dict(o) if isinstance(o, dict) else {"label": str(o)}
        label = str(o.get("label") or o.get("option") or o.get("text") or o.get("title") or o.get("key") or "")
        out.append({"key": str(o.get("key") or f"option_{i + 1}"), "label": label.strip()[:300],
                    "description": o.get("description") or (label[300:] or None)})  # fmt: skip
    keys = {o["key"] for o in out}
    if recommended and recommended not in keys:
        head = re.match(r"\W*(\w+)", recommended)
        lettered = [o["key"] for o in out if head and re.match(rf"\W*{re.escape(head.group(1))}\b", o["label"])]
        recommended = lettered[0] if len(lettered) == 1 else None
    return out, recommended


def _escalation(category: str, problem: str, why: str, options: List[Any], **extra: Any) -> Dict:
    options, rec = _options(options, extra.get("recommended"))
    if "recommended" in extra:
        extra["recommended"] = rec
    return {"category": category, "problem": problem, "why": why, "options": options, **extra}


_KEEP_SKIP_STOP = [
    {"key": "keep", "label": "Keep trying", "recommended": True},
    {"key": "skip", "label": "Skip this card"},
    {"key": "stop", "label": "Stop the card"},
]


def _bounded(f: RoundFacts, d: Decision) -> Decision:
    """A fix or revise goes ahead only while the rounds and the card budget last."""
    if f.round >= f.max_rounds:
        q = _escalation(
            "repeated_failure",
            f"{f.round} rounds without passing: {d.reason}",
            f"the card's round limit is {f.max_rounds}",
            _KEEP_SKIP_STOP,
            tried=d.feedback[-1500:],
            engine=True,
        )
        return Decision("ask", f"max_rounds ({f.max_rounds}) reached: {d.reason}", d.feedback, question=q)
    if f.cost_usd >= f.card_max_usd:
        q = _escalation(
            "budget",
            f"the card has spent ${f.cost_usd:.2f} of ${f.card_max_usd:.2f}: {d.reason}",
            "the card's budget is used up",
            [
                {"key": "keep", "label": "Extend the card's budget", "recommended": True},
                {"key": "skip", "label": "Skip this card"},
                {"key": "stop", "label": "Stop the card"},
            ],
            engine=True,
        )
        return Decision("ask", f"card budget reached: {d.reason}", d.feedback, question=q)
    return d


def _bullets(items: List[str]) -> str:
    return "\n".join(f"- {x}" for x in items)


def _finding_text(fs: List[Dict[str, Any]]) -> str:
    return _bullets(
        [f"[{x.get('severity')}] {x.get('file')}:{x.get('line')}: {x.get('title')}. {x.get('detail', '')}".strip()
         for x in fs]
    )  # fmt: skip


def decide(f: RoundFacts) -> Decision:
    """Plan §7's table, first matching row wins."""
    # the coder could not do its turn: nothing about the code is known
    if f.coder_status == "rate_limited":
        return Decision("requeue", "rate limited", not_before=f.resets_at)
    if f.coder_status == "cancelled":
        return Decision("requeue", "the session was stopped")
    if f.coder_status == "infra":
        return Decision("failed", f"infrastructure: {f.coder_error or 'the coder could not run'}")
    if f.coder_status == "auth":
        q = _escalation(
            "access",
            "Claude Code is not signed in, or its sign-in stopped working",
            "no coder session can run until it is",
            [{"key": "retry", "label": "Signed in again: retry", "recommended": True},
             {"key": "stop", "label": "Stop the card"}],
            evidence=f.coder_error[-500:],
        )  # fmt: skip
        return Decision("ask", "the coder is not signed in", question=q)

    # the implementer asked for a decision
    if f.needs_human:
        t = f.triage or {"action": "escalate"}
        if t.get("action") in ("answer", "assume") and t.get("answer"):
            how = "decided from evidence" if t["action"] == "answer" else "decided as a routine assumption"
            fb = f"Your question: {f.needs_human.get('question', '')}\nAnswer ({how}): {t['answer']}"
            return _bounded(f, Decision("fix", f"needs_human, {how}", fb))
        msg = dict(t.get("message") or {})
        q = _escalation(
            t.get("category") or "requirements",
            msg.get("problem") or f.needs_human.get("question", ""),
            msg.get("why") or f.needs_human.get("why", ""),
            msg.get("options") or f.needs_human.get("options") or [],
            tried=msg.get("tried", ""),
            recommended=msg.get("recommended"),
            safe_default=bool(msg.get("safe_default")),
        )
        return Decision("ask", "the implementer needs a decision", question=q)

    # the guard refused part of the diff (it was reverted, not committed)
    if f.guard_blockers:
        fb = "Some of your changes were refused and reverted:\n" + _bullets(f.guard_blockers)
        return _bounded(f, Decision("fix", "guard: " + "; ".join(f.guard_blockers)[:300], fb))

    if f.empty_diff and f.kind != "init":  # a docs card unchanged is as unmet as a code card (tipcalc m1/C6)
        if f.empty_retries >= 1:
            return Decision("failed", "the implementer made no change, twice")
        fb = "Your last turn changed no file. The card's criteria need changes to the files; make them."
        return _bounded(f, Decision("fix", "empty diff", fb, counts_empty=True))

    if not f.checks_ok:
        failing = _bullets(f.new_failures) or "- (see the output)"
        if f.same_failure >= SAME_FAILURE_LIMIT:
            if not f.revised:
                fb = (
                    f"A previous approach failed {f.same_failure} rounds in a row with the same failure:\n"
                    f"{failing}\n\nLast output:\n{f.failure_tail}\n\nTake a different approach."
                )
                return _bounded(f, Decision("revise", f"same failure {f.same_failure}×: revise the approach", fb))
            q = _escalation(
                "repeated_failure",
                f"the same failure {f.same_failure} rounds in a row, after a revised approach",
                "neither approach got past it",
                _KEEP_SKIP_STOP,
                evidence=f.failure_tail[-1500:],
                tried="a fix in the same session, then a new session with a different approach",
                engine=True,
            )
            return Decision("ask", "the same failure after a revision", question=q)
        fb = f"These checks fail:\n{failing}\n\nOutput:\n{f.failure_tail}"
        return _bounded(f, Decision("fix", "checks fail: " + ", ".join(f.new_failures)[:300], fb))

    if f.upheld:
        q = _escalation(
            "requirements",
            "a review finding is still open after a fix and an arbitration",
            "the implementer and the reviewer disagree; a third reading sided with the reviewer",
            [
                {"key": "fix", "label": "The finding stands: fix it", "recommended": True},
                {"key": "waive", "label": "Waive the finding"},
                {"key": "stop", "label": "Stop the card"},
            ],
            evidence=_finding_text(f.upheld),
            findings=[x.get("fingerprint") for x in f.upheld],
        )
        return Decision("ask", "a repeated finding was upheld", question=q)

    if f.findings:
        fb = "The review found:\n" + _finding_text(f.findings)
        return _bounded(f, Decision("fix", f"review: {len(f.findings)} blocking finding(s)", fb))

    if f.heldout_ok is False and f.heldout_same >= HELDOUT_SAME_LIMIT:
        q = _escalation(
            "requirements",
            f"the same held-out tests failed {f.heldout_same} times in a row while the checks and the review passed",
            "the implementer cannot see them, so a held-out test that expects what no implementation can do (an "
            "engine's behaviour the rows do not promise) is never fixed by another round",
            _KEEP_SKIP_STOP,
            evidence="\n".join(f.heldout_failures)[-1500:],  # what a rewrite is told: the failing names, no "tried"
            engine=True,
        )
        return Decision("ask", f"held-out: the same {len(f.heldout_failures)} failing {f.heldout_same}×", question=q)
    if f.heldout_ok is False:
        fb = "The card's examiner tests (which you cannot see) fail. Names and messages only:\n" + _bullets(
            f.heldout_failures
        )
        return _bounded(f, Decision("fix", f"held-out: {len(f.heldout_failures)} failing", fb))

    return Decision("verified", "checks pass, review clean" + (", held-out pass" if f.heldout_ok else ""))
