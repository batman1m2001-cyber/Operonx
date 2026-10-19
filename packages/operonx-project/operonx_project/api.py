"""``operonx-project api <json>``: the owner's actions for another program (the studio).

One request, one JSON answer on stdout. The checks are the CLI's and
Telegram's: an answer goes through :meth:`Interactions.submit_answer` (a
second or a stale one is refused, and says what is current), and every
other action is queued for the daemon, which refuses what does not apply.

Requests (``op`` and its fields)::

    {"op": "answer",   "milestone": "m1", "ref": "Q-03" | "C4" | <id>, "answer": "b" | "words" | {"choice", "text"}}
    {"op": "approve" | "pause" | "unpause" | "cancel", "milestone": "m1"}
    {"op": "replan",   "milestone": "m1", "notes": "…"}
    {"op": "retry",    "milestone": "m1", "usd": 20 | "hours": 2}
    {"op": "prioritize", "milestone": "m1", "card": "C5", "priority": -1}   # lower runs first
    {"op": "take",     "milestone": "m1", "card": "C5"}                    # the actor works it (plan D7)
    {"op": "submit",   "milestone": "m1", "card": "C5", "summary": "…"}    # checks, review, held-out tests
    {"op": "release",  "milestone": "m1", "card": "C5"}                    # back to the engine's implementer
    {"op": "card",     "milestone": "m1", "card": "C5"}                    # what a taker needs: worktree, criteria
    {"op": "start",    "title": "…", "rows": ["ID", …], "goal": "…", "mode": "w2", "max_usd": 10,
                       "from_board": "m1"}                                  # a new milestone, queued to start

The answer: ``{"ok": bool, "status": …, "message": …}`` (``seq``: the queued
command; ``current``: the open questions, for a stale answer).
"""

from __future__ import annotations

from typing import Any, Dict

from operonx_project.interactions import Interactions
from operonx_project.model import CardState

__all__ = ["OPS", "act", "taker_brief"]


def taker_brief(project: Any, mid: str, cid: str) -> Dict[str, Any]:
    """What the person (or assistant) working card *cid* needs: where, what, and
    what the last round found."""
    card = project.store.get_card(mid, cid)
    q = card.detail.get("question") or {}
    acc = card.detail.get("acceptance") or {}
    waiting = card.state == CardState.WAITING_HUMAN and q.get("kind") == "taken"
    return {"card": card.id, "title": card.title, "state": card.state.value,
            "stage": card.stage.value if card.stage else None, "taken": card.detail.get("taken"),
            "waiting_for_submit": waiting, "worktree": str(project.git.worktree_path(mid, cid)),
            "branch": project.git.card_branch(mid, cid), "intent": card.intent, "rows": card.rows,
            "criteria": card.criteria, "paths": card.paths, "acceptance_tests": acc.get("dir"),
            "round": q.get("round"), "feedback": q.get("feedback") if waiting else "",
            "reason": card.reason}  # fmt: skip


OPS = (
    "answer",
    "approve",
    "pause",
    "unpause",
    "cancel",
    "replan",
    "retry",
    "prioritize",
    "take",
    "submit",
    "release",
    "card",
    "start",
)


def _no(status: str, message: str, **extra: Any) -> Dict[str, Any]:
    return {"ok": False, "status": status, "message": message, **extra}


def _start(project: Any, req: Dict[str, Any], actor: str) -> Dict[str, Any]:
    """A new milestone for rows of the checklist (or a goal), queued to start."""
    from operonx_project.brd.records import matrix_status
    from operonx_project.model import Policy
    from operonx_project.project import BRD_DIR, board_seed, create_milestone

    title = str(req.get("title") or "").strip()
    rows = [str(r) for r in req.get("rows") or []]
    goal = str(req.get("goal") or "")
    if not title:
        return _no("invalid", "a milestone needs a title")
    if not rows and not goal and not req.get("from_board"):
        return _no("invalid", "say what to build: rows of the checklist, a goal, or from_board")
    known = matrix_status(project.repo / BRD_DIR)
    unknown = [r for r in rows if r not in known]
    if unknown:
        return _no("invalid", f"not in the checklist: {', '.join(unknown)}")
    mode = str(req.get("mode") or "w2")
    if mode not in ("w1", "w2"):
        return _no("invalid", "mode is w1 or w2")
    policy = Policy.w1() if mode == "w1" else Policy.w2()
    if req.get("max_usd") is not None:
        policy.budget.max_usd = float(req["max_usd"])
    seed = None
    if req.get("from_board"):
        try:
            seed = board_seed(project.repo / BRD_DIR, str(req["from_board"]))
        except KeyError as exc:
            return _no("unknown", str(exc.args[0]))
    ms = create_milestone(project, title, goal=goal, mode=mode, rows=rows, policy=policy, seed=seed)
    seq = project.store.enqueue(ms.id, "start", None, actor=actor)
    return {"ok": True, "status": "queued", "milestone": ms.id, "seq": seq,
            "message": f"{ms.id} created ({len(rows)} rows); the daemon starts it"}  # fmt: skip


def act(project: Any, req: Dict[str, Any], actor: str = "studio") -> Dict[str, Any]:
    op, mid = req.get("op"), req.get("milestone")
    if op not in OPS:
        return _no("invalid", f"unknown op {op!r}; one of {', '.join(OPS)}")
    store = project.store
    if op == "start":
        return _start(project, req, actor)
    try:
        ms = store.get_milestone(str(mid))
    except KeyError:
        return _no("unknown", f"no milestone {mid!r}")
    if op == "answer":
        got = Interactions(store).submit_answer(str(req.get("ref") or ""), req.get("answer"), actor=actor,
                                                via="studio", mid=ms.id)  # fmt: skip
        out = {"ok": got.ok, "status": got.status, "message": got.message}
        if got.current:
            out["current"] = [{"qid": i["qid"], "id": i["id"], "card": i.get("card"), "kind": i["kind"]}
                              for i in got.current]  # fmt: skip
        if got.interaction and got.interaction.get("command"):
            out["seq"] = got.interaction["command"]
        return out
    if op == "prioritize":
        try:
            card = store.get_card(ms.id, str(req.get("card")))
            priority = int(req["priority"])
        except (KeyError, TypeError, ValueError):
            return _no("invalid", "prioritize needs an existing card and an integer priority")
        if card.state not in (CardState.PENDING, CardState.READY, CardState.BLOCKED):
            return _no("refused", f"{card.id} is {card.state.value}: only a card not started can move")
        store.update_card(ms.id, card.id, actor=actor, priority=priority)
        return {"ok": True, "status": "done", "message": f"{card.id}: priority {priority}"}
    if op == "card":
        try:
            return {"ok": True, "status": "done", **taker_brief(project, ms.id, str(req.get("card")))}
        except KeyError:
            return _no("unknown", f"no card {req.get('card')!r} in {ms.id}")
    payload: Dict[str, Any] = {}
    if op in ("take", "submit", "release"):
        try:
            store.get_card(ms.id, str(req.get("card")))
        except KeyError:
            return _no("unknown", f"no card {req.get('card')!r} in {ms.id}")
        if store.lease_holder() is None:
            return _no("refused", "no daemon drives this project: start one (operonx-project daemon, or Run on "
                                  "the studio's Board)")  # fmt: skip
        payload = {"actor": actor, "summary": str(req.get("summary") or "")}
        seq = store.enqueue(ms.id, op, payload, card=str(req["card"]), actor=actor)
        return {"ok": True, "status": "queued", "message": f"{op} {req['card']} queued for {ms.id}", "seq": seq}
    if op == "replan":
        if not str(req.get("notes") or "").strip():
            return _no("invalid", "a replan needs notes: what to change")
        payload = {"notes": str(req["notes"])}
    if op == "retry":
        payload = {k: float(req[k]) for k in ("usd", "hours") if req.get(k) is not None}
    if op == "approve":
        # through the plan's question when there is one, so Telegram's message closes too
        plan = [i for i in Interactions(store).open(ms.id) if i["kind"] == "approval"]
        if plan:
            got = Interactions(store).submit_answer(plan[0]["id"], {"choice": "approve", "text": ""}, actor=actor,
                                                    via="studio")  # fmt: skip
            return {"ok": got.ok, "status": got.status, "message": got.message,
                    "seq": (got.interaction or {}).get("command")}  # fmt: skip
    seq = store.enqueue(ms.id, op, payload or None, actor=actor)
    return {"ok": True, "status": "queued", "message": f"{op} queued for {ms.id}; the daemon applies it", "seq": seq}
