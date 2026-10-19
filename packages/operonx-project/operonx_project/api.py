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
                       "from_board": "m1", "needs": ["operonx-studio#m3"]}  # a new milestone, queued to start
    {"op": "request",  "text": "…the owner's words…", "title": "…", "mode": "w1", "max_usd": 5}
                                                                            # a new milestone from the words (plan D14)
    {"op": "ship" | "merge", "milestone": "m1"}  # push + PR (gh) or the GitLab URL; merge where granted (D15)
    {"op": "settings"}                                                      # the models each role runs on
    {"op": "settings", "model": "sonnet", "models": {"reviewer": "opus"}}   # set them ("" clears one)

The answer: ``{"ok": bool, "status": …, "message": …}`` (``seq``: the queued
command; ``current``: the open questions, for a stale answer).
"""

from __future__ import annotations

from typing import Any, Dict

from operonx.core.loggings import LOGGER

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
    "request",
    "settings",
    "ship",
    "merge",
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
    try:
        ms = create_milestone(project, title, goal=goal, mode=mode, rows=rows, policy=policy, seed=seed,
                              needs=[str(n) for n in req.get("needs") or []])  # fmt: skip
    except ValueError as exc:
        return _no("invalid", str(exc))
    seq = project.store.enqueue(ms.id, "start", None, actor=actor)
    return {"ok": True, "status": "queued", "milestone": ms.id, "seq": seq,
            "message": f"{ms.id} created ({len(rows)} rows); the daemon starts it"}  # fmt: skip


def _request(project: Any, req: Dict[str, Any], actor: str) -> Dict[str, Any]:
    """The owner's words become rows (plan D14): the words are a source of the
    BRD (``sources/owner_requests/``), extracted into requirements in the
    milestone's requirements stage; the milestone's scope is those rows, and it
    is planned and waits for the owner's approval like any other."""
    import re
    import time

    from operonx_project.brd.records import matrix_status
    from operonx_project.model import Policy
    from operonx_project.project import BRD_DIR, create_milestone

    text = str(req.get("text") or "").strip()
    if not text:
        return _no("invalid", "a request needs its words: text")
    title = str(req.get("title") or "").strip() or text.splitlines()[0][:70].rstrip(" .")
    mode = str(req.get("mode") or "w1")
    if mode not in ("w1", "w2"):
        return _no("invalid", "mode is w1 or w2")
    policy = Policy.w1() if mode == "w1" else Policy.w2()
    if req.get("max_usd") is not None:
        policy.budget.max_usd = float(req["max_usd"])
    ids = list(matrix_status(project.repo / BRD_DIR))
    heads = [i.split("-REQ-", 1)[0] for i in ids if "-REQ-" in i]
    prefix = max(set(heads), key=heads.count) if heads else ""  # the BRD's own prefix: TIP-REQ-… stays TIP
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:40] or "request"
    name = f"{time.strftime('%Y-%m-%d')}-{slug}.md"
    asked = project.state / "requests" / name
    asked.parent.mkdir(parents=True, exist_ok=True)
    asked.write_text(f"# {title}\n\nThe owner's request, in their words ({actor}, {time.strftime('%Y-%m-%d %H:%M')}):"
                     f"\n\n{text}\n")  # fmt: skip
    src = {"path": str(asked), "role": "approved", "title": f"Owner request: {title}",
           "into": f"sources/owner_requests/{name}", "scope": True}  # fmt: skip
    goal = f"The owner's request: {text}"
    try:
        ms = create_milestone(project, title, goal=goal, mode=mode, sources=[src], policy=policy, prefix=prefix,
                              needs=[str(n) for n in req.get("needs") or []])  # fmt: skip
    except ValueError as exc:
        return _no("invalid", str(exc))
    seq = project.store.enqueue(ms.id, "start", None, actor=actor)
    return {"ok": True, "status": "queued", "milestone": ms.id, "seq": seq, "source": src["into"],
            "message": f"{ms.id} created from the request; its requirements are extracted, then it is planned "
                       "and waits for your approval"}  # fmt: skip


def _settings(project: Any, req: Dict[str, Any], actor: str) -> Dict[str, Any]:
    """Read, or change, the models the project's sessions run on (``settings.toml``).
    A change applies to the next session; nothing running is stopped."""
    from operonx_project.project import load_settings, save_settings, valid_model
    from operonx_project.runtime import DEFAULT_MODELS

    try:
        settings = load_settings(project.state)
    except ValueError as exc:
        return _no("invalid", f"settings.toml does not parse: {exc}")
    changes = {k: req[k] for k in ("model", "models") if k in req}
    if changes:
        per = dict(settings.get("models") or {})
        wanted = changes.get("models") or {}
        if not isinstance(wanted, dict):
            return _no("invalid", "models is an object: {role: model}")
        unknown = [r for r in wanted if r not in DEFAULT_MODELS]
        bad = [str(m) for m in [changes.get("model"), *wanted.values()] if m and not valid_model(str(m))]
        if unknown or bad:
            return _no("invalid", (f"no role {', '.join(unknown)} (roles: {', '.join(DEFAULT_MODELS)}). " if unknown
                                   else "") + (f"not a model: {', '.join(bad)} (sonnet, opus, haiku, fable or a "
                                               "claude-… id)" if bad else ""))  # fmt: skip
        if "model" in changes:
            if changes["model"]:
                settings["model"] = str(changes["model"])
            else:
                settings.pop("model", None)
        for role, m in wanted.items():
            if m:
                per[role] = str(m)
            else:
                per.pop(role, None)
        settings["models"] = per
        save_settings(project.state, settings)
        LOGGER.info("operonx-project: %s set the models: %s %s", actor, settings.get("model"), per)
    per = settings.get("models") or {}
    effective = {r: per.get(r) or settings.get("model") or d for r, d in DEFAULT_MODELS.items()}
    return {"ok": True, "status": "done" if changes else "read",
            "message": "the next sessions run on: " + ", ".join(sorted(set(effective.values()))),
            "model": settings.get("model"), "models": per, "effective": effective}  # fmt: skip


def act(project: Any, req: Dict[str, Any], actor: str = "studio") -> Dict[str, Any]:
    op, mid = req.get("op"), req.get("milestone")
    if op not in OPS:
        return _no("invalid", f"unknown op {op!r}; one of {', '.join(OPS)}")
    store = project.store
    if op == "start":
        return _start(project, req, actor)
    if op == "request":
        return _request(project, req, actor)
    if op == "settings":
        return _settings(project, req, actor)
    try:
        ms = store.get_milestone(str(mid))
    except KeyError:
        return _no("unknown", f"no milestone {mid!r}")
    if op in ("ship", "merge"):
        from operonx_project import ship as shipping

        try:
            got = (shipping.ship if op == "ship" else shipping.merge)(project, ms.id, actor=actor)
        except shipping.Refused as exc:
            return _no("refused", str(exc))
        return {"ok": True, "status": "done", **got}
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
