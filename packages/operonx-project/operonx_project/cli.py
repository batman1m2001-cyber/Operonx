"""``operonx-project`` (and ``operonx project``): the BRD and the board, read and kept.

    operonx-project new DIR [--name N]                 # a repo with docs/brd/
    operonx-project adopt [--repo .] [--prefix P]       # an existing project: its BRD from its docs and tests
    operonx-project status [--repo .] [--json]         # checklist, board, what needs you (read-only)
    operonx-project board [--repo .] [--json]          # the board's cycles and cards (docs/brd/board.md)
    operonx-project board CARD STATE [--note TEXT]     # move a card: todo, doing, "needs you", done
    operonx-project put [CARD] [--title T] [--cycle ID|Backlog] [--files F ...] [--req R ...] [--after C ...]
    operonx-project cycle open ID TITLE [--plan docs/X_PLAN.md]   # a cycle: a plan's round of work
    operonx-project cycle close ID                     # refused while a card in it is not done
    operonx-project api '{"op": "status"}' [--repo .]  # status as JSON, for the studio

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
        # the engine's state stays denied; the figures the session must look at live beside it
        rt = BrdRuntime(
            coder=ClaudeCodeCoder(state_dirs=[str(state)]), data_dir=state.parent / "brd", settings_dir=state
        )
        report = asyncio.run(extract_source(rt, args.brd, args.file, args.sid, args.prefix, args.title, args.name))
        out.write(json.dumps(report, ensure_ascii=False, indent=1) + "\n")
        return 0 if not report.get("problems") else 1
    return 2


# ── the project ────────────────────────────────────────────────────────────


def _repo_arg(p) -> None:
    p.add_argument("--repo", type=Path, default=Path("."), help="the project's repo (default: here)")


def _project_parsers(sub) -> None:
    p = sub.add_parser("new", help="a new project: a git repo with docs/brd/")
    p.add_argument("dir", type=Path)
    p.add_argument("--name")
    p = sub.add_parser("adopt", help="an existing project: its BRD from its docs and tests")
    _repo_arg(p)
    p.add_argument("--prefix", default="", help="the requirement id prefix (default: from the project name)")
    p.add_argument("--answer", type=Path, default=None, help="a recorded answer (JSON) instead of the session")
    p.add_argument("--max-usd", type=float, default=2.0)
    p.add_argument("--model", default=None)
    p.add_argument("--python", default=None, help="the interpreter the cited tests run with (default: its .venv)")
    p = sub.add_parser("api", help='\'{"op": "status"}\': the status as JSON (for the studio)')
    _repo_arg(p)
    p.add_argument("request", help="the request as JSON, or - to read it from stdin")
    p = sub.add_parser("status", help="where the project stands: checklist, board, what needs you, changes")
    _repo_arg(p)
    p.add_argument("--json", action="store_true")
    p = sub.add_parser("board", help="the board's cards; with CARD STATE, move a card")
    _repo_arg(p)
    p.add_argument("card", nargs="?")
    p.add_argument("state", nargs="?", help="todo, doing, needs you, done")
    p.add_argument("--note", default=None, help="the card's note: the question, or the owner's answer")
    p.add_argument("--json", action="store_true")
    p = sub.add_parser("put", help="add a card (no CARD), or change one's title, cycle or fields")
    _repo_arg(p)
    p.add_argument("card", nargs="?", default="")
    p.add_argument("--title", default="")
    p.add_argument("--cycle", default=None, help="an open cycle's id, or Backlog")
    for k in ("req", "after", "files", "done"):
        p.add_argument(f"--{k}", nargs="*", default=None)
    p = sub.add_parser("cycle", help="open or close a cycle (a plan's round of work)")
    _repo_arg(p)
    p.add_argument("action", choices=("open", "close"))
    p.add_argument("id")
    p.add_argument("title", nargs="?", default="")
    p.add_argument("--plan", default="", help="the plan doc the cycle works")


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


def _put(args) -> int:
    """``put``: add a card, or change one. Exit 1 when refused."""
    from operonx_project import board

    fields = {k: getattr(args, k) for k in ("req", "after", "files", "done") if getattr(args, k) is not None}
    try:
        c = board.put(args.repo, args.card, args.title, cycle=args.cycle, **fields)
    except (board.BoardError, OSError) as exc:
        sys.stderr.write(f"error: {exc}\n")
        return 1
    sys.stdout.write(f"{c.id} · {c.title} · {c.state} · {c.cycle or board.BACKLOG}\n")
    return 0


def _cycle(args) -> int:
    """``cycle open|close``. Exit 1 when refused (a close with cards not done)."""
    from operonx_project import board

    try:
        y = (board.open_cycle(args.repo, args.id, args.title, plan=args.plan) if args.action == "open"
             else board.close(args.repo, args.id))  # fmt: skip
    except (board.BoardError, OSError) as exc:
        sys.stderr.write(f"error: {exc}\n")
        return 1
    sys.stdout.write(f"{y.id} · {y.title} · {y.state}\n")
    return 0


def _board(args) -> int:
    """``board``: print the cards, or move one (``board C4 done``). Exit 1 when the move is refused."""
    from operonx_project import board

    out = sys.stdout
    try:
        if args.card:
            if not args.state:
                raise board.BoardError("move a card with: board CARD STATE")
            c = board.move(args.repo, args.card, args.state, note=args.note)
            out.write(f"{c.id} · {c.title} · {c.state}\n" + (f"  {c.note}\n" if c.note else ""))
            return 0
        got = board.read_all(args.repo)
    except (board.BoardError, OSError) as exc:
        sys.stderr.write(f"error: {exc}\n")
        return 1
    if got is None:
        out.write(f"no board yet ({board.PATH}): the first card makes it\n")
        return 0
    view = board.view(got[1], got[0])
    if args.json:
        out.write(json.dumps(view, indent=1, ensure_ascii=False) + "\n")
        return 0
    shut = {y["id"] for y in view["cycles"] if y["state"] == "closed"}
    for y in view["cycles"]:
        out.write(f"## {y['id']} · {y['title']} · {y['state']} · {y['done']}/{len(y['cards'])} done\n")
    for c in view["cards"]:
        if c["cycle"] in shut:
            continue
        waits = f"  after {', '.join(c['waits'])}" if c["waits"] else ""
        out.write(f"{c['id']:<4} {c['state']:<9} {c['cycle'] or 'Backlog':<8} {c['lane']:<8} {c['kind']:<5} {c['title']}{waits}\n")
        if c["note"]:
            out.write(f"     {c['note'].splitlines()[0][:200]}\n")
    out.write(" · ".join(f"{len(ids)} {s}" for s, ids in view["columns"].items()) + "\n")
    return 0


def _run(args) -> int:
    from operonx_project.status import status, text

    out = sys.stdout
    if args.cmd == "put":
        return _put(args)
    if args.cmd == "cycle":
        return _cycle(args)
    if args.cmd == "board":
        return _board(args)
    if args.cmd == "status":
        doc = status(args.repo)
        out.write(json.dumps(doc, indent=1, ensure_ascii=False) + "\n" if args.json else text(doc))
        return 0
    if args.cmd == "api":
        raw = sys.stdin.read() if args.request == "-" else args.request
        try:
            req = json.loads(raw)
        except ValueError as exc:
            out.write(json.dumps({"ok": False, "status": "invalid", "message": f"not JSON: {exc}"}) + "\n")
            return 2
        if not isinstance(req, dict) or req.get("op") != "status":
            out.write(json.dumps({"ok": False, "status": "invalid", "message": "the one op is status"}) + "\n")
            return 2
        doc = status(args.repo, changes=int(req.get("changes", 15)))
        # the same in plain lines: what an assistant reads (the studio's project_status tool)
        out.write(json.dumps({"ok": True, "project": doc, "text": text(doc)}, ensure_ascii=False) + "\n")
        return 0
    if args.cmd == "new":
        from operonx_project.project import new_project

        proj = new_project(args.dir, name=args.name)
        out.write(f"{proj.repo}: a project with docs/brd/. Next: operonx-project brd register "
                  f"<requirements> --repo {proj.repo}\n")  # fmt: skip
        return 0
    if args.cmd == "adopt":
        from operonx_project.adopt import adopt, main_report
        from operonx_project.project import Project

        proj = Project.open(args.repo)
        answer = json.loads(args.answer.read_text(encoding="utf-8")) if args.answer else None
        report = asyncio.run(adopt(proj, _coder(proj.state), prefix=args.prefix, answer=answer,
                                   python=[args.python] if args.python else None, max_usd=args.max_usd,
                                   model=args.model))  # fmt: skip
        out.write(main_report(report) + "\n")
        out.write(f"Read {proj.repo / 'docs' / 'brd'} and commit it; the studio shows it.\n")
        return 0
    return 2


def main(argv: Optional[List[str]] = None) -> int:
    from operonx_project import __version__

    args = sys.argv[1:] if argv is None else argv
    if args in ([], ["--version"]):
        sys.stdout.write(f"operonx-project {__version__}\n")
        return 0
    ap = argparse.ArgumentParser(prog="operonx-project")
    sub = ap.add_subparsers(dest="cmd", required=True)
    _brd_parser(sub)
    _project_parsers(sub)
    ns = ap.parse_args(args)
    if ns.cmd == "brd":
        return _brd(ns)
    try:
        return _run(ns)
    except KeyError as exc:
        sys.stderr.write(f"error: {exc.args[0] if exc.args else exc}\n")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
