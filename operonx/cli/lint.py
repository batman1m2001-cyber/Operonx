"""``operonx lint``: check a project against the operonx conventions.

    operonx lint [PATH]            the static rules (C3, C5–C12) and the manifest (C1)
    operonx lint --build [PATH]    also load the application and build every graph offline (C1, C2)
    operonx lint --rules           what each rule checks

It exits 1 when any error is found (warnings alone exit 0), so it drops into
CI or a pre-commit hook. ``--build`` runs the project's code: run it in the
project's own environment.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import List, Optional, Sequence

from operonx.lint.rules import Finding, lint_path

__all__ = ["RULES", "main"]

#: rule → what it checks (``--rules``).
RULES = {
    "C1": "operonx.toml exists and the application loads; with --build, every graph compiles",
    "C2": "with --build, building a graph reaches no network",
    "C3": "op names come from plain assignments (auto_name), never reassigned in a graph",
    "C5": "no op built or edge wired inside a loop",
    "C6": "resource= is a literal key; credentials come from resources.yaml (warnings)",
    "C7": "no @graph defined inside a function or class",
    "C8": "no print() outside tests and terminal tools (scripts/, bench/): LOGGER",
    "C9": "no retry loop in an op: [retry] or @op(retry=...)",
    "C10": "a graph body only wires: no Python if, try, with, await or I/O; no helpers in graph.py",
    "C11": "a graph that builds ops writes START >> ... >> END",
    "C12": "every ${NAME} in resources.yaml and every credential read is listed in .env.example",
}


def _report(findings: Sequence[Finding], root: Path) -> int:
    if not findings:
        print(f"operonx lint: {root}: clean")
        return 0
    base = root if root.is_dir() else root.parent
    for f in findings:
        try:
            shown = f.file.resolve().relative_to(base.resolve())
        except ValueError:
            shown = f.file
        level = "" if f.severity == "error" else " (warning)"
        print(f"{shown}:{f.line}: [{f.rule}]{level} {f.message}")
    errors = [f for f in findings if f.severity == "error"]
    print(f"\n{len(errors)} error(s), {len(findings) - len(errors)} warning(s)")
    return 1 if errors else 0


def _manifest(root: Path) -> List[str]:
    from operonx.app.manifest import Manifest, ManifestError

    if not (root / "operonx.toml").is_file():
        return [
            "[C1] no operonx.toml: `operonx init` writes one; it points the CLIs at the application"
        ]
    try:
        Manifest.from_file(root / "operonx.toml")
    except (ManifestError, OSError, ValueError) as exc:
        return [f"[C1] operonx.toml: {exc}"]
    return []


def _report_build(root: Path) -> int:
    from operonx.lint.build import check_build

    reports = check_build(root)
    print()
    failed = 0
    for r in reports:
        if r.clean:
            note = "   [C2] slow: something heavy runs while building" if r.slow else ""
            print(f"  {r.graph:32} built offline in {r.seconds * 1000:.0f}ms{note}")
            continue
        failed += 1
        if r.network:
            print(f"  {r.graph:32} [C2] reached the network while building:")
            for attempt in r.network:
                print(f"      {attempt}")
            print("      Building must be cheap: defer this to the first run.")
        else:
            print(f"  {r.graph:32} [C1] {r.error}")
    built = [r for r in reports if r.ok]
    print(f"\n{len(built)}/{len(reports)} graph(s) build offline")
    return 1 if failed else 0


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="operonx lint",
        description="Check a project against the operonx conventions (the rules in its AGENTS.md).",
    )
    parser.add_argument(
        "path", nargs="?", default=".", help="the project, or one file (default: here)"
    )
    parser.add_argument(
        "--build",
        action="store_true",
        help="load the application and build every graph with the network blocked (C1, C2); "
        "runs the project's code, so use the project's environment",
    )
    parser.add_argument(
        "--no-manifest", action="store_true", help="skip the operonx.toml check (C1)"
    )
    parser.add_argument("--rules", action="store_true", help="list the rules and exit")
    args = parser.parse_args(argv)

    if args.rules:
        for rule, what in RULES.items():
            print(f"{rule:4} {what}")
        return 0
    root = Path(args.path)
    if not root.exists():
        print(f"operonx lint: {root} does not exist", file=sys.stderr)
        return 2
    problems = [] if (args.no_manifest or root.is_file()) else _manifest(root)
    for p in problems:
        print(f"{root}: {p}")
    rc = _report(lint_path(root), root)
    if args.build and not problems and root.is_dir():
        rc = _report_build(root) or rc
    return 1 if problems else rc


if __name__ == "__main__":
    raise SystemExit(main())
