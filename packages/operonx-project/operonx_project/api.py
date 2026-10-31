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
    {"op": "edit",     "milestone": "m1", "card": "C5", "paths": […], "criteria": […], "title": "…"}
                                                                            # or "intent"; a card not running
    {"op": "take",     "milestone": "m1", "card": "C5"}                    # the actor works it (plan D7)
    {"op": "submit",   "milestone": "m1", "card": "C5", "summary": "…"}    # checks, review, held-out tests
    {"op": "release",  "milestone": "m1", "card": "C5"}                    # back to the engine's implementer
    {"op": "card",     "milestone": "m1", "card": "C5"}                    # what a taker needs: worktree, criteria
    {"op": "start",    "title": "…", "rows": ["ID", …], "goal": "…", "mode": "w2", "max_usd": 10,
                       "from_board": "m1", "needs": ["operonx-studio#m3"]}  # a new milestone, queued to start
    {"op": "request",  "text": "…the owner's words…", "title": "…", "mode": "w1", "max_usd": 5}
                                                                            # a new milestone from the words (plan D14)
    {"op": "plan",     "path": "docs/X_PLAN.md", "text": "…"}  # a plan doc on the board: the plan a milestone,
                                              # each phase a card, as the doc says; ``text``: the doc where it is
                                              # worked (a branch's worktree), else the checkout's; committed
    {"op": "ship" | "merge", "milestone": "m1"}  # push + PR (gh) or the GitLab URL; merge where granted (D15)
    {"op": "settings"}                                                      # the models each role runs on
    {"op": "settings", "model": "sonnet", "models": {"reviewer": "opus"}}   # set them ("" clears one)
    {"op": "practices"}                       # how the project is built: the file, in words, and the arch check
    {"op": "practices", "preview": "operonx"} # what a preset's rules would find on the checkout; nothing written
    {"op": "practices", "preset": "operonx"}  # write a preset (``force``: over an existing file)
    {"op": "practices", "toml": "…"}          # validate and write the file
                                              # a write is committed on the checkout's branch: that file only

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
    "edit",
    "take",
    "submit",
    "release",
    "card",
    "start",
    "request",
    "settings",
    "practices",
    "plan",
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


def _practices(project: Any, req: Dict[str, Any], actor: str) -> Dict[str, Any]:
    """Read the project's practices, with the architecture check run on the checkout; or write them (a preset,
    or TOML the engine validates first), committed on the checkout's branch, that file alone. The next card
    reads them from its own branch, so a milestone already running keeps the practices it started with."""
    from operonx_project.contracts import check
    from operonx_project.practices import FILE, PRESETS, PracticesError, load, parse, render, tomllib

    repo = project.repo
    path = repo / FILE
    wrote = ""
    writing = bool(req.get("preset")) or req.get("toml") is not None
    if req.get("preview") and req["preview"] not in PRESETS:
        return _no("invalid", f"no preset {req['preview']!r}; there are {', '.join(PRESETS)}")
    if writing:
        if req.get("preset"):
            if req["preset"] not in PRESETS:
                return _no("invalid", f"no preset {req['preset']!r}; there are {', '.join(PRESETS)}")
            if path.exists() and not req.get("force"):
                return _no("refused", f"{FILE} exists: read it and send its new text as toml, or force the preset")
            text = PRESETS[req["preset"]]
        else:
            text = str(req["toml"])
        try:
            parse(tomllib.loads(text), source=FILE)
        except (PracticesError, tomllib.TOMLDecodeError) as exc:
            return _no("invalid", f"{FILE} would not be valid: {exc}")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text if text.endswith("\n") else text + "\n", encoding="utf-8")
        wrote = _commit_one(project, FILE, f"practices: {req.get('preset') or 'edited'} ({actor})")
        LOGGER.info("operonx-project: %s wrote %s (%s)", actor, FILE, req.get("preset") or "toml")
    try:
        p = load(repo)
    except PracticesError as exc:
        return {"ok": False, "status": "invalid", "message": str(exc), "file": FILE,
                "text": path.read_text(encoding="utf-8") if path.exists() else ""}  # fmt: skip
    found = check(repo, p) if p.enforced else []
    preview = None
    if req.get("preview"):  # the evidence for a recommendation: what the preset's rules would flag today
        seen = check(repo, parse(tomllib.loads(PRESETS[req["preview"]]), source=f"preset {req['preview']}"))
        preview = {"preset": req["preview"], "violation_count": len(seen),
                   "violations": [_violation(v) for v in seen[:100]]}  # fmt: skip
    summary = render(p) or f"no {FILE}: the defaults (acceptance tests first; no architecture rules)"
    said = [f"wrote {FILE}" + (f" (commit {wrote[:8]})" if wrote else "")] if writing else []
    said.append(f"{len(found)} architecture violation(s) on the checkout" if p.enforced else "no architecture rules")
    return {
        "ok": True,
        "status": "done" if writing else "read",
        "message": "; ".join(said),
        "file": FILE,
        "exists": path.exists(),
        "text": path.read_text(encoding="utf-8") if path.exists() else "",
        "practices": p.to_dict(),
        "summary": summary,
        "presets": list(PRESETS),
        "violations": [_violation(v) for v in found[:100]],
        "violation_count": len(found),
        "preview": preview,
        "commit": wrote or None,
    }  # fmt: skip


def _mirrored(store: Any, ms: Any) -> str:
    """The plan doc a milestone mirrors (op ``plan``, or a board made from plans before it), or ""."""
    import re

    got = (store.ms_get(ms.id, "plan") or {}).get("path")
    m = re.match(r"Plan: \[(docs/[^\]]+\.md)\]", ms.goal or "")
    return str(got or (m.group(1) if m else ""))


_PHASE_CARD = {"done": CardState.DONE, "skipped": CardState.CANCELLED, "in_progress": CardState.RUNNING,
               "open": CardState.READY}  # fmt: skip


def _plan(project: Any, req: Dict[str, Any], actor: str) -> Dict[str, Any]:
    """A plan doc worked outside the engine (a chat, a Claude Code session), on the board: the plan is a
    milestone, each phase a card. The doc is the record: each call makes the milestone say what the doc says
    (a new phase is a new card, a phase's status its card's state), so call it whenever a phase moves. The
    engine never works these cards: the milestone stays paused until the plan is done."""
    from pathlib import Path

    from operonx_project import plans
    from operonx_project.brd.board import BOARD_DIR, board_text, index_text
    from operonx_project.model import Card, Milestone, MilestoneState, Policy
    from operonx_project.project import BRD_DIR

    rel = str(req.get("path") or "").strip().removeprefix("./")
    if not (rel.startswith("docs/") and rel.endswith(".md")) or ".." in Path(rel).parts or rel.startswith(BRD_DIR):
        return _no("invalid", "path is the plan doc, as docs/<NAME>_PLAN.md")
    text = req.get("text")
    if text is None:
        if not (project.repo / rel).is_file():
            return _no("unknown", f"no {rel} in the checkout; send its text from where it is worked")
        text = (project.repo / rel).read_text(encoding="utf-8")
    try:
        data = plans.front_matter(str(text))
        if data is None or "status" not in data:
            return _no("invalid", f"{rel} has no status block (title, status, phases): the board reads it")
        plan = plans.parse(rel, data, str(text))
    except plans.PlanError as exc:
        return _no("invalid", f"{rel}: {exc}")
    store = project.store
    old = next((m for m in store.milestones() if _mirrored(store, m) == rel), None)
    mid = old.id if old else project.next_milestone_id()
    was = {c.id: c for c in store.cards(mid)} if old else {}
    phases = plan.phases or [plans.Phase("", plan.title, "done" if plan.status == "done" else "open")]
    cards = []
    for i, ph in enumerate(phases, 1):
        card = Card(
            id=f"C{i}",
            milestone=mid,
            title=f"{ph.id} {ph.title}".strip(),
            rows=list(plan.rows),
            state=_PHASE_CARD[ph.status],
            reason="skipped" if ph.status == "skipped" else "",
        )
        if card.id in was:
            card.created = was[card.id].created
        cards.append(card)
    done, cur = sum(p.status in ("done", "skipped") for p in phases), plan.current
    if plan.status == "done":
        state, reason = MilestoneState.DONE, f"built outside the engine; recorded in {rel}"
    else:
        state = MilestoneState.PAUSED
        reason = (
            f"parked: {done} of {len(phases)} done — see {rel}"
            if plan.status == "parked"
            else f"worked in chat, not by the engine: {done} of {len(phases)} done"
            + (f", {cur.id} {cur.status.replace('_', ' ')}" if cur and cur.id else "")
            + f" — see {rel}"
        )
    ms = Milestone(
        id=mid,
        project=str(project.repo),
        title=plan.title,
        rows=list(plan.rows),
        state=state,
        goal=f"Plan: [{rel}](../../{rel[len('docs/') :]}) — the detail, the decisions and the gate are there.",
        policy=old.policy if old else Policy.w2(),
        reason=reason,
        cost_usd=old.cost_usd if old else 0.0,
    )
    if old:
        ms.created = old.created
    store.mirror(ms, cards, actor=actor)
    store.ms_put(mid, "plan", {"path": rel})
    folder = project.repo / BRD_DIR / BOARD_DIR
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{mid}.md").write_text(board_text(ms, store.cards(mid)), encoding="utf-8")
    (folder / "README.md").write_text(index_text([(m, store.cards(m.id)) for m in store.milestones()]),
                                      encoding="utf-8")  # fmt: skip
    commit = _commit_one(project, f"{BRD_DIR}/{BOARD_DIR}", f"board: {mid} from {rel} ({actor})")
    said = f"{mid} · {plan.title}: {done} of {len(phases)} done ({'updated' if old else 'new'})"
    return {"ok": True, "status": "updated" if old else "created", "milestone": mid, "commit": commit,
            "message": said}  # fmt: skip


def _violation(v: Any) -> Dict[str, Any]:
    return {"id": v.id, "rule": v.rule, "file": v.file, "line": v.line, "message": v.message}


def _commit_one(project: Any, rel: str, message: str) -> str:
    """Commit *rel* alone on the checkout's branch (anything else staged stays staged); "" outside git or
    when the file did not change."""
    from operonx_project.gitops import _run

    repo = project.repo
    try:
        _run(repo, "add", "--", rel)
        if not _run(repo, "diff", "--cached", "--name-only", "--", rel).strip():
            return ""
        _run(repo, "commit", "-q", "--no-verify", "-m", message, "--", rel, env=project.git._commit_env(repo))
        return project.git.rev("HEAD", cwd=repo)
    except Exception as exc:  # noqa: BLE001 - not a git checkout, or no identity: the file is written either way
        LOGGER.warning(f"operonx-project: {rel} written, not committed: {exc}")
        return ""


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
    if op == "practices":
        return _practices(project, req, actor)
    if op == "plan":
        return _plan(project, req, actor)
    try:
        ms = store.get_milestone(str(mid))
    except KeyError:
        return _no("unknown", f"no milestone {mid!r}")
    mirrored = _mirrored(store, ms)
    if mirrored and op != "card":
        return _no("refused", f"{ms.id} mirrors {mirrored}, which is worked in chat: change the plan doc, "
                              "then send it again (op plan)")  # fmt: skip
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
    if op == "edit":  # the owner widens a card's paths or rewords its criteria (mr-finance m33: only by hand in the db)
        try:
            card = store.get_card(ms.id, str(req.get("card")))
        except KeyError:
            return _no("unknown", f"no card {req.get('card')!r} in {ms.id}")
        if card.state in (CardState.RUNNING, CardState.VERIFIED, CardState.DONE, CardState.CANCELLED):
            return _no("refused", f"{card.id} is {card.state.value}: edit a card that waits, is failed or not started")
        values = {k: req[k] for k in ("paths", "criteria") if isinstance(req.get(k), list)}
        values.update({k: str(req[k]) for k in ("title", "intent") if req.get(k)})
        if not values:
            return _no("invalid", "edit needs paths, criteria, title or intent")
        store.update_card(ms.id, card.id, actor=actor, **values)
        return {"ok": True, "status": "done", "message": f"{card.id}: {', '.join(values)} changed"}
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
