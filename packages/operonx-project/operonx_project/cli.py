"""``operonx-project`` (and ``operonx project``).

The milestone commands queue a command in the project's store; the daemon
(one per project) runs it and drives the milestone::

    operonx-project new DIR [--name N]                 # a repo with docs/brd/ and an empty board
    operonx-project adopt [--repo .] [--prefix P]       # an existing project: its BRD and its baseline board
    operonx-project start --title T [--goal G] [--mode w1|w2] [--source FILE ...] [--private FILE ...]
                          [--rows ID ...] [--auto-approve] [--max-usd N] [--from-board MID [--board DIR]] [--repo .]
    operonx-project daemon [--repo .] [--once] [--no-sandbox] [--model ROLE=MODEL ...]
    operonx-project status [--repo .] [--json]         # checklist, plans, what needs you, changes (read-only)
    operonx-project milestones [--repo .] [--json]     # every milestone: board, cost, what waits for you
    operonx-project plans [--repo .] [--json]          # the plan docs under docs/ and where each stands
    operonx-project show MID [CARD] [--repo .] [--json]
    operonx-project approve MID | pause MID | unpause MID | cancel MID
    operonx-project answer MID CARD ANSWER [--text WORDS]
    operonx-project replan MID --notes TEXT              # revise a waiting plan; replan a stopped milestone
    operonx-project retry MID [--usd N]                  # a failed milestone, or more budget
    operonx-project take MID CARD [--as WHO] [--wait S]  # work a card yourself: prints its worktree, criteria
    operonx-project submit MID CARD [--summary T] [--wait S]  # its checks, review and held-out tests decide
    operonx-project release MID CARD                     # give it back to the engine's implementer

The living BRD::

    operonx-project brd init [--repo .] [--path docs/brd]
    operonx-project brd check [--brd docs/brd] [--source SRC-01=path ...]
    operonx-project brd register FILE [--role authoritative|approved|proposed] [--private] [--title T]
    operonx-project brd read FILE [--out DIR]         # the sections the extraction sees, as JSON
    operonx-project brd extract SRC-nn FILE --prefix BRD --title T [--name N]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, List, Optional


def _brd_parser(sub) -> None:
    brd = sub.add_parser("brd", help="the living BRD: init, check, register, read, extract")
    cmds = brd.add_subparsers(dest="brd_cmd", required=True)
    p = cmds.add_parser("init", help="write the BRD folder's template (only the files it lacks)")
    p.add_argument("--repo", type=Path, default=Path("."))
    p.add_argument("--path", default="docs/brd")
    p = cmds.add_parser("check", help="check links, ids, the matrix, cited tests, sources")
    p.add_argument("--brd", type=Path, default=Path("docs/brd"))
    p.add_argument("--source", action="append", default=[], metavar="SRC-nn=PATH",
                   help="also check a source file's hash and codes")  # fmt: skip
    p = cmds.add_parser("register", help="add a source to sources/README.md")
    p.add_argument("file", type=Path)
    p.add_argument("--brd", type=Path, default=Path("docs/brd"))
    p.add_argument("--repo", type=Path, default=Path("."))
    p.add_argument("--role", default="authoritative", choices=("authoritative", "approved", "proposed"))
    p.add_argument("--private", action="store_true", help="never committed: registered by path and hash only")
    p.add_argument("--title")
    p = cmds.add_parser("read", help="print a source's sections as JSON")
    p.add_argument("file", type=Path)
    p.add_argument("--out", type=Path, help="a folder for what the source holds (figures, a zip's files)")
    p = cmds.add_parser("extract", help="extract a registered source's requirements (a Claude Code session)")
    p.add_argument("sid")
    p.add_argument("file", type=Path)
    p.add_argument("--brd", type=Path, default=Path("docs/brd"))
    p.add_argument("--prefix", required=True)
    p.add_argument("--title", required=True)
    p.add_argument("--name")
    p.add_argument("--state", type=Path, default=Path(".operonx/project"), help="engine state (outside git)")


def _brd(args) -> int:
    out = sys.stdout
    if args.brd_cmd == "init":
        from operonx_project.brd.folder import init_brd

        for f in init_brd(args.repo, args.path):
            out.write(f"  wrote {f}\n")
        return 0
    if args.brd_cmd == "check":
        from operonx_project.brd.checker import check

        sources = dict(s.split("=", 1) for s in args.source)
        errors, report = check(args.brd, {k: Path(v) for k, v in sources.items()} or None)
        for line in report:
            out.write(f"  {line}\n")
        for e in errors:
            out.write(f"ERROR {e}\n")
        out.write(f"{len(errors)} error(s)\n")
        return 1 if errors else 0
    if args.brd_cmd == "register":
        from operonx_project.brd.register import register_source

        s = register_source(args.brd, args.file, role=args.role, title=args.title, private=args.private,
                            repo=args.repo)  # fmt: skip
        out.write(f"{s.id} {s.title} ({s.role}{', private' if s.private else ''}) sha256 {s.sha256[0]}\n")
        return 0
    if args.brd_cmd == "read":
        from operonx_project.brd.readers import read_source

        dest = args.out or Path(tempfile.mkdtemp(prefix="operonx-brd-read-"))
        src = read_source(args.file, dest)
        out.write(json.dumps({"kind": src.kind, "sha256": src.sha256, "title": src.title,
                              "sections": [s.to_dict() for s in src.all_sections()], "figures": src.figures,
                              "skipped": src.skipped}, ensure_ascii=False, indent=1) + "\n")  # fmt: skip
        return 0
    if args.brd_cmd == "extract":
        from operonx_project.brd.run import extract_source
        from operonx_project.coder.claude import ClaudeCodeCoder
        from operonx_project.flows.brd_flow import BrdRuntime

        state = args.state.resolve()
        # the engine's store stays denied; the figures the session must look at live beside it
        rt = BrdRuntime(
            coder=ClaudeCodeCoder(state_dirs=[str(state)]), data_dir=state.parent / "brd", settings_dir=state
        )
        report = asyncio.run(extract_source(rt, args.brd, args.file, args.sid, args.prefix, args.title, args.name))
        out.write(json.dumps(report, ensure_ascii=False, indent=1) + "\n")
        return 0 if not report.get("problems") else 1
    return 2


# ── milestones ─────────────────────────────────────────────────────────────


def _repo_arg(p) -> None:
    p.add_argument("--repo", type=Path, default=Path("."), help="the project's repo (default: here)")
    p.add_argument("--worktrees", type=Path, default=None, help="default ~/.operonx/worktrees")


def _milestone_parsers(sub) -> None:
    p = sub.add_parser("new", help="a new project: a git repo with docs/brd/ and an empty board")
    p.add_argument("dir", type=Path)
    p.add_argument("--name")
    p = sub.add_parser("practices", help="how the project is built (docs/brd/practices.toml): show, check, --init")
    _repo_arg(p)
    p.add_argument("--init", metavar="PRESET", help="write a starting file: operonx | clean")
    p.add_argument("--force", action="store_true", help="with --init: replace an existing file")
    p.add_argument("--json", action="store_true")
    p = sub.add_parser("arch", help="check the architecture the practices declare: one FAILED line per violation")
    _repo_arg(p)
    p.add_argument("--preset", help="check a preset's rules instead of the file's (what would it flag?)")
    p.add_argument("--json", action="store_true")
    p = sub.add_parser("plans", help="the plan docs under docs/ and where each stands (their status blocks)")
    _repo_arg(p)
    p.add_argument("--json", action="store_true")
    p = sub.add_parser("adopt", help="an existing project: its BRD from its docs and tests, its baseline board")
    _repo_arg(p)
    p.add_argument("--prefix", default="", help="the requirement id prefix (default: from the project name)")
    p.add_argument("--answer", type=Path, default=None, help="a recorded answer (JSON) instead of the session")
    p.add_argument("--max-usd", type=float, default=2.0)
    p.add_argument("--model", default=None)
    p.add_argument("--python", default=None, help="the interpreter the cited tests run with (default: its .venv)")
    p = sub.add_parser("start", help="create a milestone from its sources and goal, and start it")
    _repo_arg(p)
    p.add_argument("--title", required=True)
    p.add_argument("--goal", default="")
    p.add_argument("--mode", choices=("w1", "w2"), default="w2", help="w1: spec → DAG; w2: BRD → board (default)")
    p.add_argument("--source", action="append", default=[], type=Path, help="a requirements source (any form)")
    p.add_argument("--private", action="append", default=[], type=Path, help="a source never committed")
    p.add_argument("--rows", nargs="*", default=[], help="limit the scope to these requirement ids")
    p.add_argument("--prefix", default="", help="the requirement id prefix (default: from the project name)")
    p.add_argument("--constraints", default="")
    p.add_argument("--auto-approve", action="store_true", help="no tap when the plan review passed, no card risky")
    p.add_argument("--max-usd", type=float, default=None)
    p.add_argument("--max-hours", type=float, default=None, help="hours of running (waiting for you does not count)")
    p.add_argument("--max-parallel", type=int, default=None)
    p.add_argument("--no-heldout", action="store_true")
    p.add_argument("--from-board", metavar="MID",
                   help="the plan is milestone MID's cards from a board (docs/brd/board, or --board DIR)")  # fmt: skip
    p.add_argument("--board", type=Path, default=None, help="the BRD (or board) folder --from-board reads")
    p.add_argument("--needs", nargs="*", default=[], metavar="PROJECT#MID",
                   help="milestones to wait for (studio#m3, or m2 here): it starts when they are done")  # fmt: skip
    p = sub.add_parser("request", help="your words become requirements and a milestone that waits for your approval")
    _repo_arg(p)
    p.add_argument("text", help="what you want, in your words (- reads them from stdin)")
    p.add_argument("--title", default="")
    p.add_argument("--mode", choices=("w1", "w2"), default="w1")
    p.add_argument("--max-usd", type=float, default=None)
    for name, text in (
        ("ship", "push a delivered milestone's branch and open its PR (gh), or the GitLab MR link"),
        ("merge", "merge a shipped milestone's PR once its checks pass (settings.toml: merge = true)"),
    ):
        p = sub.add_parser(name, help=text)
        _repo_arg(p)
        p.add_argument("milestone")
    p = sub.add_parser("daemon", help="drive the project's milestones and run the queued commands")
    _repo_arg(p)
    p.add_argument("--once", action="store_true", help="stop when nothing is left to drive")
    p.add_argument("--no-sandbox", action="store_true",
                   help="a trusted repo: the implementer's shell runs with no OS sandbox (no boundary)")  # fmt: skip
    p.add_argument("--model", action="append", default=[], metavar="ROLE=MODEL")
    p.add_argument("--python", help="the interpreter the target's checks run with (default: its .venv, else this)")
    p.add_argument("--poll", type=float, default=0.5)
    p.add_argument("--check-mode", choices=("host", "docker"), default="host",
                   help="the acceptance and held-out tests: on the host, or in docker with no network")  # fmt: skip
    p.add_argument("--check-image", help="docker: an image with the project's interpreter and pytest")
    p.add_argument("--telegram-chat", help="send the questions to this chat (the bot token: OPERONX_TELEGRAM_TOKEN)")
    p.add_argument("--telegram-user", action="append", default=[], help="a Telegram user id allowed to answer")
    p.add_argument("--telegram-api", default="https://api.telegram.org", help=argparse.SUPPRESS)
    p = sub.add_parser("api", help="one action as JSON in, JSON out (for the studio): see operonx_project.api")
    _repo_arg(p)
    p.add_argument("request", help="the request as JSON, or - to read it from stdin")
    p.add_argument("--actor", default="studio", help="who acts (recorded in the events)")
    p = sub.add_parser("pending", help="the questions waiting for you")
    _repo_arg(p)
    p.add_argument("--json", action="store_true")
    p = sub.add_parser("status", help="where the project stands: checklist, plans, what needs you, recent changes")
    _repo_arg(p)
    p.add_argument("--json", action="store_true")
    p = sub.add_parser("milestones", help="every milestone: state, board, cost, what waits for you")
    _repo_arg(p)
    p.add_argument("--json", action="store_true")
    p = sub.add_parser("show", help="one milestone (its cards and last events), or one card")
    _repo_arg(p)
    p.add_argument("milestone")
    p.add_argument("card", nargs="?")
    p.add_argument("--json", action="store_true")
    p.add_argument("--events", type=int, default=15)
    simple = (
        ("approve", "approve the plan waiting for you"),
        ("pause", "pause: running cards end their round"),
        ("unpause", "go on from a pause"),
        ("cancel", "cancel the milestone"),
    )
    for name, what in simple:
        p = sub.add_parser(name, help=what)
        _repo_arg(p)
        p.add_argument("milestone")
    p = sub.add_parser("answer", help="answer a question: a card's, a plan's, a budget's")
    _repo_arg(p)
    p.add_argument("milestone")
    p.add_argument("card", help="the card, or the question's Q-nn")
    p.add_argument("answer", help="an option's key, or words")
    p.add_argument("--text", default="", help="words to go with the option")
    p = sub.add_parser("replan", help="revise a waiting plan, or replan a stopped milestone")
    _repo_arg(p)
    p.add_argument("milestone")
    p.add_argument("--notes", required=True)
    for name, what in (
        ("take", "work a card yourself (an assistant, a person): its worktree and criteria"),
        ("submit", "submit a taken card: checks, review and held-out tests decide"),
        ("release", "give a taken card back to the engine's implementer"),
    ):
        p = sub.add_parser(name, help=what)
        _repo_arg(p)
        p.add_argument("milestone")
        p.add_argument("card")
        p.add_argument("--as", dest="actor", default=None, help="who takes it (default: you, $USER)")
        p.add_argument("--summary", default="", help="what you did (submit)")
        p.add_argument("--wait", type=float, default=600.0, help="seconds to wait for the engine (0: do not wait)")
        p.add_argument("--json", action="store_true")
    p = sub.add_parser("retry", help="retry a failed milestone, or extend an exhausted budget")
    _repo_arg(p)
    p.add_argument("milestone")
    p.add_argument("--usd", type=float, default=None)
    p.add_argument("--hours", type=float, default=None)


def _open(args):
    from operonx_project.project import Project

    return Project.open(args.repo, worktrees=args.worktrees)


def _coder(state: Path):
    """``OPERONX_PROJECT_CODER=module:factory`` (``factory(state_dir) -> Coder``) swaps
    the coder: tests, or another agent CLI. The default is headless Claude Code."""
    import importlib
    import os

    spec = os.environ.get("OPERONX_PROJECT_CODER")
    if spec:
        mod, _, attr = spec.partition(":")
        return getattr(importlib.import_module(mod), attr)(state)
    from operonx_project.coder.claude import ClaudeCodeCoder

    return ClaudeCodeCoder(state_dirs=[str(state)])


def status_data(project) -> List[dict]:
    from operonx_project.board import columns

    out = []
    flagged = [i for i in project.store.interactions(states=["open"]) if i.get("flagged")]
    for ms in project.store.milestones():
        cards = project.store.cards(ms.id)
        cols = columns(cards)
        waiting = []
        for c in cols["waiting"]:
            q = c.detail.get("question") or {}
            waiting.append({"card": c.id, "question": q.get("problem") or q.get("reason") or c.reason})
        out.append({
            "id": ms.id, "title": ms.title, "state": ms.state.value, "reason": ms.reason,
            "mode": "w1" if ms.policy.horizon == "all" else "w2", "cost_usd": round(ms.cost_usd, 4),
            "budget_usd": ms.policy.budget.max_usd, "cards": len(cards),
            "columns": {k: len(v) for k, v in cols.items()}, "waiting": waiting,
            "flagged": [i["qid"] for i in flagged if i["milestone"] == ms.id],
        })  # fmt: skip
    return out


def pending_data(project) -> List[dict]:
    from operonx_project.interactions import Interactions

    out = []
    for i in Interactions(project.store).open():
        p = i.get("payload") or {}
        out.append({"id": i["id"], "short": i["short"], "qid": i["qid"], "milestone": i["milestone"],
                    "card": i.get("card"), "kind": i["kind"], "question": p.get("problem") or p.get("reason") or "",
                    "options": [o.get("key") for o in p.get("options") or [] if isinstance(o, dict)],
                    "recommended": p.get("recommended"), "flagged": bool(i.get("flagged")),
                    "delivery": i["delivery"], "since": i["created"]})  # fmt: skip
    return out


def _unsynced(store, mid: str, ref: str, inter: Optional[dict]) -> bool:
    """The card waits on a question the daemon has not made an interaction for yet."""
    try:
        card = store.get_card(mid, ref)
    except KeyError:
        return False
    iid = (card.detail.get("question") or {}).get("interrupt_id")
    return card.state.value == "waiting_human" and (inter is None or inter["id"] != iid)


def show_data(project, mid: str, cid: Optional[str], events: int) -> dict:
    store = project.store
    ms = store.get_milestone(mid)
    if cid:
        c = store.get_card(mid, cid)
        detail = {k: v for k, v in c.detail.items() if k not in ("baseline", "heldout")}
        held = c.detail.get("heldout") or {}
        detail["heldout"] = {"files": len(held.get("files") or [])} if held else None  # never the tests themselves
        evs = [e for e in store.events(mid, limit=100_000) if e["card"] == cid][-events:]
        return {"milestone": mid, "id": c.id, "title": c.title, "state": c.state.value,
                "stage": c.stage.value if c.stage else None, "reason": c.reason, "rows": c.rows,
                "criteria": c.criteria, "deps": c.deps, "paths": c.paths, "kind": c.kind, "risk": c.risk,
                "reopens": c.reopens, "run_id": c.run_id, "cost_usd": round(c.cost_usd, 4), "detail": detail,
                "events": evs}  # fmt: skip
    cards = store.cards(mid)
    return {"id": ms.id, "title": ms.title, "goal": ms.goal, "state": ms.state.value, "reason": ms.reason,
            "cost_usd": round(ms.cost_usd, 4), "replans": ms.replans, "policy": ms.policy.to_dict(),
            "cards": [{"id": c.id, "state": c.state.value, "stage": c.stage.value if c.stage else None,
                       "title": c.title, "deps": c.deps, "cost_usd": round(c.cost_usd, 4), "reason": c.reason}
                      for c in cards],
            "events": store.events(mid, limit=100_000)[-events:]}  # fmt: skip


def _practices(args) -> int:
    """``practices`` and ``arch``: files only, no store."""
    from operonx_project.practices import FILE, PRESETS, PracticesError, load, parse, render, tomllib

    out = sys.stdout
    repo = Path(args.repo)
    if args.cmd == "practices" and args.init:
        if args.init not in PRESETS:
            sys.stderr.write(f"error: no preset {args.init!r}; there are {', '.join(PRESETS)}\n")
            return 2
        path = repo / FILE
        if path.exists() and not args.force:
            sys.stderr.write(f"error: {FILE} exists (--force replaces it)\n")
            return 2
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(PRESETS[args.init], encoding="utf-8")
        out.write(f"wrote {FILE} ({args.init}); edit it for this project, then commit it\n")
    try:
        if getattr(args, "preset", None):
            if args.preset not in PRESETS:
                raise PracticesError(f"no preset {args.preset!r}; there are {', '.join(PRESETS)}")
            p = parse(tomllib.loads(PRESETS[args.preset]), source=f"preset {args.preset}")
        else:
            p = load(repo)
    except PracticesError as e:
        sys.stderr.write(f"error: {e}\n")
        return 2
    if args.cmd == "practices":
        if args.json:
            out.write(json.dumps(p.to_dict(), indent=2) + "\n")
        else:
            out.write((render(p) or f"no {FILE}: the defaults (acceptance tests first; no architecture rules)")
                      + "\n")  # fmt: skip
        return 0
    from operonx_project.contracts import check

    found = check(repo, p) if p.enforced else []
    extra = None
    if p.command:
        res = subprocess.run(p.command, shell=True, cwd=repo, capture_output=True, text=True)
        extra = {"cmd": p.command, "exit_code": res.returncode, "tail": (res.stdout + res.stderr)[-4000:]}
    if args.json:
        out.write(json.dumps({"enforced": p.enforced, "violations": [{**v.__dict__, "id": v.id} for v in found],
                              "command": extra}, indent=2) + "\n")  # fmt: skip
    else:
        for v in found:
            out.write(f"FAILED {v.id} - {v.message} (line {v.line})\n")
        if extra is not None:
            out.write(f"$ {extra['cmd']}\n{extra['tail']}\n")
            if extra["exit_code"]:
                out.write(f"FAILED arch:command - exit {extra['exit_code']}\n")
        out.write(f"{len(found)} violation(s)" + ("" if p.enforced else f" (no rules in {FILE})") + "\n")
    return 1 if found or (extra and extra["exit_code"]) else 0


def _plans(args) -> int:
    """``plans``: files only, no store. Exit 1 when a block is wrong."""
    from operonx_project.plans import find

    out = sys.stdout
    plans, errors, warnings = find(Path(args.repo))
    if args.json:
        out.write(json.dumps({"plans": [p.as_dict() for p in plans], "errors": errors, "warnings": warnings},
                             indent=1, ensure_ascii=False) + "\n")  # fmt: skip
        return 1 if errors else 0
    order = {"in_progress": 0, "plan": 1, "parked": 2, "done": 3}
    for p in sorted(plans, key=lambda p: (order[p.status], p.path)):
        cur = p.current if p.status == "in_progress" else None
        step = f" · now {cur.id} {cur.title}".rstrip() if cur else ""
        out.write(f"{p.status:<12} {p.done}/{len(p.phases)}  {p.title}{step}  ({p.path})\n")
    for w in warnings:
        out.write(f"warning: {w}\n")
    for e in errors:
        out.write(f"ERROR {e}\n")
    out.write(f"{len(plans)} plan(s), {len(warnings)} without a status block, {len(errors)} error(s)\n")
    return 1 if errors else 0


def _status(args) -> int:
    """``status``: the project in one document (PROJECT_MODE_PLAN D2), read-only: no store is made."""
    from operonx_project.status import status, text

    doc = status(args.repo)
    sys.stdout.write(json.dumps(doc, indent=1, ensure_ascii=False) + "\n" if args.json else text(doc))
    return 0


def _milestones(args) -> int:
    out = sys.stdout
    if args.cmd in ("practices", "arch"):
        return _practices(args)
    if args.cmd == "plans":
        return _plans(args)
    if args.cmd == "status":
        return _status(args)
    if args.cmd == "api":
        raw = sys.stdin.read() if args.request == "-" else args.request
        try:
            req = json.loads(raw)
        except ValueError as exc:
            out.write(json.dumps({"ok": False, "status": "invalid", "message": f"not JSON: {exc}"}) + "\n")
            return 2
        if isinstance(req, dict) and req.get("op") == "status":  # read-only: before a store is opened
            from operonx_project.status import status, text

            doc = status(args.repo, changes=int(req.get("changes", 15)))
            # the same in plain lines: what an assistant reads (the studio's project_status tool)
            out.write(json.dumps({"ok": True, "project": doc, "text": text(doc)}, ensure_ascii=False) + "\n")
            return 0
    if args.cmd == "new":
        from operonx_project.project import new_project

        proj = new_project(args.dir, name=args.name)
        out.write(f"{proj.repo}: a project with docs/brd/ and an empty board. Next: operonx-project start "
                  f"--repo {proj.repo} --title ... --source <requirements>\n")  # fmt: skip
        return 0
    proj = _open(args)
    if args.cmd == "adopt":
        from operonx_project.adopt import adopt, main_report

        answer = json.loads(args.answer.read_text(encoding="utf-8")) if args.answer else None
        report = asyncio.run(adopt(proj, _coder(proj.state), prefix=args.prefix, answer=answer,
                                   python=[args.python] if args.python else None, max_usd=args.max_usd,
                                   model=args.model))  # fmt: skip
        out.write(main_report(report) + "\n")
        out.write(f"Read {proj.repo / 'docs' / 'brd'} and commit it; the studio's Board shows it.\n")
        return 0
    if args.cmd == "start":
        from operonx_project.model import Policy
        from operonx_project.project import create_milestone

        kw = {"approve_plan": "auto" if args.auto_approve else "ask", "heldout": not args.no_heldout}
        if args.max_parallel:
            kw["max_parallel"] = args.max_parallel
        policy = Policy.w1(**kw) if args.mode == "w1" else Policy.w2(**kw)
        if args.max_usd is not None:
            policy.budget.max_usd = args.max_usd
        if args.max_hours is not None:
            policy.budget.max_hours = args.max_hours
        sources = [{"path": str(p)} for p in args.source] + [{"path": str(p), "private": True} for p in args.private]
        seed = None
        if args.from_board:
            from operonx_project.project import board_seed

            seed = board_seed(args.board or proj.repo / "docs" / "brd", args.from_board)
        ms = create_milestone(proj, args.title, goal=args.goal, mode=args.mode, sources=sources, rows=args.rows,
                              policy=policy, prefix=args.prefix, constraints=args.constraints, seed=seed,
                              needs=args.needs)  # fmt: skip
        proj.store.enqueue(ms.id, "start")
        out.write(f"{ms.id} created and queued; the daemon starts it (operonx-project daemon --repo {proj.repo})\n")
        return 0
    if args.cmd in ("ship", "merge"):
        from operonx_project.api import act

        got = act(proj, {"op": args.cmd, "milestone": args.milestone}, actor="cli")
        out.write(got["message"] + "\n")
        return 0 if got["ok"] else 1
    if args.cmd == "request":
        from operonx_project.api import act

        text = sys.stdin.read() if args.text == "-" else args.text
        got = act(proj, {"op": "request", "text": text, "title": args.title, "mode": args.mode,
                         "max_usd": args.max_usd}, actor="cli")  # fmt: skip
        out.write(got["message"] + "\n")
        return 0 if got["ok"] else 1
    if args.cmd == "daemon":
        from operonx_project.daemon import Daemon, DaemonBusy
        from operonx_project.lifecycle import Lifecycle

        models = dict(m.split("=", 1) for m in args.model)
        if args.check_mode == "docker" and not args.check_image:
            sys.stderr.write("error: --check-mode docker needs --check-image\n")
            return 2
        from operonx_project.project import load_settings

        sandbox = not args.no_sandbox and bool(load_settings(proj.state).get("sandbox", True))
        life = Lifecycle(proj, _coder(proj.state), models=models or None, sandbox=sandbox,
                         python=args.python, check_mode=args.check_mode, check_image=args.check_image)  # fmt: skip
        from operonx_project.interactions import Interactions

        inter = Interactions(proj.store, project=proj)
        bridge = None
        if args.telegram_chat:
            import os

            from operonx_project.telegram import TelegramBridge, TelegramMessenger

            token = os.environ.get("OPERONX_TELEGRAM_TOKEN", "")
            if not token or not args.telegram_user:
                sys.stderr.write("error: Telegram needs OPERONX_TELEGRAM_TOKEN and at least one --telegram-user\n")
                return 2
            bridge = TelegramBridge(proj.store, inter, TelegramMessenger(token, api=args.telegram_api),
                                    args.telegram_chat, args.telegram_user, project_name=proj.repo.name)  # fmt: skip
        try:
            asyncio.run(_serve(Daemon(life, poll_s=args.poll, interactions=inter, bridge=bridge), args.once))
        except DaemonBusy as exc:
            sys.stderr.write(f"error: {exc}\n")
            return 3
        return 0
    if args.cmd == "milestones":
        data = status_data(proj)
        if args.json:
            out.write(json.dumps(data, indent=1) + "\n")
            return 0
        if not data:
            out.write("no milestone yet: operonx-project start --title ...\n")
        for m in data:
            out.write(f"{m['id']}  {m['state']:<18} {m['title']}  ({m['mode']}, ${m['cost_usd']:.2f} of "
                      f"${m['budget_usd']:.0f})\n")  # fmt: skip
            if m["reason"]:
                out.write(f"    {m['reason'][:200]}\n")
            out.write("    " + " · ".join(f"{k} {v}" for k, v in m["columns"].items()) + "\n")
            for w in m["waiting"]:
                out.write(f"    waits on you: {w['card']} — {str(w['question'])[:160]}\n")
            if m["flagged"]:
                out.write(f"    ⚑ expired with no answer and no safe default: {', '.join(m['flagged'])}\n")
        return 0
    if args.cmd == "api":
        from operonx_project.api import act

        result = act(proj, req if isinstance(req, dict) else {}, actor=args.actor)
        out.write(json.dumps(result) + "\n")
        return 0 if result["ok"] else 1
    if args.cmd == "pending":
        data = pending_data(proj)
        if args.json:
            out.write(json.dumps(data, indent=1) + "\n")
            return 0
        if not data:
            out.write("nothing waits on you\n")
        for q in data:
            where = q["milestone"] + (f"/{q['card']}" if q["card"] else "")
            opts = f"  [{' | '.join(map(str, q['options']))}]" if q["options"] else ""
            flag = "  ⚑ expired" if q["flagged"] else ""
            out.write(f"{q['qid']}  {where:<10} {q['kind']:<9} {str(q['question'])[:110]}{opts}{flag}\n")
        if data:
            out.write("answer: operonx-project answer <milestone> <card or Q-nn> <option or words>\n")
        return 0
    if args.cmd == "show":
        data = show_data(proj, args.milestone, args.card, args.events)
        if args.json:
            out.write(json.dumps(data, indent=1, default=str) + "\n")
            return 0
        if args.card:
            out.write(f"{data['milestone']}/{data['id']}  {data['state']}{'/' + data['stage'] if data['stage'] else ''}"
                      f"  {data['title']}  (${data['cost_usd']:.2f}, re-opened {data['reopens']}×)\n")  # fmt: skip
            for k in ("reason", "rows", "deps", "paths", "criteria", "run_id"):
                if data[k]:
                    out.write(f"  {k}: {data[k]}\n")
            if data["detail"].get("question"):
                out.write(f"  question: {json.dumps(data['detail']['question'], ensure_ascii=False)[:600]}\n")
        else:
            out.write(f"{data['id']}  {data['state']}  {data['title']}  (${data['cost_usd']:.2f}, "
                      f"{data['replans']} replan(s))\n")  # fmt: skip
            if data["reason"]:
                out.write(f"  {data['reason'][:300]}\n")
            for c in data["cards"]:
                st = c["state"] + (f"/{c['stage']}" if c["stage"] else "")
                out.write(f"  {c['id']:<6} {st:<18} {c['title'][:50]:<50} {c['reason'][:70]}\n")
        for e in data["events"]:
            out.write(f"  · {e['seq']:>5} {e['kind']:<22} {(e['card'] or ''):<6} {json.dumps(e['data'])[:110]}\n")
        return 0
    if args.cmd in ("take", "submit", "release"):
        return _taker(proj, args, out)
    payload = {}
    card = None
    if args.cmd == "answer":
        from operonx_project.interactions import Interactions

        answer = {"choice": args.answer, "text": args.text} if args.text else args.answer
        proj.store.get_milestone(args.milestone)
        got = Interactions(proj.store).submit_answer(args.card, answer, actor="owner", via="cli", mid=args.milestone)
        if got.ok:
            out.write(f"{got.message}; the daemon applies it\n")
            return 0
        if not (got.status in ("unknown", "already", "stale") and _unsynced(proj.store, args.milestone, args.card,
                                                                            got.interaction)):  # fmt: skip
            sys.stderr.write(f"error: {got.message}\n")
            return 1
        card = args.card  # the daemon has not opened this question yet: queue the answer on the card
        payload = {"answer": answer}
    elif args.cmd == "replan":
        payload = {"notes": args.notes}
    elif args.cmd == "retry":
        payload = {k: v for k, v in (("usd", args.usd), ("hours", args.hours)) if v is not None}
    proj.store.get_milestone(args.milestone)
    seq = proj.store.enqueue(args.milestone, args.cmd, payload, card=card)
    out.write(f"queued {args.cmd} for {args.milestone} (#{seq}); the daemon runs it\n")
    return 0


def _taker(proj, args, out) -> int:
    """take / submit / release: queued through the api, then waited on until the
    card is where its taker acts again (parked for a submit) or has ended."""
    import getpass
    import time

    from operonx_project.api import act, taker_brief

    actor = args.actor or f"cli:{getpass.getuser()}"
    sent = act(proj, {"op": args.cmd, "milestone": args.milestone, "card": args.card, "summary": args.summary},
               actor=actor)  # fmt: skip
    if not sent["ok"]:
        sys.stderr.write(f"error: {sent['message']}\n")
        return 1
    seq, deadline = sent["seq"], time.time() + max(0.0, args.wait)
    brief: dict = {}
    while True:
        cmd = proj.store.command(seq)
        if cmd and cmd["status"] not in ("pending",) and cmd["status"] != "done":
            sys.stderr.write(f"error: {cmd.get('result') or cmd['status']}\n")
            return 1
        brief = taker_brief(proj, args.milestone, args.card)
        settled = cmd and cmd["status"] == "done" and (
            brief["waiting_for_submit"] or brief["state"] in ("verified", "done", "failed", "cancelled")
            or args.cmd == "release")  # fmt: skip
        if settled or time.time() >= deadline:
            break
        time.sleep(1.0)
    if args.json:
        out.write(json.dumps(brief) + "\n")
        return 0
    if brief["waiting_for_submit"]:
        out.write(f"{brief['card']} · {brief['title']} — yours ({brief['taken']['actor']}), round {brief['round']}\n"
                  f"  worktree  {brief['worktree']}  (branch {brief['branch']})\n"
                  f"  rows      {', '.join(brief['rows']) or '—'}\n"
                  f"  files     {', '.join(brief['paths']) or 'anywhere in the project'}\n"
                  f"  tests     {brief['acceptance_tests'] or '—'} (written for you; do not change them)\n"
                  "  criteria\n" + "".join(f"    - {c}\n" for c in brief["criteria"]))  # fmt: skip
        if brief["feedback"]:
            out.write("  the last round found\n" + "".join(f"    {ln}\n" for ln in brief["feedback"].splitlines()))
        out.write(f"When the criteria pass there: operonx-project submit {args.milestone} {args.card}\n")
    else:
        out.write(f"{brief['card']}: {brief['state']}{' (' + brief['stage'] + ')' if brief['stage'] else ''}"
                  f" — {brief['reason'] or sent['message']}\n")  # fmt: skip
    return 0


async def _serve(daemon: Any, once: bool) -> None:
    """The daemon until it is done, or until SIGTERM / SIGINT: a stop ends the loop and releases the lease (a
    killed process left it held, and a start right after the studio's stop saw "a daemon drives it")."""
    import signal

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)
    await daemon.serve(once=once, stop=stop)


def main(argv: Optional[List[str]] = None) -> int:
    from operonx_project import __version__

    args = sys.argv[1:] if argv is None else argv
    if args in ([], ["--version"]):
        sys.stdout.write(f"operonx-project {__version__}\n")
        return 0
    ap = argparse.ArgumentParser(prog="operonx-project")
    sub = ap.add_subparsers(dest="cmd", required=True)
    _brd_parser(sub)
    _milestone_parsers(sub)
    ns = ap.parse_args(args)
    if ns.cmd == "brd":
        return _brd(ns)
    try:
        return _milestones(ns)
    except KeyError as exc:
        sys.stderr.write(f"error: {exc.args[0] if exc.args else exc}\n")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
