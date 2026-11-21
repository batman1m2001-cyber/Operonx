"""``operonx prompt``: the project's prompts, their versions and labels.

    operonx prompt list                      every prompt, its versions and labels
    operonx prompt show NAME [--version N]   the text an op is given
    operonx prompt publish NAME [--label L]  freeze the working text as the next version
    operonx prompt label NAME LABEL VERSION  point a label at a version (the rollback)
    operonx prompt diff NAME [A] [B]         two versions; default: newest version → working text
    operonx prompt log NAME                  its versions, and which label is on each

A prompt is ``prompts/NAME.md``; see ``operonx.prompts``. Versions are files
under ``prompts/.versions/`` and labels are ``prompts/labels.toml``: commit
them, and the history is the repository's.
"""

from __future__ import annotations

import argparse
import difflib
import sys
from pathlib import Path
from typing import List, Optional, Sequence

from operonx import prompt_store
from operonx import prompts as P

__all__ = ["main"]


def _root(start: Optional[str]) -> Path:
    here = Path(start or ".").resolve()
    return next((p for p in (here, *here.parents) if (p / "operonx.toml").is_file()), here)


def _on(name: str, root: Path) -> dict:
    """{version: [labels]} for *name*."""
    out: dict = {}
    for label, version in P.labels(root).get(name, {}).items():
        out.setdefault(version, []).append(label)
    return out


def _pushed(root: Path, *, must: bool) -> List[str]:
    """Copy to the prompt store and say what moved. A publish or a label in a
    project with no store set says nothing (*must* false)."""
    if not prompt_store.configured(root):
        if must:
            raise prompt_store.StoreError(
                "no prompt store is set ([prompts] store in operonx.toml, or OPERONX_PROMPT_STORE)"
            )
        return []
    got = prompt_store.push(root)
    lines = [
        f"store ({got['project']}): {len(got['versions'])} new version(s), {len(got['labels'])} label(s) moved"
    ]
    lines += [f"  + {n} v{v}" for n, v in got["versions"]]
    lines += [
        f"  {n}: {lab} = {v}" + (f" (was {b})" if b is not None else "")
        for n, lab, v, b in got["labels"]
    ]
    return lines


def _text(name: str, version: Optional[str], root: Path) -> str:
    if version in (None, "working"):
        return P._read(P.prompts_dir(root), name, None)
    return P._read(P.prompts_dir(root), name, int(version))


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="operonx prompt",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--project", default=None, help="the project (default: the one at or above here)"
    )
    sub = parser.add_subparsers(dest="cmd")
    sub.add_parser("list")
    show = sub.add_parser("show")
    show.add_argument("name")
    show.add_argument("--version", default=None)
    show.add_argument("--label", default=None)
    pub = sub.add_parser("publish")
    pub.add_argument("name")
    pub.add_argument(
        "--label", action="append", default=[], help="also point this label at the new version"
    )
    lab = sub.add_parser("label")
    lab.add_argument("name")
    lab.add_argument("label")
    lab.add_argument("version", type=int)
    diff = sub.add_parser("diff")
    diff.add_argument("name")
    diff.add_argument("a", nargs="?", default=None)
    diff.add_argument("b", nargs="?", default=None)
    log = sub.add_parser("log")
    log.add_argument("name")
    sub.add_parser("push", help="copy the published versions and labels to the prompt store")
    sub.add_parser("store", help="what the prompt store serves for this project")
    args = parser.parse_args(list(argv) if argv is not None else None)
    root = _root(args.project)
    out: List[str] = []
    try:
        if args.cmd == "list":
            names = P.prompt_names(root)
            if not names:
                out.append(f"no prompts: {P.prompts_dir(root)} has no .md files")
            for name in names:
                have, on = P.versions(name, root), _on(name, root)
                marks = ", ".join(f"{label} = {v}" for v in sorted(on) for label in sorted(on[v]))
                out.append(
                    f"{name:<32} {len(have)} version{'' if len(have) == 1 else 's'}"
                    + (f"   {marks}" if marks else "")
                )
        elif args.cmd == "show":
            if args.version is not None:
                out.append(P.prompt(args.name, version=int(args.version), root=root).text)
            else:
                out.append(P.prompt(args.name, label=args.label, root=root).text)
        elif args.cmd == "publish":
            number, new = P.publish(args.name, root)
            out.append(
                f"{args.name}: version {number}"
                + ("" if new else " (unchanged since it was published)")
            )
            for label in args.label:
                before = P.set_label(args.name, label, number, root)
                out.append(
                    f"  {label} = {number}"
                    + (f" (was {before})" if before not in (None, number) else "")
                )
        elif args.cmd == "label":
            before = P.set_label(args.name, args.label, args.version, root)
            out.append(
                f"{args.name}: {args.label} = {args.version}"
                + (f" (was {before})" if before is not None else "")
            )
        elif args.cmd == "diff":
            have = P.versions(args.name, root)
            a = args.a or (str(have[-1]) if have else None)
            b = args.b or "working"
            if a is None:
                out.append(f"{args.name} has no published version to compare with")
            else:
                lines = difflib.unified_diff(
                    _text(args.name, a, root).splitlines(),
                    _text(args.name, b, root).splitlines(),
                    f"{args.name} @ {a}",
                    f"{args.name} @ {b}",
                    lineterm="",
                )
                out += list(lines) or [f"{args.name}: {a} and {b} are the same"]
        elif args.cmd == "log":
            have, on = P.versions(args.name, root), _on(args.name, root)
            P._read(P.prompts_dir(root), args.name, None)  # a name the project does not have
            if not have:
                out.append(f"{args.name}: never published — the working text is what is served")
            for v in reversed(have):
                size = len(_text(args.name, str(v), root))
                out.append(
                    f"{v:>4}  {size:>6} chars"
                    + (f"   ← {', '.join(sorted(on[v]))}" if v in on else "")
                )
        elif args.cmd == "push":
            out += _pushed(root, must=True)
        elif args.cmd == "store":
            got = prompt_store.status(root)
            if not got["configured"]:
                out.append(
                    "no prompt store is set ([prompts] store in operonx.toml): prompts are served from the files"
                )
            else:
                out.append(
                    f"project {got['project']!r}"
                    + (f" — not read: {got['error']}" if got["error"] else "")
                )
                mine = P.labels(root)
                for row in got["serving"]:
                    here = mine.get(row["name"], {}).get(row["label"])
                    out.append(
                        f"  {row['name']:<32} {row['label']} = {row['version']}"
                        + (
                            ""
                            if here == row["version"]
                            else f"   (files: {here if here is not None else 'no such label'})"
                        )
                    )
                if not got["serving"] and not got["error"]:
                    out.append("  nothing pushed yet (operonx prompt push)")
        else:
            parser.print_help()
            return 2
        if args.cmd in ("publish", "label"):
            out += _pushed(root, must=False)
    except prompt_store.StoreError as exc:
        print("\n".join(out), end="\n" if out else "")
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except (P.PromptNotFound, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print("\n".join(out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
