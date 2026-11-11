"""``operonx lint``: check a project against the operonx conventions.

    operonx lint [PATH]             the static rules (C3, C5–C16) and the manifest (C1)
    operonx lint --build [PATH]     also load the application and build every graph offline (C1, C2)
    operonx lint --baseline [PATH]  record today's count per rule in operonx.toml
    operonx lint --all [PATH]       every finding, the baseline ignored
    operonx lint --rules            what each rule checks

It exits 1 when any error is found (warnings alone exit 0), so it drops into
CI or a pre-commit hook. ``--build`` runs the project's code: run it in the
project's own environment.

A project older than a rule records a baseline (``[lint] baseline = {C13 = 15}``).
A rule at or under its count is held: one line, not red. Over it, its findings
show and lint is red. Under it, re-run ``--baseline`` to lower it. Only
``--baseline`` writes; plain lint never touches the project.
"""

from __future__ import annotations

import argparse
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

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
    "C13": "another feature only through its __init__ names or its graph",
    "C14": "every endpoint is a Service: no hand-written route",
    "C15": "the environment is read only in src/_shared/config.py",
    "C16": "the root holds only the allow-list; what runs write goes in data/ or out/",
}

_BASELINE_LINE = re.compile(r"^baseline\s*=.*$\n?", re.M)


def _baseline(root: Path) -> Dict[str, int]:
    """``[lint] baseline`` of *root*'s operonx.toml: rule → count held."""
    try:
        import tomllib
    except ModuleNotFoundError:  # Python 3.10
        import tomli as tomllib  # type: ignore[no-redef]
    try:
        raw = tomllib.loads((root / "operonx.toml").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    table = raw.get("lint", {}).get("baseline", {})
    return {str(k): int(v) for k, v in table.items()} if isinstance(table, dict) else {}


def _hold(findings: List[Finding], base: Dict[str, int]) -> Tuple[List[Finding], List[str]]:
    """Drop the findings of each rule at or under its baseline; say so."""
    counts = Counter(f.rule for f in findings if f.severity == "error")
    notes, held = [], set()
    for rule, limit in sorted(base.items()):
        n = counts.get(rule, 0)
        if n > limit:
            notes.append(f"[{rule}] {n} over the baseline of {limit}: the new ones are below")
            continue
        held.add(rule)
        lower = f"; it was {limit}, run `operonx lint --baseline` to lower it" if n < limit else ""
        notes.append(f"[{rule}] {n} held by the baseline{lower}")
    return [f for f in findings if f.rule not in held], notes


def _write_baseline(root: Path, findings: Sequence[Finding]) -> int:
    toml = root / "operonx.toml"
    if not toml.is_file():
        print(f"operonx lint: {root}: no operonx.toml to hold a baseline", file=sys.stderr)
        return 2
    counts = Counter(f.rule for f in findings if f.severity == "error")
    order = sorted(counts, key=lambda r: (len(r), r))
    line = "baseline = {" + ", ".join(f"{r} = {counts[r]}" for r in order) + "}\n"
    text = toml.read_text(encoding="utf-8")
    text, n = _BASELINE_LINE.subn(line if counts else "", text, count=1)
    if not n and counts:
        text, n = re.subn(r"^\[lint\]\s*$\n?", lambda m: "[lint]\n" + line, text, 1, re.M)
        if not n:
            text = text.rstrip("\n") + "\n\n[lint]\n" + line
    text = re.sub(r"\n*^\[lint\]\n*(?=^\[|\Z)", "\n\n", text, flags=re.M).rstrip("\n") + "\n"
    toml.write_text(text, encoding="utf-8")
    held = ", ".join(f"{r} {counts[r]}" for r in order) or "nothing: the project is clean"
    print(f"operonx lint: {toml}: baseline {held}")
    return 0


def _report(findings: Sequence[Finding], root: Path, notes: Sequence[str] = ()) -> int:
    for note in notes:
        print(note)
    if not findings:
        print(f"operonx lint: {root}: clean" + (" (on its baseline)" if notes else ""))
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
    parser.add_argument(
        "--baseline",
        action="store_true",
        help="record today's error count per rule in operonx.toml ([lint] baseline)",
    )
    parser.add_argument("--all", action="store_true", help="every finding: ignore the baseline")
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
    findings = lint_path(root)
    if args.baseline:
        return _write_baseline(root, findings) if root.is_dir() else 2
    notes: List[str] = []
    if root.is_dir() and not args.all:
        findings, notes = _hold(findings, _baseline(root))
    rc = _report(findings, root, notes)
    if args.build and not problems and root.is_dir():
        rc = _report_build(root) or rc
    return 1 if problems else rc


if __name__ == "__main__":
    raise SystemExit(main())
