"""The ops of ``task_flow``: prepare a card once, then run it round by round.

``prepare`` sets the card up: its worktree, the baseline checks, the
acceptance tests written by a separate test author (protected from the
implementer), and the held-out tests written by the examiner outside the
repo. What it made is kept in the card's ``detail``, so a run after a crash,
a rate limit or a re-open does each of these once. The project's practices
(read on the card's branch) set how: ``[tdd] mode = "strict"`` runs the
acceptance tests before any code and has them written again, once, when one
already passes (the red evidence is ``detail["red"]``); ``"off"`` writes
neither set.

``round_`` is one round (plan §7): implement → guard → commit → checks →
review (→ arbitration) → held-out → :func:`~operonx_project.decide.decide`.
Each step is a ``child()`` in the trace. A round re-run after a crash skips
the coder sessions that had ended, by checkpoints under the round's
idempotency key; checks run again (they are idempotent).

All state between rounds is the ``state`` cell, which the journal keeps:
rounds, the session, failure signatures, findings seen, questions asked.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from operonx import child, op, run_context
from operonx.core.loggings import LOGGER

from operonx_project.checks import CheckResult, compare, run_checks
from operonx_project.coder.base import CoderRequest, CoderResult, Started
from operonx_project.decide import ESCALATION_CATEGORIES, Decision, RoundFacts, decide, question_fingerprint
from operonx_project.gitops import conflict_markers, guard, sha256_file
from operonx_project.model import Card, Stage
from operonx_project.practices import PracticesError
from operonx_project.practices import load as load_practices
from operonx_project.prompts import (
    ARBITER_SCHEMA,
    AUTHOR_SCHEMA,
    IMPLEMENTER_SCHEMA,
    REVIEWER_SCHEMA,
    TRIAGE_SCHEMA,
    arbiter_prompt,
    examiner_prompt,
    implement_prompt,
    rebase_prompt,
    reviewer_prompt,
    test_author_prompt,
    triage_prompt,
)
from operonx_project.review import check_findings, heldout_failures
from operonx_project.runtime import Runtime, get_runtime

__all__ = ["keep", "prepare", "round_"]

#: Caches a check run leaves behind; never part of a card's change.
_JUNK = ("__pycache__/", ".pytest_cache/", ".mypy_cache/", ".ruff_cache/", ".hypothesis/")
_STOP = ("rate_limited", "cancelled", "infra", "auth")


# ── shared helpers ────────────────────────────────────────────────────────


def _results(rows: List[Dict[str, Any]]) -> List[CheckResult]:
    return [CheckResult(**r) for r in rows]


def _junk(path: str) -> bool:
    return any(j in f"/{path}" or path.startswith(j) for j in _JUNK) or path.endswith(".pyc")


def _clean_junk(rt: Runtime, wt: Path) -> None:
    for path in rt.git.dirty(wt):
        if _junk(path):
            f = wt / path
            if f.is_file():
                f.unlink()


async def _coder(
    rt: Runtime,
    card: Card,
    name: str,
    req: CoderRequest,
    remember_session: bool = False,
) -> CoderResult:
    """One coder session as a ``child()`` step; its cost goes on the card at once."""
    mid, cid = card.milestone, card.id

    def started(s: Started) -> None:
        if remember_session and s.session:
            rt.set_detail(mid, cid, session=s.session, pgid=s.pgid)

    req.on_activity = rt.activity_for(mid, cid, req.role)

    async with child(name, inputs={"role": req.role, "session": req.session, "prompt": req.prompt[-4000:]},
                     op_type="coder") as step:  # fmt: skip
        res = await rt.coder.run(req, started)
        step.outputs = {
            "status": res.status,
            "session": res.session,
            "text": res.text[-2000:],
            "output": res.output,
            "error": res.error,
        }
        step.attrs.update({"cost_usd": res.cost_usd, "turns": res.turns, "model": req.model or ""})
    if res.cost_usd:
        rt.store.add_cost(mid, res.cost_usd, card=cid)
    if remember_session and res.session:
        rt.set_detail(mid, cid, session=res.session)
    return res


def _request(rt: Runtime, card: Card, role: str, prompt: str, cwd: Path, name: str, **kw: Any) -> CoderRequest:
    now = rt.card(card.milestone, card.id)
    # the run's allowance (``card_budget``: the owner's "keep", "rewrite" or retry extend it), else the policy's
    budget = now.detail.get("card_budget") or rt.store.get_milestone(card.milestone).policy.budget.card_max_usd
    left = max(0.05, budget - now.cost_usd)
    hide = [] if role == "examiner" else [rt.heldout_root]
    return CoderRequest(
        prompt=prompt,
        cwd=cwd,
        role=role,
        model=rt.model_for(role),
        max_usd=min(rt.call_max_usd, left),
        timeout_s=rt.call_timeout_s,
        transcript=rt.transcript(card.milestone, card.id, name),
        sandbox=rt.sandbox,
        hide=hide,
        tags={"milestone": card.milestone, "card": card.id, "step": name},
        **kw,
    )


def _checked(ran: Optional[Dict[str, Any]]) -> Optional[CoderResult]:
    """A coder result saved by this round before a crash, if any."""
    return None if ran is None else CoderResult(**ran)


def _save(rt: Runtime, key: str, res: CoderResult) -> None:
    rt.store.save_checkpoint(
        key,
        {
            "status": res.status,
            "session": res.session,
            "text": res.text,
            "output": res.output,
            "cost_usd": res.cost_usd,
            "error": res.error,
            "resets_at": res.resets_at,
        },
    )


# ── prepare ───────────────────────────────────────────────────────────────


def _fresh_state(card: Card, rt: Runtime) -> Dict[str, Any]:
    budget = rt.store.get_milestone(card.milestone).policy.budget
    allowance = card.cost_usd + budget.card_max_usd  # a retried card starts with a whole budget, not what is left
    rt.set_detail(card.milestone, card.id, card_budget=allowance)
    return {
        "round": 0,
        "cap": budget.max_rounds,
        "card_budget": allowance,
        "mode": "first",  # first | resume | fresh (a new session) | rebase
        "feedback": "",
        "last_sig": "",
        "same": 0,
        "revised": False,
        "empty": 0,
        "open": [],  # the last review's blocking findings
        "seen": [],  # fingerprints that blocked in an earlier round
        "arbitrated": {},  # fingerprint → the arbiter's verdict
        "owner_fix": [],  # fingerprints the owner said stand
        "waived": [],
        "answered": 0,
        "asked": {},  # question fingerprint → the answer
        "pending": None,  # the question the run is parked on
        "decisions": [],
        "skip_implement": False,
        "last_summary": "",
        "abort": None,
    }


async def _baseline(rt: Runtime, card: Card, wt: Path) -> List[Dict[str, Any]]:
    """The card's checks on its base: the integration branch's last results when they
    stand for it (the merge before ran them on the same code), else run here."""
    checks, mid = rt.card_checks, card.milestone
    async with child("baseline", inputs={"checks": [c.name for c in checks]}, op_type="checks") as step:
        head = rt.git.head(wt)
        rows = rt.reuse(mid, rt.store.checkpoint(rt.integration_key(mid)), head, checks)
        rows = rows if rows is not None else rt.reuse(mid, rt.store.ms_get(mid, "baseline"), head, checks)
        reused = rows is not None
        if rows is None:
            rows = [r.to_dict() for r in await run_checks(checks, wt, rt.env)]
        step.outputs = {"failing": [r["name"] for r in rows if not r["ok"]], "reused": reused}
    return rows


async def _author(rt: Runtime, card: Card, wt: Path, wrong: str = "",
                  by: str = "the owner") -> Tuple[Optional[Dict[str, Any]], Optional[CoderResult]]:  # fmt: skip
    """The acceptance tests, written before the code by a session of their own; with
    *wrong*, written again because *by* found them wrong (what was found)."""
    directory = f"{rt.acceptance_root}/{card.id}"
    req = _request(rt, card, "test_author", test_author_prompt(card, rt.brief, directory, wrong, by=by), wt,
                   "test-author", schema=AUTHOR_SCHEMA)  # fmt: skip
    res = await _coder(rt, card, "test_author", req)
    if not res.ok:
        return None, res
    _clean_junk(rt, wt)
    stray = [p for p in rt.git.dirty(wt) if not p.startswith(directory + "/")]
    if stray:
        LOGGER.warning(f"card {card.id}: the test author changed {stray}; reverted")
        rt.git.revert(wt, stray)
    # every file git sees in the directory, not only the ones this session changed: a rewrite that keeps a file
    # keeps it protected (and checked); never a cache a test run left (tipcalc m3/C5 listed a .pyc the red run
    # wrote, which the guard could only restore from a commit that never had it)
    changed = [p for p in rt.git.dirty(wt) if p.startswith(directory + "/")]
    seen = rt.git.git("ls-files", "--cached", "--others", "--exclude-standard", "--", directory, cwd=wt).split("\n")
    files = sorted(p for p in seen if p and (wt / p).is_file() and not _junk(p))
    if not files:
        LOGGER.warning(f"card {card.id}: the test author wrote no test")
        return {"dir": directory, "files": {}, "commit": None}, res
    sha = rt.git.commit(wt, f"{card.milestone}/{card.id}: acceptance tests") if changed else rt.git.head(wt)
    return {"dir": directory, "files": {p: sha256_file(wt / p) for p in files}, "commit": sha}, res


#: pytest's summary: "3 failed, 1 passed in 0.2s".
_PASSED = re.compile(r"\b(\d+) passed\b")
#: Said to the test author when a strict card's tests already pass before any code.
VACUOUS = ("These tests already pass before any code is written, so they check nothing this card adds ({passed} "
           "passed on the card's base). Under test-first, every acceptance test fails before the code (red) and "
           "passes after it (green): test the behaviour the card adds, through what will expose it.")  # fmt: skip


def _tdd(rt: Runtime, card: Card, wt: Path) -> str:
    """The card's TDD mode, from the practices on its own branch."""
    try:
        return load_practices(wt).tdd
    except PracticesError as e:  # discovery refused a broken file; one changed on a branch is not this op's to fail
        LOGGER.warning(f"card {card.id}: the practices do not parse ({e}); acceptance tests as usual")
        return "acceptance"


async def _red(rt: Runtime, card: Card, wt: Path, acc: Dict[str, Any]) -> Dict[str, Any]:
    """The acceptance tests run on the card's base, before any code: how many fail (red) and pass."""
    check = rt.test_check("acceptance", rt.acceptance_cmd.format(dir=acc["dir"]))
    async with child("red", inputs={"files": len(acc["files"])}, op_type="checks") as step:
        (res,) = await run_checks([check], wt, rt.env)
        passed = sum(int(n) for n in _PASSED.findall(res.tail[-2000:]))
        out = {"red": not res.ok and passed == 0, "exit_code": res.exit_code, "failed": res.failures[:50],
               "passed": passed if not res.ok else max(passed, 1), "tail": res.tail[-1500:]}  # fmt: skip
        step.outputs = {k: out[k] for k in ("red", "passed", "failed")}
    return out


async def _author_red(rt: Runtime, card: Card, wt: Path) -> Tuple[Optional[Dict[str, Any]], Optional[CoderResult]]:
    """Strict TDD: the acceptance tests, then the proof that they fail before the code; written again,
    once, when one passes. A card whose tests still pass after that goes on, with the evidence recorded."""
    acc, res = await _author(rt, card, wt)
    if acc is None or not acc["files"]:
        return acc, res
    red = await _red(rt, card, wt, acc)
    rewritten = False
    if not red["red"]:
        rt.store.note(card.milestone, "acceptance.vacuous", {"passed": red["passed"]}, card=card.id)
        again, res2 = await _author(rt, card, wt, VACUOUS.format(passed=red["passed"]), by="the engine")
        if again is None:
            return None, res2
        if again["files"]:
            acc, res, rewritten = again, res2, True
            red = await _red(rt, card, wt, acc)
    rt.set_detail(card.milestone, card.id, red={**red, "rewritten": rewritten})
    return acc, res


async def _examine(rt: Runtime, card: Card, wt: Path,
                   wrong: str = "") -> Tuple[Optional[Dict[str, Any]], Optional[CoderResult]]:  # fmt: skip
    """Held-out tests (V2): from the same rows, outside the repo, seen by no one else;
    with *wrong*, corrected because the owner found them wrong (what was found)."""
    hdir = rt.heldout_dir(card.milestone, card.id)
    hdir.mkdir(parents=True, exist_ok=True)
    req = _request(rt, card, "examiner", examiner_prompt(card, rt.brief, str(hdir), wrong), wt, "examiner",
                   add_dirs=[hdir], allow_write=[str(hdir)])  # fmt: skip
    res = await _coder(rt, card, "examiner", req)
    dirty = rt.git.dirty(wt)
    if dirty:  # the examiner may not change the repo
        LOGGER.warning(f"card {card.id}: the examiner changed {dirty}; reverted")
        rt.git.revert(wt, dirty)
    if not res.ok:
        return None, res
    files = sorted(str(p.relative_to(hdir)) for p in hdir.rglob("*.py"))
    return {"dir": str(hdir), "files": files}, res


@op
async def prepare(key: str, mid: str, cid: str, reopen: dict) -> dict:
    rt = get_runtime(key)
    card = rt.card(mid, cid)
    st = _fresh_state(card, rt)
    async with child("worktree", inputs={"card": cid}, op_type="git") as step:
        rt.git.ensure_integration(mid)
        wt = rt.git.ensure_card(mid, cid)
        step.outputs = {"worktree": str(wt), "head": rt.git.head(wt)}
    detail = card.detail
    if "baseline" not in detail:
        rt.set_detail(mid, cid, baseline=await _baseline(rt, card, wt))
    tdd = _tdd(rt, card, wt) if card.kind == "code" else "acceptance"
    if tdd == "off" and card.kind == "code":  # no test author, no examiner: the checks and the review judge it
        if "acceptance" not in detail:
            rt.set_detail(mid, cid, acceptance={"dir": "", "files": {}, "commit": None, "tdd": "off"})
        if "heldout" not in detail:
            rt.set_detail(mid, cid, heldout={"dir": "", "files": [], "tdd": "off"})
        detail = rt.card(mid, cid).detail
    if card.kind == "code" and "acceptance" not in detail:
        acc, res = await (_author_red if tdd == "strict" else _author)(rt, card, wt)
        if acc is None:
            st["abort"] = _stopped(res, "the test author")
            return {"state": st}
        rt.set_detail(mid, cid, acceptance=acc)
    policy = rt.store.get_milestone(mid).policy
    if policy.heldout and card.kind == "code" and "heldout" not in detail:
        held, res = await _examine(rt, card, wt)
        if held is None:
            st["abort"] = _stopped(res, "the examiner")
            return {"state": st}
        rt.set_detail(mid, cid, heldout=held)
    card = rt.card(mid, cid)
    if card.detail.get("session"):
        st["mode"] = "resume"  # a run after a rate limit or a re-open goes on in the same session
    if reopen:
        async with child("update", inputs={"reopen": reopen}, op_type="git") as step:
            m = rt.git.update_card(mid, cid)
            step.outputs = {"status": m.status, "files": m.files}
        st["mode"] = "rebase"
        st["feedback"] = rebase_prompt(reopen.get("why", ""), reopen.get("reason", ""), m.files)
    return {"state": st}


def _stopped(res: Optional[CoderResult], who: str) -> Dict[str, Any]:
    if res is None:
        return {"status": "failed", "reason": f"{who} did not run"}
    if res.status in ("rate_limited", "cancelled"):
        return {"status": "requeue", "reason": f"{who}: {res.status}", "not_before": res.resets_at}
    return {"status": "failed", "reason": f"{who} failed ({res.status}): {res.error or res.text[-300:]}"}


# ── a round ───────────────────────────────────────────────────────────────


def _end(st: Dict[str, Any], status: str, reason: str, not_before: Optional[float] = None) -> dict:
    return {
        "next": "end",
        "question": None,
        "state": st,
        "outcome": {"status": status, "reason": reason, "not_before": not_before},
    }


def _choice(question: Dict[str, Any], answer: Any) -> Tuple[Optional[str], str]:
    """An answer is an option key, ``{"choice", "text"}``, or words."""
    keys = [o.get("key") for o in question.get("options") or [] if isinstance(o, dict)]
    if isinstance(answer, dict):
        c = answer.get("choice")
        return (c if c in keys else None), str(answer.get("text") or "")
    text = str(answer)
    return (text if text in keys else None), ("" if text in keys else text)


def _apply_answer(rt: Runtime, card: Card, st: Dict[str, Any], answer: Any) -> Optional[dict]:
    """The owner answered the question the run was parked on. Returns an
    ending, or ``None`` with ``st`` set up for the next round."""
    q = st.get("pending") or {}
    st["pending"] = None
    st["heldout_same"], st["heldout_last"] = 0, None  # rewritten tests, or the owner's "keep": a fresh count
    if q.get("fingerprint"):
        st["asked"][q["fingerprint"]] = answer
    choice, text = _choice(q, answer)
    policy = rt.store.get_milestone(card.milestone).policy.budget
    spent = rt.card(card.milestone, card.id).cost_usd
    # the card goes on: with at least a call's worth (mr-finance m33/F3: a rewrite past the budget got $0.05)
    if choice not in ("stop", "skip") and st["card_budget"] - spent < rt.call_max_usd:
        st["card_budget"] = spent + policy.card_max_usd
        rt.set_detail(card.milestone, card.id, card_budget=st["card_budget"])
    note = f" The owner adds: {text}" if text else ""
    rt.store.note(card.milestone, "answer.applied", {"question": q.get("fingerprint"), "choice": choice, "text": text,
                                                      "problem": str(q.get("problem") or q.get("reason") or "")[:600]},
                  card=card.id)  # fmt: skip
    if choice == "rewrite_tests":  # the tests that fail are wrong: whoever wrote them writes them again
        # a question asked before "rewrite" was recorded: its reason says whether the held-out tests failed
        which = q.get("rewrite") or ("heldout" if "held-out:" in str(q.get("reason", "")) else "acceptance")
        st["rewrite_tests"] = "\n".join(x for x in (q.get("tried") or q.get("evidence") or q.get("problem", ""),
                                                     text) if x)  # fmt: skip
        st["rewrite_which"] = which
        st["cap"] = st["round"] + policy.max_rounds
        st["same"], st["revised"], st["last_sig"] = 0, False, ""
        if which == "heldout":  # the code may be right: check it again against the corrected tests
            st["skip_implement"] = True
            st["feedback"] = "The owner had this card's held-out tests corrected: they were wrong." + note
        else:
            st["feedback"] = ("The owner had this card's acceptance tests rewritten: they were wrong. "
                              "Run them again." + note)  # fmt: skip
        return None
    if q.get("kind") == "taken":  # the taker submitted (or gave the card back to the engine)
        if isinstance(answer, dict) and answer.get("submitted"):
            st["submitted"] = True
            st["last_summary"] = str(answer.get("summary") or f"submitted by {q.get('taker')}")
        st["feedback"] = ""
        return None
    if choice == "stop":
        return _end(st, "failed", "stopped by the owner")
    if choice == "skip":
        return _end(st, "failed", "skipped by the owner")
    if q.get("kind") == "engine":  # max rounds, budget, repeated failure: keep (or words) goes on
        st["cap"] = st["round"] + policy.max_rounds
        st["same"], st["revised"], st["last_sig"] = 0, False, ""
        st["feedback"] = (st.get("feedback") or "") + "\n\nThe owner says: keep trying." + note
        return None
    if q.get("kind") == "auth":
        return None  # retry the round as it was
    if q.get("kind") == "finding":
        fps = list(q.get("findings") or [])
        if choice == "waive":
            st["waived"] += fps
            st["open"] = [f for f in st["open"] if f["fingerprint"] not in fps]
            st["skip_implement"] = True  # nothing to change: check and review again
            return None
        st["owner_fix"] += fps
        st["feedback"] = "The owner decided these findings stand; fix them:\n" + q.get("evidence", "") + note
        return None
    # the implementer's own question, escalated by triage
    label = next((o.get("label") for o in q.get("options") or [] if o.get("key") == choice), None)
    said = f"{label or choice}" + (f" ({text})" if text and label else text if not label else "")
    st["decisions"].append(f"{q.get('problem', '')}: {said}")
    st["feedback"] = f"Your question: {q.get('problem', '')}\nThe owner's decision: {said}"
    return None


async def _implement(rt: Runtime, card: Card, wt: Path, st: Dict[str, Any], ikey: str, n: int) -> CoderResult:
    saved = _checked(rt.store.checkpoint(f"{ikey}:implement"))
    if saved is not None:
        LOGGER.info(f"card {card.id} round {n}: implement had ended before the restart; not run again")
        return saved
    acc = card.detail.get("acceptance") or {}
    acceptance = acc.get("dir") if acc.get("files") else None
    session = card.detail.get("session")
    base = implement_prompt(card, rt.brief, acceptance, rt.shared)
    if st["mode"] == "fresh" or not session:
        session = None
        prompt = base + (f"\n\n{st['feedback']}" if st["feedback"] else "")
    else:
        prompt = st["feedback"] or "Continue the card."
    req = _request(rt, card, "implementer", prompt, wt, f"r{n}-implement", session=session,
                   schema=IMPLEMENTER_SCHEMA)  # fmt: skip
    res = await _coder(rt, card, "implement", req, remember_session=True)
    _save(rt, f"{ikey}:implement", res)
    return res


async def _triage(rt: Runtime, card: Card, wt: Path, st: Dict[str, Any], ikey: str, nh: Dict[str, Any]) -> Dict:
    fp = question_fingerprint(card.id, str(nh.get("category", "")), str(nh.get("question", "")))
    if fp in st["asked"]:  # never the same question twice
        return {"action": "answer", "answer": f"(asked before) {st['asked'][fp]}"}
    saved = rt.store.checkpoint(f"{ikey}:triage")
    if saved is None:
        req = _request(rt, card, "triage", triage_prompt(card, rt.brief, nh, st["decisions"]), wt, "triage",
                       schema=TRIAGE_SCHEMA)  # fmt: skip
        res = await _coder(rt, card, "triage", req)
        saved = res.output if res.ok and res.output else {"action": "escalate"}
        rt.store.save_checkpoint(f"{ikey}:triage", saved)
    t = dict(saved)
    if t.get("action") == "escalate" and t.get("category") not in ESCALATION_CATEGORIES:
        msg = t.get("message") or {}
        if msg.get("recommended") and t.get("category"):
            # not a category the owner is asked about: decide it, and record the assumption
            t = {"action": "assume", "answer": msg["recommended"]}
        else:
            t["category"] = "requirements"
    if t.get("action") == "assume":
        rt.store.note(card.milestone, "assumption", {"question": nh.get("question"), "answer": t.get("answer")},
                      card=card.id)  # fmt: skip
    if t.get("action") in ("answer", "assume"):
        st["decisions"].append(f"{nh.get('question')}: {t.get('answer')}")
    return t


def _guard(rt: Runtime, card: Card, wt: Path) -> Tuple[List[str], List[str], bool]:
    """(blockers, warnings, committable): refused changes are reverted here,
    before any commit, so they never reach the branch's history."""
    _clean_junk(rt, wt)
    dirty = rt.git.dirty(wt)
    acc = card.detail.get("acceptance") or {}
    protected = [acc["dir"]] if acc.get("dir") else []
    protected.append("docs/brd/board/*")  # the engine writes the board (plan D1); a card never does
    findings = guard(wt, dirty, paths=card.paths, shared=rt.shared, protected=protected, private=rt.private)
    markers = conflict_markers(wt, dirty)
    blockers = [f.message for f in findings if f.severity == "blocker"]
    warnings = [f.message for f in findings if f.severity == "warning"]
    refused = sorted({f.path for f in findings if f.severity == "blocker"} - set(markers))
    if refused:
        rt.git.revert(wt, refused)
    blockers += [f"{p} still has conflict markers; resolve them" for p in markers]
    # a protected file changed in a commit the engine did not make: put it back
    for path, digest in (acc.get("files") or {}).items():
        f = wt / path
        if not f.is_file() or sha256_file(f) != digest:
            rt.git.git("checkout", "-q", acc["commit"], "--", path, cwd=wt)
            blockers.append(f"{path} is a protected acceptance file; restored")
    return blockers, warnings, not markers


def _card_changed(rt: Runtime, card: Card, wt: Path) -> List[str]:
    acc = (card.detail.get("acceptance") or {}).get("dir")
    changed = rt.git.changed(wt, rt.git.integration_branch(card.milestone))
    return [p for p in changed if not (acc and p.startswith(acc + "/"))]


async def _checks(rt: Runtime, card: Card, wt: Path) -> Tuple[List[CheckResult], List[str], str, str]:
    """(results, new failures, failure tail, signature) against the card's baseline."""
    checks = list(rt.card_checks)
    acc = card.detail.get("acceptance") or {}
    if acc.get("files"):
        checks.append(rt.test_check("acceptance", rt.acceptance_cmd.format(dir=acc["dir"])))
    async with child("checks", inputs={"checks": [c.name for c in checks]}, op_type="checks") as step:
        results = await run_checks(checks, wt, rt.env)
        cmp = compare(_results(card.detail.get("baseline") or []), results)
        step.outputs = {"new": cmp.new, "old": cmp.old, "fixed": cmp.fixed}
    # what the merge reuses when the integration branch has not moved under the card (GitIntegrator)
    own = {c.name for c in rt.card_checks}
    rt.set_detail(card.milestone, card.id, checked={"sha": rt.git.head(wt), "ok": not cmp.new,
                                                    "results": [r.to_dict() for r in results if r.name in own]})
    failing = [r for r in results if not r.ok and any(n == r.name or n.startswith(r.name + ":") for n in cmp.new)]
    tail = "\n\n".join(f"$ {r.name}\n{r.tail[-3000:]}" for r in failing)
    sig = hashlib.sha256("\n".join(sorted(r.signature for r in failing)).encode()).hexdigest()[:16] if failing else ""
    return results, cmp.new, tail, sig


async def _review(
    rt: Runtime, card: Card, wt: Path, st: Dict[str, Any], ikey: str, results: List[CheckResult]
) -> Tuple[Optional[CoderResult], List[Dict[str, Any]], List[Dict[str, Any]]]:
    """A fresh read-only reviewer; (failed coder result or None, blocking, other)."""
    base = rt.git.integration_branch(card.milestone)
    diff = rt.git.diff(wt, base)
    changed = rt.git.changed(wt, base)
    saved = rt.store.checkpoint(f"{ikey}:review")
    if saved is None:
        summary = "\n".join(f"- {r.name}: {'pass' if r.ok else 'fail (failing before this card too)'}"
                            for r in results)  # fmt: skip
        acc = card.detail.get("acceptance") or {}
        prompt = reviewer_prompt(card, diff, summary, st["open"], acc.get("dir") if acc.get("files") else None)
        req = _request(rt, card, "reviewer", prompt, wt, f"r{st['round']}-review", schema=REVIEWER_SCHEMA)
        res = await _coder(rt, card, "review", req)
        if not res.ok and res.status not in _STOP:  # one more try: the reviewer's failure is not the card's
            res = await _coder(rt, card, "review", req)
        if not res.ok:
            return res, [], []
        saved = {"findings": list((res.output or {}).get("findings") or [])}
        rt.store.save_checkpoint(f"{ikey}:review", saved)
    previous = [f["fingerprint"] for f in st["open"]]
    blocking, other = check_findings(saved["findings"], wt, changed, previous)
    blocking = [f for f in blocking if f["fingerprint"] not in st["waived"]]
    return None, blocking, other


async def _arbitrate(
    rt: Runtime, card: Card, wt: Path, st: Dict[str, Any], ikey: str, repeated: List[Dict[str, Any]]
) -> Dict[str, Dict[str, Any]]:
    """A third session reads each finding that came back after a fix: does it stand?"""
    saved = rt.store.checkpoint(f"{ikey}:arbiter")
    if saved is None:
        diff = rt.git.diff(wt, rt.git.integration_branch(card.milestone))
        req = _request(rt, card, "arbiter", arbiter_prompt(card, diff, repeated, st["last_summary"]), wt,
                       f"r{st['round']}-arbiter", schema=ARBITER_SCHEMA)  # fmt: skip
        res = await _coder(rt, card, "arbiter", req)
        verdicts = (res.output or {}).get("verdicts") if res.ok else None
        if not verdicts:  # no verdict is not a waiver: the findings stand
            verdicts = [{"id": f["fingerprint"], "stands": True, "reason": "no arbitration"} for f in repeated]
        saved = {"verdicts": verdicts}
        rt.store.save_checkpoint(f"{ikey}:arbiter", saved)
    return {v["id"]: v for v in saved["verdicts"] if isinstance(v, dict) and "id" in v}


async def _heldout(rt: Runtime, card: Card, wt: Path) -> Tuple[Optional[bool], List[str]]:
    held = card.detail.get("heldout") or {}
    if not held.get("files"):
        return None, []
    check, inside = rt.heldout_check("heldout", Path(held["dir"]), wt)
    async with child("heldout", inputs={"files": len(held["files"])}, op_type="checks") as step:
        (res,) = await run_checks([check], wt, rt.env)
        names = [] if res.ok else heldout_failures(res.tail, Path(inside))
        if not res.ok and not names:
            names = [f"the held-out run failed (exit {res.exit_code}) without naming a test"]
        step.outputs = {"ok": res.ok, "failing": names}  # names and messages only, here too
    return res.ok, names


def _question(card: Card, st: Dict[str, Any], d: Decision, kind: str) -> Dict[str, Any]:
    q = dict(d.question or {})
    q.update({
        "kind": kind,
        "milestone": card.milestone,
        "card": card.id,
        "title": card.title,
        "round": st["round"],
        "reason": d.reason,
    })  # fmt: skip
    q["fingerprint"] = question_fingerprint(card.id, q.get("category", ""), f"{q.get('problem', '')}|{kind}")
    # the implementer may never change either set, so a wrong test has no other way out: an acceptance
    # one (tipcalc m2/C3), or a held-out one no implementation can pass (m2/C6 compared engine states)
    held = bool(st.get("heldout_failing")) and bool((card.detail.get("heldout") or {}).get("files"))
    if kind in ("engine", "human") and (held or (card.detail.get("acceptance") or {}).get("files")):
        opts = list(q.get("options") or [])
        if not any(o.get("key") == "rewrite_tests" for o in opts):
            opts.append({"key": "rewrite_tests", "label": "Rewrite its held-out tests (they are wrong)",
                         "description": "The examiner corrects them, told which failed; the code is checked "
                                        "again, with fresh rounds"} if held else
                        {"key": "rewrite_tests", "label": "Rewrite its acceptance tests (they are wrong)",
                         "description": "The test author writes them again, told what was found; the card gets "
                                        "fresh rounds"})  # fmt: skip
            q["options"] = opts
            q["rewrite"] = "heldout" if held else "acceptance"
    if held and not q.get("evidence"):  # the owner sees what the implementer cannot: which held-out tests fail
        q["evidence"] = "The held-out tests failing:\n" + "\n".join(f"- {x}" for x in st["heldout_failing"])[:1500]
    return q


@op
async def round_(key: str, mid: str, cid: str, state: dict, answers: list) -> dict:
    rt = get_runtime(key)
    st = dict(state)
    if st.get("abort"):
        a = st["abort"]
        return _end(st, a["status"], a["reason"], a.get("not_before"))
    card = rt.card(mid, cid)
    if len(answers) > st["answered"]:
        st["answered"] = len(answers)
        ended = _apply_answer(rt, card, st, answers[-1])
        if ended is not None:
            return ended
    ikey = run_context().idempotency_key
    wt = rt.git.worktree_path(mid, cid)
    wrong = st.pop("rewrite_tests", None)
    which = st.pop("rewrite_which", "acceptance")
    if wrong and which != "heldout":
        acc, res = await _author(rt, rt.card(mid, cid), wt, wrong)
        if acc is None:
            a = _stopped(res, "the test author")
            return _end(st, a["status"], a["reason"], a.get("not_before"))
        rt.set_detail(mid, cid, acceptance=acc)
        rt.store.note(mid, "acceptance.rewritten", {"files": sorted(acc["files"]), "why": wrong[:500]}, card=cid)
    # the owner's reason is about the card's tests: held-out ones written from the same misreading fail the same
    # way (tipcalc rebuild/C3: both sets expected a direct op call to raise), so the examiner checks its own too
    if wrong and (which == "heldout" or (rt.card(mid, cid).detail.get("heldout") or {}).get("files")):
        held, res = await _examine(rt, rt.card(mid, cid), wt, wrong)
        if held is None:
            a = _stopped(res, "the examiner")
            return _end(st, a["status"], a["reason"], a.get("not_before"))
        rt.set_detail(mid, cid, heldout=held)
        rt.store.note(mid, "heldout.rewritten", {"files": held["files"], "why": wrong[:500]}, card=cid)
    skip = st.pop("skip_implement", False)
    st["skip_implement"] = False
    res: Optional[CoderResult] = None
    worked = False  # this round is someone's work: the implementer's, or a taker's submit
    taken = rt.card(mid, cid).detail.get("taken")
    if not skip and taken:
        # a person or an assistant works this card (plan D7): the run parks until they submit,
        # and their submission is a round like the engine's, through the same gates
        if not st.pop("submitted", False):
            rt.stage(mid, cid, Stage.IMPLEMENT)
            q = {
                "kind": "taken",
                "taker": taken.get("actor"),
                "worktree": str(wt),
                "round": st["round"],
                "feedback": st["feedback"],
                "reason": f"taken by {taken.get('actor')}: waits for their submit",
            }
            st["pending"] = q
            return {"next": "ask", "question": q, "state": st, "outcome": None}
        st["round"] += 1
        skip = worked = True
    if not skip:
        st["round"] += 1
        rt.stage(mid, cid, Stage.IMPLEMENT)
        res = await _implement(rt, card, wt, st, ikey, st["round"])
        worked = True
        st["mode"] = "resume"
        st["feedback"] = ""
        st["last_summary"] = (res.output or {}).get("summary") or res.text[-1500:]
    n = st["round"]
    facts = RoundFacts(round=n, max_rounds=st["cap"], kind=card.kind, card_max_usd=st["card_budget"])
    record: Dict[str, Any] = {"round": n}
    if res is not None:
        facts.coder_status, facts.coder_error, facts.resets_at = res.status, res.error, res.resets_at
        record.update(coder=res.status, session=res.session, cost=res.cost_usd)

    if res is None or res.status not in _STOP:
        card = rt.card(mid, cid)
        async with child("guard", inputs={}, op_type="git") as step:
            blockers, warnings, committable = _guard(rt, card, wt)
            sha = rt.git.commit(wt, f"{mid}/{cid} round {n}: {st['last_summary'][:60]}") if committable else None
            step.outputs = {"blockers": blockers, "warnings": warnings, "commit": sha}
        facts.guard_blockers = blockers
        facts.empty_diff = worked and not _card_changed(rt, card, wt)
        facts.empty_retries = st["empty"]
        record.update(commit=sha, blockers=blockers, warnings=warnings)
        nh = (res.output or {}).get("needs_human") if res is not None else None
        if nh:
            facts.needs_human = nh
            facts.triage = await _triage(rt, card, wt, st, ikey, nh)
        elif not blockers and not facts.empty_diff:
            rt.stage(mid, cid, Stage.QA)
            results, new, tail, sig = await _checks(rt, card, wt)
            facts.checks_ok, facts.new_failures, facts.failure_tail = not new, new, tail
            st["same"] = 0 if not new else (st["same"] + 1 if sig == st["last_sig"] else 1)
            st["last_sig"] = sig
            facts.same_failure, facts.revised = st["same"], st["revised"]
            record.update(checks_ok=not new, new_failures=new)
            if not new:
                rt.stage(mid, cid, Stage.REVIEW)
                failed, blocking, other = await _review(rt, card, wt, st, ikey, results)
                if failed is not None:
                    facts.coder_status = failed.status if failed.status in _STOP else "infra"
                    facts.coder_error = f"the reviewer failed ({failed.status}): {failed.error}"
                    facts.resets_at = failed.resets_at
                else:
                    repeated = [f for f in blocking if f["fingerprint"] in st["seen"]
                                and f["fingerprint"] not in st["owner_fix"]]  # fmt: skip
                    upheld: List[Dict[str, Any]] = []
                    if repeated:
                        verdicts = await _arbitrate(rt, card, wt, st, ikey, repeated)
                        for f in repeated:
                            v = verdicts.get(f["fingerprint"], {"stands": True})
                            st["arbitrated"][f["fingerprint"]] = v
                            if v.get("stands"):
                                upheld.append(f)
                            else:
                                st["waived"].append(f["fingerprint"])
                        blocking = [f for f in blocking if f["fingerprint"] not in st["waived"]]
                    facts.upheld = upheld
                    facts.findings = [f for f in blocking if f not in upheld]
                    st["seen"] = sorted(set(st["seen"]) | {f["fingerprint"] for f in blocking})
                    st["open"] = blocking
                    record.update(findings=len(blocking), unverified=len([f for f in other if "unverified" in f]),
                                  upheld=len(upheld))  # fmt: skip
                    if not blocking:
                        rt.stage(mid, cid, Stage.QA)
                        facts.heldout_ok, facts.heldout_failures = await _heldout(rt, card, wt)
                        record.update(heldout=facts.heldout_ok)
    if facts.heldout_ok is not None:  # a round the held-out tests did not run in neither breaks nor extends a run
        same = facts.heldout_ok is False and sorted(facts.heldout_failures) == st.get("heldout_last")
        st["heldout_same"] = st.get("heldout_same", 0) + 1 if same else (1 if facts.heldout_ok is False else 0)
        st["heldout_last"] = sorted(facts.heldout_failures) if facts.heldout_ok is False else None
        # what still fails: a round they did not run in (a question, a failing check) keeps the last run's names,
        # so its question offers to rewrite the held-out tests (tipcalc m3/C5 offered the acceptance tests)
        st["heldout_failing"] = facts.heldout_failures if facts.heldout_ok is False else []
    facts.heldout_same = st.get("heldout_same", 0)

    facts.cost_usd = rt.card(mid, cid).cost_usd
    async with child("decide", inputs={"round": n}, op_type="decide") as step:
        d = decide(facts)
        step.outputs = {"next": d.next, "reason": d.reason}
    record.update(next=d.next, reason=d.reason)
    rt.store.note(mid, "round", record, card=cid)
    LOGGER.info(f"card {cid} round {n}: {d.next} ({d.reason})")
    if d.counts_empty:
        st["empty"] += 1

    if d.next == "verified":
        return _end(st, "verified", d.reason)
    if d.next in ("failed", "requeue"):
        return _end(st, d.next, d.reason, d.not_before)
    if d.next == "revise":
        st["revised"], st["same"], st["last_sig"] = True, 0, ""
        st["mode"] = "fresh"
        rt.set_detail(mid, cid, session=None)
    if d.next in ("fix", "revise"):
        st["feedback"] = d.feedback
        return {"next": "fix", "question": None, "state": st, "outcome": None}
    # ask
    # the engine's own limits are "engine" even on a round the implementer asked in: their "keep" extends the
    # limit (tipcalc m3/C5 hit max rounds on a question round; "keep" read as an answer left the cap at 6)
    mine = (d.question or {}).get("engine")
    kind = ("auth" if facts.coder_status == "auth" else "finding" if facts.upheld
            else "engine" if mine else "human" if facts.needs_human else "engine")  # fmt: skip
    q = _question(card, st, d, kind)
    if q["fingerprint"] in st["asked"]:  # asked and answered before: apply that answer again
        st["pending"] = q
        ended = _apply_answer(rt, card, st, st["asked"][q["fingerprint"]])
        if ended is not None:
            return ended
        return {"next": "fix", "question": None, "state": st, "outcome": None}
    st["pending"] = q
    st["feedback"] = d.feedback
    return {"next": "ask", "question": q, "state": st, "outcome": None}


@op
def keep(response: Any) -> dict:
    return {"answers": [response]}
