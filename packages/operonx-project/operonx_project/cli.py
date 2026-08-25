"""``operonx-lint`` and ``operonx-extract``.

    operonx-lint [PATH]              check PATH (default: cwd)
    operonx-lint --build [PATH]      also build each graph offline (C1, C2)
    operonx-lint --suggest [PATH]    draft an operonx.toml for PATH
    operonx-new [PATH]               scaffold a new project
    operonx-extract [PATH]           build PATH's graphs and emit Project IR

``operonx-lint`` exits 1 when any error-severity finding is reported, so it
drops straight into CI or a pre-commit hook.
"""

from __future__ import annotations

import argparse
import ast
import sys
from pathlib import Path
from typing import List, Sequence

from operonx_project.lint import SKIP_DIRS, Finding, lint_path
from operonx_project.manifest import MANIFEST_NAME, Manifest, ManifestError

__all__ = ["main", "extract_main", "new_main", "suggest_manifest"]


def _decorators(node: ast.AST) -> set[str]:
    out = set()
    for d in getattr(node, "decorator_list", []):
        f = d.func if isinstance(d, ast.Call) else d
        if isinstance(f, ast.Name):
            out.add(f.id)
        elif isinstance(f, ast.Attribute):
            out.add(f.attr)
    return out


def _returns_a_graph(fn: ast.AST) -> bool:
    """A builder: a plain function defining a nested @graph and returning it.

    This is the shape a project reaches for when a graph must be
    parameterised at build time — callbot's ``build_ws_callbot_pipeline``.
    The tutorial has none, so it is invisible unless we look for it.
    """
    if _decorators(fn) & {"op", "graph"}:
        return False
    nested = {
        n.name
        for n in ast.walk(fn)
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and "graph" in _decorators(n)
    }
    if not nested:
        return False
    return any(
        isinstance(n, ast.Return) and isinstance(n.value, ast.Name) and n.value.id in nested
        for n in ast.walk(fn)
    )


def suggest_manifest(root: Path) -> str:
    """Draft an ``operonx.toml`` for an existing project.

    Entries are *candidates*, not an inventory — the point is to give an
    author something to prune. Sub-graphs used only as components should be
    deleted from the draft; the extractor finds them by walking the built
    graph.
    """
    name = root.resolve().name.replace("_", "-")
    lines = [
        "# Draft — prune it. Entries are graphs you want to load or run",
        "# standalone; components are discovered automatically.",
        "",
        "[project]",
        f'name = "{name}"',
    ]
    if (root / "resources.yaml").exists():
        lines += ["", "[resources]", 'overlay = "resources.yaml"']

    for py in sorted(root.rglob("*.py")):
        if any(p in SKIP_DIRS for p in py.parts):
            continue
        try:
            tree = ast.parse(py.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        rel = py.relative_to(root)
        module = ".".join(rel.with_suffix("").parts)
        for node in tree.body:
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if "graph" in _decorators(node):
                lines += ["", "[[graph]]", f'name  = "{node.name}"', f'entry = "{module}:{node.name}"']
            elif _returns_a_graph(node):
                params = [a.arg for a in node.args.args]
                lines += ["", "[[graph]]", f'name  = "{node.name}"', f'entry = "{module}:{node.name}"']
                if params:
                    lines.append("[graph.bind]")
                    lines += [f'{p} = "module:provide_{p}"   # zero-arg provider' for p in params]
    return "\n".join(lines) + "\n"


def _report(findings: Sequence[Finding], root: Path) -> int:
    if not findings:
        print(f"operonx-lint: {root} — clean")
        return 0
    for f in findings:
        try:
            shown = f.file.relative_to(root)
        except ValueError:
            shown = f.file
        print(f"{shown}:{f.line}: [{f.rule}] {f.message}")
    errors = [f for f in findings if f.severity == "error"]
    warnings = len(findings) - len(errors)
    print(f"\n{len(errors)} error(s), {warnings} warning(s)")
    return 1 if errors else 0


def _report_build(root: Path) -> int:
    """C1 + C2: every graph constructs, and does so without the network."""
    from operonx_project.buildcheck import check_build

    try:
        reports = check_build(Manifest.load(root))
    except ManifestError as exc:
        print(f"[C1] {exc}")
        return 1

    print()
    failed = 0
    for r in reports:
        if r.clean:
            note = ""
            if r.slow:
                note = "   [C2] slow — something heavy runs at construction"
            print(f"  {r.graph:32} built offline in {r.seconds * 1000:.0f}ms{note}")
            continue
        failed += 1
        if r.network:
            print(f"  {r.graph:32} [C2] reached the network while building:")
            for attempt in r.network:
                print(f"      {attempt}")
            print("      Construction must be cheap — defer this to first use.")
        else:
            print(f"  {r.graph:32} [C1] {r.error}")
    slowest = max((r.seconds for r in reports if r.ok), default=0.0)
    slow = [r for r in reports if r.slow]
    print(f"\n{len(reports) - failed}/{len(reports)} graph(s) build offline; "
          f"slowest {slowest * 1000:.0f}ms")
    if slow:
        print(
            f"{len(slow)} slow build(s). A cached load is paid once by whichever "
            f"graph builds first, so the rest look cheap and are not — move the "
            f"work to first use."
        )
    return 1 if failed else 0


def _check_manifest(root: Path) -> List[str]:
    path = root / MANIFEST_NAME
    if not path.exists():
        return [f"[C1] no {MANIFEST_NAME} — a project must declare its entry graphs"]
    try:
        Manifest.load(root)
    except ManifestError as exc:
        return [f"[C1] {exc}"]
    return []


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="operonx-lint", description=__doc__)
    parser.add_argument("path", nargs="?", default=".", help="project root (default: cwd)")
    parser.add_argument("--suggest", action="store_true", help="draft an operonx.toml and exit")
    parser.add_argument(
        "--build",
        action="store_true",
        help="build each declared graph with the network blocked (C1, C2). "
        "Executes project code, so run it in the project's own environment.",
    )
    parser.add_argument("--no-manifest", action="store_true", help="skip the C1 manifest check")
    args = parser.parse_args(argv)

    root = Path(args.path)
    if not root.exists():
        print(f"operonx-lint: {root} does not exist", file=sys.stderr)
        return 2

    if args.suggest:
        sys.stdout.write(suggest_manifest(root))
        return 0

    problems = [] if (args.no_manifest or root.is_file()) else _check_manifest(root)
    for p in problems:
        print(f"{root}: {p}")
    rc = _report(lint_path(root), root)
    if args.build and not problems:
        rc = _report_build(root) or rc
    return 1 if problems else rc


def extract_main(argv: Sequence[str] | None = None) -> int:
    """``operonx-extract`` — build a project's graphs and print the IR.

    Output is deterministic: keys sorted, no timestamps, no object ids. Two
    runs over unchanged source must produce byte-identical JSON, which is
    what makes the IR safe to cache and to diff.
    """
    import json

    from operonx_project.extract import ExtractError, extract_project

    parser = argparse.ArgumentParser(prog="operonx-extract", description=extract_main.__doc__)
    parser.add_argument("path", nargs="?", default=".", help="project root (default: cwd)")
    parser.add_argument("-o", "--output", help="write here instead of stdout")
    parser.add_argument("--compact", action="store_true", help="no indentation")
    args = parser.parse_args(argv)

    root = Path(args.path)
    try:
        ir = extract_project(Manifest.load(root))
    except (ManifestError, ExtractError) as exc:
        print(f"operonx-extract: {exc}", file=sys.stderr)
        return 1

    text = json.dumps(ir, sort_keys=True, indent=None if args.compact else 2) + "\n"
    if args.output:
        Path(args.output).write_text(text, encoding="utf-8")
        graphs = len(ir["graphs"])
        nodes = sum(len(g["nodes"]) for g in ir["graphs"])
        print(f"operonx-extract: {graphs} graph(s), {nodes} node(s) -> {args.output}")
    else:
        sys.stdout.write(text)
    return 0


def new_main(argv: Sequence[str] | None = None) -> int:
    """``operonx-new`` — scaffold a project that already lints clean."""
    from operonx_project.scaffold import ScaffoldError, scaffold

    parser = argparse.ArgumentParser(prog="operonx-new", description=new_main.__doc__)
    parser.add_argument("path", nargs="?", default=".", help="target directory (default: cwd)")
    parser.add_argument("--name", help="project name (default: the directory name)")
    parser.add_argument(
        "--llm", action="store_true", help="include an LLMOp, resources.yaml and .env.example"
    )
    args = parser.parse_args(argv)

    try:
        written = scaffold(Path(args.path), args.name, with_llm=args.llm)
    except ScaffoldError as exc:
        print(f"operonx-new: {exc}", file=sys.stderr)
        return 1

    root = Path(args.path)
    for path in written:
        print(f"  created {path}")
    print(
        f"\nNext:\n"
        f"  cd {root} && uv sync\n"
        f"  operonx-lint --build .\n"
        f"  operonx-studio . --serve"
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
