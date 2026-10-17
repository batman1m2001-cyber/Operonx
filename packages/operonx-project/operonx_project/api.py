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

The answer: ``{"ok": bool, "status": …, "message": …}`` (``seq``: the queued
command; ``current``: the open questions, for a stale answer).
"""

from __future__ import annotations

from typing import Any, Dict

from operonx_project.interactions import Interactions
from operonx_project.model import CardState

__all__ = ["OPS", "act"]

OPS = ("answer", "approve", "pause", "unpause", "cancel", "replan", "retry", "prioritize")


def _no(status: str, message: str, **extra: Any) -> Dict[str, Any]:
    return {"ok": False, "status": status, "message": message, **extra}


def act(project: Any, req: Dict[str, Any], actor: str = "studio") -> Dict[str, Any]:
    op, mid = req.get("op"), req.get("milestone")
    if op not in OPS:
        return _no("invalid", f"unknown op {op!r}; one of {', '.join(OPS)}")
    store = project.store
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
    payload: Dict[str, Any] = {}
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
