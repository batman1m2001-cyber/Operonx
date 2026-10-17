"""``operonx-project`` (and ``operonx project``).

The milestone commands queue a command in the project's store; the daemon
(one per project) runs it and drives the milestone::

    operonx-project new DIR [--name N]                 # a repo with docs/brd/ and an empty board
    operonx-project start --title T [--goal G] [--mode w1|w2] [--source FILE ...] [--private FILE ...]
                          [--rows ID ...] [--auto-approve] [--max-usd N] [--repo .]
    operonx-project daemon [--repo .] [--once] [--no-sandbox] [--model ROLE=MODEL ...]
    operonx-project status [--repo .] [--json]
    operonx-project show MID [CARD] [--repo .] [--json]
    operonx-project approve MID | pause MID | unpause MID | cancel MID
    operonx-project answer MID CARD ANSWER [--text WORDS]
    operonx-project replan MID --notes TEXT              # revise a waiting plan; replan a stopped milestone
    operonx-project retry MID [--usd N]                  # a failed milestone, or more budget

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
import sys
import tempfile
from pathlib import Path
from typing import List, Optional


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
        rt = BrdRuntime(coder=ClaudeCodeCoder(state_dirs=[str(state)]), data_dir=state.parent / "brd")
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
    p.add_argument("--max-parallel", type=int, default=None)
    p.add_argument("--no-heldout", action="store_true")
    p = sub.add_parser("daemon", help="drive the project's milestones and run the queued commands")
    _repo_arg(p)
    p.add_argument("--once", action="store_true", help="stop when nothing is left to drive")
    p.add_argument("--no-sandbox", action="store_true",
                   help="a trusted repo: the implementer's shell runs with no OS sandbox (no boundary)")  # fmt: skip
    p.add_argument("--model", action="append", default=[], metavar="ROLE=MODEL")
    p.add_argument("--python", help="the interpreter the target's checks run with (default: its .venv, else this)")
    p.add_argument("--poll", type=float, default=0.5)
    for name in ("status",):
        p = sub.add_parser(name, help="every milestone: state, board, cost, what waits for you")
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
    p = sub.add_parser("answer", help="answer the question a card waits on")
    _repo_arg(p)
    p.add_argument("milestone")
    p.add_argument("card")
    p.add_argument("answer", help="an option's key, or words")
    p.add_argument("--text", default="", help="words to go with the option")
    p = sub.add_parser("replan", help="revise a waiting plan, or replan a stopped milestone")
    _repo_arg(p)
    p.add_argument("milestone")
    p.add_argument("--notes", required=True)
    p = sub.add_parser("retry", help="retry a failed milestone, or extend an exhausted budget")
    _repo_arg(p)
    p.add_argument("milestone")
    p.add_argument("--usd", type=float, default=None)


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
        })  # fmt: skip
    return out


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


def _milestones(args) -> int:
    out = sys.stdout
    if args.cmd == "new":
        from operonx_project.project import new_project

        proj = new_project(args.dir, name=args.name)
        out.write(f"{proj.repo}: a project with docs/brd/ and an empty board. Next: operonx-project start "
                  f"--repo {proj.repo} --title ... --source <requirements>\n")  # fmt: skip
        return 0
    proj = _open(args)
    if args.cmd == "start":
        from operonx_project.model import Policy
        from operonx_project.project import create_milestone

        kw = {"approve_plan": "auto" if args.auto_approve else "ask", "heldout": not args.no_heldout}
        if args.max_parallel:
            kw["max_parallel"] = args.max_parallel
        policy = Policy.w1(**kw) if args.mode == "w1" else Policy.w2(**kw)
        if args.max_usd is not None:
            policy.budget.max_usd = args.max_usd
        sources = [{"path": str(p)} for p in args.source] + [{"path": str(p), "private": True} for p in args.private]
        ms = create_milestone(proj, args.title, goal=args.goal, mode=args.mode, sources=sources, rows=args.rows,
                              policy=policy, prefix=args.prefix, constraints=args.constraints)  # fmt: skip
        proj.store.enqueue(ms.id, "start")
        out.write(f"{ms.id} created and queued; the daemon starts it (operonx-project daemon --repo {proj.repo})\n")
        return 0
    if args.cmd == "daemon":
        from operonx_project.daemon import Daemon, DaemonBusy
        from operonx_project.lifecycle import Lifecycle

        models = dict(m.split("=", 1) for m in args.model)
        life = Lifecycle(proj, _coder(proj.state), models=models or None, sandbox=not args.no_sandbox,
                         python=args.python)  # fmt: skip
        try:
            asyncio.run(Daemon(life, poll_s=args.poll).serve(once=args.once))
        except DaemonBusy as exc:
            sys.stderr.write(f"error: {exc}\n")
            return 3
        return 0
    if args.cmd == "status":
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
    payload = {}
    card = None
    if args.cmd == "answer":
        card = args.card
        payload = {"answer": {"choice": args.answer, "text": args.text} if args.text else args.answer}
    elif args.cmd == "replan":
        payload = {"notes": args.notes}
    elif args.cmd == "retry" and args.usd is not None:
        payload = {"usd": args.usd}
    proj.store.get_milestone(args.milestone)
    seq = proj.store.enqueue(args.milestone, args.cmd, payload, card=card)
    out.write(f"queued {args.cmd} for {args.milestone} (#{seq}); the daemon runs it\n")
    return 0


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
