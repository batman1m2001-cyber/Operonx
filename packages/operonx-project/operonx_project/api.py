"""``operonx-project api <json>``: one action for another program (the studio), one JSON answer on stdout.

Requests (``op`` and its fields)::

    {"op": "status", "changes": 15}           # the project in one document (read-only; answered by the CLI)
    {"op": "plan", "path": "docs/X_PLAN.md", "text": "…"}  # a plan doc on the board: the plan a milestone,
                                              # each phase a card, as the doc says; ``text``: the doc where it is
                                              # worked (a branch's worktree), else the checkout's; committed
                                              # (docs/brd/board/) only when read from the checkout: a branch's
                                              # plan moves the store alone, the checkout's branch is left as it is

The answer: ``{"ok": bool, "status": …, "message": …}``.
"""

from __future__ import annotations

from typing import Any, Dict

from operonx.core.loggings import LOGGER

from operonx_project.model import CardState

__all__ = ["OPS", "act"]

OPS = ("plan", "status")


def _no(status: str, message: str, **extra: Any) -> Dict[str, Any]:
    return {"ok": False, "status": status, "message": message, **extra}


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
    import hashlib
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
    # sha: what the board last read, so a reader can tell a doc changed since (the studio's turn-end sync)
    store.ms_put(mid, "plan", {"path": rel, "sha": hashlib.sha256(str(text).encode("utf-8")).hexdigest()})
    said = f"{mid} · {plan.title}: {done} of {len(phases)} done ({'updated' if old else 'new'})"
    if req.get("text") is not None:
        # the doc lives on a branch: the checkout (often the live one, on main) gets no files and no commit
        return {"ok": True, "status": "updated" if old else "created", "milestone": mid, "commit": None,
                "message": said}  # fmt: skip
    folder = project.repo / BRD_DIR / BOARD_DIR
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{mid}.md").write_text(board_text(ms, store.cards(mid)), encoding="utf-8")
    (folder / "README.md").write_text(index_text([(m, store.cards(m.id)) for m in store.milestones()]),
                                      encoding="utf-8")  # fmt: skip
    commit = _commit_one(project, f"{BRD_DIR}/{BOARD_DIR}", f"board: {mid} from {rel} ({actor})")
    return {"ok": True, "status": "updated" if old else "created", "milestone": mid, "commit": commit,
            "message": said}  # fmt: skip


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
    op = req.get("op")
    if op == "plan":
        return _plan(project, req, actor)
    return _no("invalid", f"unknown op {op!r}; one of {', '.join(OPS)}")
