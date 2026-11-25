"""The project's shape: six checks over the whole project, not one module.

- **C13** code in ``src/`` reaches another feature only through its
  top-level names (a split feature's ``__init__`` names) or its ``graph``:
  ``from billing import charge`` and ``billing.graph`` are fine,
  ``billing.ops`` or ``billing._rates`` is reaching inside. ``app/`` wires every feature, so it is not checked.
- **C14** every endpoint is a ``Service``: no hand-written route
  (``@app.get("/x")``, ``Route("/x", …)``, ``add_api_route``).
- **C15** the environment is read only in ``src/_shared/config.py``.
- **C16** the root holds only the allow-list below (and any ``*.md`` or
  ``*.sh``: notes and entry scripts); what a run writes goes in ``data/``
  (kept) or ``out/`` (thrown away).
- **C17** in ``web/``, only ``web/lib/`` calls ``fetch(``: pages and
  components reach the server through ``web/lib/api.js``.
- **C20** ``src/`` declares no ``Service`` or ``Job``: what a feature
  exposes is ``app/<feature>.py``. It finds an import of ``Service``,
  ``Job``, ``http``, ``asgi``, ``websocket`` (``operonx.app``) or
  ``agent_service`` (``operonx.agents``), and a call through the module
  (``app.Service(...)``). Helpers such as ``last_run`` stay allowed.

A feature is a module of ``src/`` whose name does not start with ``_``: one
file, ``src/billing.py`` (the default), or a folder, ``src/billing/``. Tests
and terminal tools (``scripts/``, ``tools/``) are not checked. Like
:mod:`operonx.lint.rules`, this reads source only (``ast``; C17 a regex).
"""

from __future__ import annotations

import ast
import re
from pathlib import Path
from typing import Iterable, Iterator, List, Set

from operonx.lint.rules import SKIP_DIRS, Finding, _allowed, _is_script, _is_test_file

__all__ = ["ROOT_ALLOWED", "lint_shape"]

#: What a project's root may hold besides dotfiles, ``*.md`` and ``*.sh`` (C16).
ROOT_ALLOWED = {
    # folders of the shape
    "app", "src", "web", "tests", "data", "datasets", "out", "docs", "deploy", "scripts", "prompts",
    "tools",
    # the project's files
    "operonx.toml", "resources.yaml", "pyproject.toml", "uv.lock", "requirements.txt",
    "README.md", "AGENTS.md", "CLAUDE.md", "LICENSE", "CHANGELOG.md", "Makefile",
    "Dockerfile", "docker-compose.yml", "compose.yaml", "Procfile",
    *SKIP_DIRS,
}  # fmt: skip

_ROUTE_METHODS = {"get", "post", "put", "patch", "delete", "route", "websocket", "api_route"}
_ROUTE_CALLS = {"Route", "WebSocketRoute", "add_api_route", "add_route", "add_websocket_route"}
_CONFIG = Path("src") / "_shared" / "config.py"
_FETCH = re.compile(r"(?<![\w.$])fetch\s*\(")
_WEB_SOURCES = {".js", ".mjs", ".ts", ".html"}
#: What declares a service or a job (C20), and the modules it comes from.
_DECLARES = {"Service", "Job", "http", "asgi", "websocket", "agent_service"}
_DECLARE_FROM = ("operonx.app", "operonx.agents", "operonx_agents")


def _features(root: Path) -> Set[str]:
    src = root / "src"
    if not src.is_dir():
        return set()
    return {
        p.stem
        for p in src.iterdir()
        if (p.is_dir() or p.suffix == ".py") and not p.name.startswith(("_", "."))
    }


def _feature_of(rel: Path) -> str:
    if len(rel.parts) < 2 or rel.parts[0] != "src":
        return ""
    part = rel.parts[1]
    return part if len(rel.parts) > 2 else part[:-3] if part.endswith(".py") else ""


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


def _from_operonx(module: str) -> bool:
    return any(module == m or module.startswith(m + ".") for m in _DECLARE_FROM)


def _c20(tree: ast.AST, path: Path) -> Iterator[Finding]:
    hint = (
        "src/ declares no Service or Job: put it in app/<feature>.py, beside the feature's others"
    )
    modules = set()  # local names bound to an operonx module: ``app`` in ``app.Service(...)``
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            modules |= {a.asname for a in n.names if a.asname and _from_operonx(a.name)}
        elif isinstance(n, ast.ImportFrom) and n.module and not n.level:
            if n.module == "operonx":
                modules |= {a.asname or a.name for a in n.names if a.name in ("app", "agents")}
            elif _from_operonx(n.module):
                names = sorted({a.name for a in n.names} & _DECLARES)
                if names:
                    yield Finding("C20", path, n.lineno, f"imports {', '.join(names)}: {hint}")
    for n in ast.walk(tree):
        f = n.func if isinstance(n, ast.Call) else None
        if not isinstance(f, ast.Attribute) or f.attr not in _DECLARES:
            continue
        base = f.value
        dotted = []
        while isinstance(base, ast.Attribute):
            dotted.append(base.attr)
            base = base.value
        if isinstance(base, ast.Name):
            module = ".".join([base.id, *reversed(dotted)])
            if base.id in modules or _from_operonx(module):
                yield Finding("C20", path, n.lineno, f"calls {f.attr}(): {hint}")


def _c16(root: Path) -> Iterator[Finding]:
    for p in sorted(root.iterdir()):
        if p.name.startswith(".") or p.name in ROOT_ALLOWED or p.name.startswith("LICENSE"):
            continue
        if p.is_file() and p.suffix in (".md", ".sh"):
            continue
        kind = "folder" if p.is_dir() else "file"
        yield Finding(
            "C16",
            p,
            1,
            f"{p.name}: a {kind} the root does not hold; code goes in src/ or app/, "
            "what runs write in data/ or out/",
        )


def _c17(root: Path) -> Iterator[Finding]:
    web = root / "web"
    if not web.is_dir():
        return
    for path in sorted(web.rglob("*")):
        rel = path.relative_to(web)
        if path.suffix not in _WEB_SOURCES or rel.parts[0] == "lib" or SKIP_DIRS & set(rel.parts):
            continue
        source = path.read_text(encoding="utf-8", errors="replace")
        allow = _allowed(source.replace("//", "#"))
        for i, line in enumerate(source.splitlines(), 1):
            if _FETCH.search(line) and "C17" not in allow.get(i, ()):
                yield Finding(
                    "C17", path, i, "calls fetch(): reach the server through web/lib/api.js"
                )


def lint_shape(root: Path, files: Iterable[Path]) -> List[Finding]:
    """C13–C17 and C20 for the project at *root*, over its Python *files* and ``web/``."""
    root = Path(root)
    features = _features(root)
    out: List[Finding] = [*_c16(root), *_c17(root)]
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
        if rel.parts[0] == "src":
            found += _c20(tree, path)
        if rel != _CONFIG:
            found += _c15(tree, path)
        allow = _allowed(source)
        out += [f for f in found if f.rule not in allow.get(f.line, ())]
    return out
