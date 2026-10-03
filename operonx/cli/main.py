"""`operonx` — the command line: one command, a subcommand each.

    operonx init [DIR] [--template NAME] [--name NAME] [--force]
    operonx guide                   # the guide's index (README.md)
    operonx guide --path            # where the installed guide is
    operonx guide --sync [DIR]      # copy it into DIR/.operonx/guide/
    operonx run ...                 # run a job or runbook   (operonx.cli.run)
    operonx serve ...               # serve the services     (operonx.cli.serve)
    operonx pack ...                # graphs → Rust JSON spec (operonx.cli.pack)
    operonx play ...                # drive a served door    (operonx.app.play)

``run``, ``serve``, ``pack`` and ``play`` are handed the rest of the
command line untouched: each is its module's own ``main(argv)``, the one
the deprecated ``operonx-<name>`` alias calls too, so there is one parser
per command and the two spellings cannot differ.
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
    "run": ("operonx.cli.run", "run a job or runbook the application declares"),
    "serve": ("operonx.cli.serve", "serve the application's services"),
    "pack": ("operonx.cli.pack", "serialise @graph factories to the Rust runtime's JSON spec"),
    "play": ("operonx.app.play", "the playground bridge: drive a service's doors over JSON lines"),
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

    guide = sub.add_parser(
        "guide",
        help="the operonx guide for coding assistants",
        description="Print the guide's index, its installed path, or copy it into a project.",
    )
    what = guide.add_mutually_exclusive_group()
    what.add_argument("--path", action="store_true", help="print the installed guide's directory")
    what.add_argument(
        "--sync",
        nargs="?",
        const="",
        default=None,
        metavar="DIR",
        help="copy the installed guide into DIR/.operonx/guide/ "
        "(default: the project at or above here)",
    )
    for name, (_, summary) in DELEGATED.items():
        # listed for --help only: main() hands these their argv before parsing
        sub.add_parser(name, help=f"{summary} (`operonx {name} --help`)", add_help=False)
    return parser


def _init(args: argparse.Namespace) -> int:
    root = Path(args.dir)
    try:
        result = init_project(root, template=args.template, name=args.name, force=args.force)
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
    print("\nCoding assistants: start at AGENTS.md.")
    return 0


def _project_root(start: Path) -> Path:
    for candidate in (start, *start.parents):
        if (candidate / "operonx.toml").is_file():
            return candidate
    return start


def _guide(args: argparse.Namespace) -> int:
    from operonx import __version__, guide

    if args.path:
        print(guide.path())
        return 0
    if args.sync is not None:
        root = Path(args.sync) if args.sync else _project_root(Path.cwd().resolve())
        dest = guide.sync(root)
        print(f"operonx {__version__} guide copied to {dest}")
        return 0
    sys.stdout.write((guide.path() / "README.md").read_text(encoding="utf-8"))
    return 0


def main(argv: Optional[Sequence[str]] = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] in DELEGATED:
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
