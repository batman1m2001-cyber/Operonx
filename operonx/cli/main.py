"""`operonx` — the command line: one command, a subcommand each.

    operonx init [DIR] [--template NAME] [--name NAME] [--force]
    operonx guide [DIR]             # sync the installed guides into DIR/.operonx/guide/
    operonx guide --check [DIR]     # exit 1 when that copy is stale (CI)
    operonx guide --path            # where the installed core guide is
    operonx run ...                 # run a job              (operonx.cli.run)
    operonx serve ...               # serve the services     (operonx.cli.serve)
    operonx play ...                # drive a served door    (operonx.app.play)
    operonx eval ...                # experiments: run, compare, report (operonx.cli.eval)
    operonx studio [DIR]            # open the project in operonx-studio (operonx.cli.studio)

``run``, ``serve``, ``play``, ``eval`` and ``studio`` are handed the rest of the
command line untouched: each is its module's own ``main(argv)`` (for the
first three, the one the deprecated ``operonx-<name>`` alias calls too),
so there is one parser per command and two spellings cannot differ.

Inside a project whose ``.operonx/guide/`` is stale — an operonx package
was added, removed or upgraded — any command first syncs it and says so
in one line on stderr.
"""

from __future__ import annotations

import argparse
import importlib
import sys
from pathlib import Path
from typing import Optional, Sequence

from operonx.cli.init import TEMPLATES, InitError, init_project

__all__ = ["main", "TEMPLATES", "DELEGATED"]

#: Subcommands that are another module's ``main(argv)``: name → (module, help).
DELEGATED = {
    "run": ("operonx.cli.run", "run a job the application declares"),
    "serve": ("operonx.cli.serve", "serve the application's services"),
    "play": ("operonx.app.play", "the playground bridge: drive a service's doors over JSON lines"),
    "eval": ("operonx.cli.eval", "run experiments, compare and report them, size them"),
    "studio": ("operonx.cli.studio", "open this project in operonx-studio (starts it if needed)"),
}


def _parser() -> argparse.ArgumentParser:
    from operonx import __version__

    parser = argparse.ArgumentParser(
        prog="operonx",
        description="operonx: start a project, keep its guide current, run and serve it.",
    )
    parser.add_argument("--version", action="version", version=f"operonx {__version__}")
    sub = parser.add_subparsers(dest="command", metavar="COMMAND")

    init = sub.add_parser(
        "init",
        help="create a project: layout, app, a feature, tests, AGENTS.md and the guide",
        description="Create an operonx project in DIR. Existing files are kept unless --force.",
    )
    init.add_argument("dir", nargs="?", default=".", help="where (default: here)")
    init.add_argument(
        "--template",
        "-t",
        default="hello",
        choices=list(TEMPLATES),
        help="what the first feature is: "
        + "; ".join(f"{t.name}: {t.summary}" for t in TEMPLATES.values()),
    )
    init.add_argument("--name", default=None, help="project name (default: DIR's name)")
    init.add_argument("--force", action="store_true", help="overwrite files that already exist")
    init.add_argument(
        "--editable",
        default=None,
        metavar="PATH",
        help="use the operonx checkout at PATH instead of PyPI (default inside a checkout)",
    )

    guide = sub.add_parser(
        "guide",
        help="sync the guides of the installed operonx packages into the project",
        description="Copy every installed operonx package's guide into DIR/.operonx/guide/ "
        "(one folder per package, plus an index) and update AGENTS.md's operonx block.",
    )
    guide.add_argument(
        "dir", nargs="?", default=None, help="the project (default: the one at or above here)"
    )
    what = guide.add_mutually_exclusive_group()
    what.add_argument(
        "--check", action="store_true", help="change nothing; exit 1 when the copy is stale"
    )
    what.add_argument(
        "--path", action="store_true", help="print the installed core guide's directory"
    )
    what.add_argument("--sync", action="store_true", help=argparse.SUPPRESS)  # the old spelling
    for name, (_, summary) in DELEGATED.items():
        # listed for --help only: main() hands these their argv before parsing
        sub.add_parser(name, help=f"{summary} (`operonx {name} --help`)", add_help=False)
    return parser


def _init(args: argparse.Namespace) -> int:
    root = Path(args.dir)
    try:
        result = init_project(
            root,
            template=args.template,
            name=args.name,
            force=args.force,
            editable=Path(args.editable) if args.editable else None,
        )
    except InitError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    where = args.dir if args.dir != "." else "here"
    if result.created or result.overwritten:
        print(f"{result.name}: {result.template} project in {where}")
        for rel in result.created:
            print(f"  + {rel}")
        for rel in result.overwritten:
            print(f"  ~ {rel} (overwritten)")
        if result.kept:
            print(f"  kept {len(result.kept)} existing file(s) (--force overwrites):")
            for rel in result.kept:
                print(f"    = {rel}")
    else:
        print(
            f"nothing to create: every file of the {result.template} template is already in {where}"
        )
        print("  (--force overwrites them)")

    print("\nNext:")
    if root.resolve() != Path.cwd().resolve():
        print(f"  cd {args.dir}")
    for step in TEMPLATES[result.template].next_steps:
        print(f"  {step}")
    print(
        "\nSee it: operonx studio   (install the studio once: see .operonx/guide/core/09-studio.md)"
    )
    print("Coding assistants: start at AGENTS.md.")
    return 0


def _guide(args: argparse.Namespace) -> int:
    from operonx import guide

    if args.path:
        print(guide.path())
        return 0
    root = Path(args.dir) if args.dir else guide.find_project()
    if root is None:
        print(
            "error: no operonx.toml here or above; name the project: operonx guide DIR",
            file=sys.stderr,
        )
        return 2
    if args.check:
        stale = guide.changes(root)
        if stale:
            print(f"guide is stale: {', '.join(stale)} (run `operonx guide`)", file=sys.stderr)
            return 1
        return 0
    done = guide.sync(root)
    print(f"guide: {', '.join(done)}" if done else "guide: up to date")
    print(root / guide.COPY_DIR / "README.md")
    return 0


def _autosync() -> None:
    """Sync a project's stale guide copy before a command; never fail it."""
    try:
        from operonx import guide

        root = guide.find_project()
        if root is None or not (root / guide.COPY_DIR).is_dir():
            return
        done = guide.sync(root) if guide.changes(root) else []
        if done:
            print(f"guide: {', '.join(done)}", file=sys.stderr)
    except Exception:  # a read-only checkout, a broken install: the command still runs
        pass


def main(argv: Optional[Sequence[str]] = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] in DELEGATED:
        _autosync()
        return importlib.import_module(DELEGATED[argv[0]][0]).main(argv[1:])
    parser = _parser()
    args = parser.parse_args(argv)
    if args.command == "init":
        return _init(args)
    if args.command == "guide":
        return _guide(args)
    parser.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
