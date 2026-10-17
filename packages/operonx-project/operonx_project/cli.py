"""``operonx-project`` (and ``operonx project``).

P4 adds the living BRD's commands; the milestone commands arrive in P5::

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


def main(argv: Optional[List[str]] = None) -> int:
    from operonx_project import __version__

    args = sys.argv[1:] if argv is None else argv
    if args in ([], ["--version"]):
        sys.stdout.write(f"operonx-project {__version__}\n")
        return 0
    ap = argparse.ArgumentParser(prog="operonx-project")
    sub = ap.add_subparsers(dest="cmd", required=True)
    _brd_parser(sub)
    ns = ap.parse_args(args)
    if ns.cmd == "brd":
        return _brd(ns)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
