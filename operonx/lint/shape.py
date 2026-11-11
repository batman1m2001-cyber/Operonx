"""The project's shape: four checks over the whole project, not one module.

- **C13** code in ``src/`` reaches another feature only through its
  ``__init__`` names or its ``graph``: ``from billing import charge`` and
  ``billing.graph`` are fine, ``billing.ops`` or ``billing._rates`` is
  reaching inside. ``app/`` wires every feature, so it is not checked.
- **C14** every endpoint is a ``Service``: no hand-written route
  (``@app.get("/x")``, ``Route("/x", …)``, ``add_api_route``).
- **C15** the environment is read only in ``src/_shared/config.py``.
- **C16** the root holds only the allow-list below; what a run writes goes
  in ``data/`` (kept) or ``out/`` (thrown away).

A feature is a folder of ``src/`` whose name does not start with ``_``. Tests
and terminal tools (``scripts/``, ``tools/``) are not checked. Like
:mod:`operonx.lint.rules`, this reads source only (``ast``).
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Iterable, Iterator, List, Set

from operonx.lint.rules import SKIP_DIRS, Finding, _allowed, _is_script, _is_test_file

__all__ = ["ROOT_ALLOWED", "lint_shape"]

#: What a project's root may hold besides dotfiles (C16).
ROOT_ALLOWED = {
    # folders of the shape
    "app", "src", "web", "tests", "data", "datasets", "out", "docs", "deploy", "scripts",
    "tools",
    # the project's files
    "operonx.toml", "resources.yaml", "pyproject.toml", "uv.lock", "requirements.txt",
    "README.md", "AGENTS.md", "CLAUDE.md", "LICENSE", "CHANGELOG.md", "Makefile",
    *SKIP_DIRS,
}  # fmt: skip

_ROUTE_METHODS = {"get", "post", "put", "patch", "delete", "route", "websocket", "api_route"}
_ROUTE_CALLS = {"Route", "WebSocketRoute", "add_api_route", "add_route", "add_websocket_route"}
_CONFIG = Path("src") / "_shared" / "config.py"


def _features(root: Path) -> Set[str]:
    src = root / "src"
    if not src.is_dir():
        return set()
    return {p.name for p in src.iterdir() if p.is_dir() and not p.name.startswith(("_", "."))}


def _feature_of(rel: Path) -> str:
    return rel.parts[1] if len(rel.parts) > 2 and rel.parts[0] == "src" else ""


def _c13(tree: ast.AST, rel: Path, path: Path, features: Set[str]) -> Iterator[Finding]:
    own = _feature_of(rel)
    if not own:
        return  # app/ wires every feature's jobs and routes: it is not a feature
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            names = [a.name for a in n.names]
        elif isinstance(n, ast.ImportFrom) and n.module and not n.level:
            names = [n.module]
        else:
            continue
        for name in names:
            parts = name.split(".")
            if parts[0] == "src":
                parts = parts[1:]
            if len(parts) > 1 and parts[0] in features and parts[0] != own and parts[1] != "graph":
                yield Finding(
                    "C13",
                    path,
                    n.lineno,
                    f"{'.'.join(parts)} reaches inside feature {parts[0]}: import what its "
                    f"__init__ exports (from {parts[0]} import …) or its graph",
                )


def _c14(tree: ast.AST, path: Path) -> Iterator[Finding]:
    # ``@app.get("/x")`` only as a decorator: ``client.get("/x")`` is a request
    decorators = {
        id(d)
        for n in ast.walk(tree)
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
        for d in n.decorator_list
    }
    for n in ast.walk(tree):
        if not isinstance(n, ast.Call):
            continue
        f = n.func
        name = f.attr if isinstance(f, ast.Attribute) else f.id if isinstance(f, ast.Name) else ""
        first = n.args[0] if n.args else None
        is_path = (
            isinstance(first, ast.Constant)
            and isinstance(first.value, str)
            and first.value.startswith("/")
        )
        hand = name in _ROUTE_CALLS or (
            isinstance(f, ast.Attribute) and name in _ROUTE_METHODS and id(n) in decorators
        )
        if hand and is_path:
            yield Finding(
                "C14",
                path,
                n.lineno,
                f"{first.value} is a hand-written route: declare it as a Service "
                "(http(...)) so every request has a trace",
            )


def _c15(tree: ast.AST, path: Path) -> Iterator[Finding]:
    for n in ast.walk(tree):
        read = (
            isinstance(n, ast.Call)
            and isinstance(n.func, ast.Attribute)
            and (
                n.func.attr == "getenv"
                or (
                    n.func.attr == "get"
                    and isinstance(n.func.value, ast.Attribute)
                    and n.func.value.attr == "environ"
                )
            )
        ) or (
            isinstance(n, ast.Subscript)
            and isinstance(n.ctx, ast.Load)
            and isinstance(n.value, ast.Attribute)
            and n.value.attr == "environ"
        )
        if read:
            yield Finding(
                "C15",
                path,
                n.lineno,
                "reads the environment: read it once in src/_shared/config.py and import the name",
            )


def _c16(root: Path) -> Iterator[Finding]:
    for p in sorted(root.iterdir()):
        if p.name.startswith(".") or p.name in ROOT_ALLOWED or p.name.startswith("LICENSE"):
            continue
        kind = "folder" if p.is_dir() else "file"
        yield Finding(
            "C16",
            p,
            1,
            f"{p.name}: a {kind} the root does not hold; code goes in src/ or app/, "
            "what runs write in data/ or out/",
        )


def lint_shape(root: Path, files: Iterable[Path]) -> List[Finding]:
    """C13–C16 for the project at *root*, over its Python *files*."""
    root = Path(root)
    features = _features(root)
    out: List[Finding] = list(_c16(root))
    for path in files:
        rel = path.relative_to(root)
        if _is_test_file(rel) or _is_script(rel):
            continue
        try:
            source = path.read_text(encoding="utf-8")
            tree = ast.parse(source)
        except (SyntaxError, UnicodeDecodeError):
            continue  # E00 is reported by the module rules
        found = [*_c13(tree, rel, path, features), *_c14(tree, path)]
        if rel != _CONFIG:
            found += _c15(tree, path)
        allow = _allowed(source)
        out += [f for f in found if f.rule not in allow.get(f.line, ())]
    return out
