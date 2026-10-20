"""The architecture the practices declare, checked (PROJECT_PRACTICES_PLAN D2).

``operonx-project arch`` parses every Python file of the project with
:mod:`ast` (no dependency, nothing imported or run) and reports each
violation on a line of its own, ``FAILED arch:<rule>:<file>-><target> - why``.
That is the shape the engine's check comparison reads failing tests in, so
the check joins the milestone's baseline like any other: a violation the
baseline already has is the past, a new one fails the round.

Rules (each only when the practices declare it):

- ``layer``: layers are listed outermost first; a module imports its own
  layer and the ones after it, never one before it (the dependency rule);
- ``forbid``: a layer (or every module) never imports these modules: the
  domain's independence from frameworks;
- ``context``: a bounded context is reached from another only through its
  public modules;
- ``private``: another top-level package's ``_private`` names, imported, or a
  ``_private`` attribute of an object this file does not define.

Ids carry no line numbers, so a violation keeps its id when code above it moves.
"""

from __future__ import annotations

import ast
import fnmatch
import re
import subprocess
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Set, Tuple

from operonx_project.practices import Context, Practices

__all__ = ["Violation", "check", "python_files"]

#: Underscored names that are public API (namedtuple's), not private access.
PUBLIC_UNDERSCORE = frozenset(
    {"_replace", "_asdict", "_fields", "_make", "_field_defaults", "_", "_getframe", "_exit", "_current_frames"}
)  # namedtuple's; sys's and os's, documented
ROOTS = ("src", ".")


@dataclass(frozen=True)
class Violation:
    rule: str  # layer | forbid | context | private
    file: str
    line: int
    target: str
    message: str

    @property
    def id(self) -> str:
        return f"arch:{self.rule}:{self.file}->{self.target}"


@lru_cache(maxsize=None)
def _glob_re(pattern: str, below: bool = True) -> "re.Pattern[str]":
    """A path glob as git and .gitignore read it: ``*`` is one path segment, ``**`` any number
    (``src/**/ops.py`` is ``src/ops.py`` too), and, *below*, a pattern naming a folder covers what is in it."""
    g, out, i = pattern.strip("/"), "", 0
    while i < len(g):
        if g.startswith("**/", i):
            out, i = out + "(?:[^/]+/)*", i + 3
        elif g.startswith("**", i):
            out, i = out + ".*", i + 2
        elif g[i] == "*":
            out, i = out + "[^/]*", i + 1
        elif g[i] == "?":
            out, i = out + "[^/]", i + 1
        else:
            out, i = out + re.escape(g[i]), i + 1
    return re.compile(out + ("(?:/.*)?" if below else ""))


def _matches(path: str, globs: Iterable[str]) -> bool:
    return any(_glob_re(g).fullmatch(path) for g in globs)


def python_files(root: Path) -> List[str]:
    """The project's Python files, repo-relative: git's tracked and untracked
    ones (ignored files are not the project's), or every one when not in git."""
    root = Path(root)
    try:
        out = subprocess.run(["git", "ls-files", "--cached", "--others", "--exclude-standard", "--", "*.py"],
                             cwd=root, capture_output=True, text=True, timeout=60, check=True).stdout  # fmt: skip
        files = sorted({f for f in out.splitlines() if f and (root / f).is_file()})
    except (OSError, subprocess.SubprocessError):
        files = sorted(str(p.relative_to(root)) for p in root.rglob("*.py") if ".venv" not in p.parts)
    return files


def _module_of(path: str, roots: Iterable[str]) -> Optional[str]:
    for r in roots:
        prefix = "" if r == "." else r.rstrip("/") + "/"
        if prefix and not path.startswith(prefix):
            continue
        rel = path[len(prefix) :]
        parts = rel[:-3].split("/")
        if parts[-1] == "__init__":
            parts = parts[:-1]
        if parts and all(p.isidentifier() for p in parts):
            return ".".join(parts)
    return None


class _Index:
    """Module names ↔ the project's files."""

    def __init__(self, root: Path, files: List[str]):
        self.roots = [r for r in ROOTS if r == "." or (root / r).is_dir()]
        self.file_of: Dict[str, str] = {}
        self.module_of: Dict[str, str] = {}
        for f in files:
            m = _module_of(f, self.roots)
            if m is not None:
                self.module_of[f] = m
                self.file_of.setdefault(m, f)
                whole = _module_of(f, ["."])  # src/jobs/x.py is also src.jobs.x where the repo imports it so
                if whole and whole != m:
                    self.file_of.setdefault(whole, f)

    def resolve(self, module: str) -> Optional[str]:
        """The project file *module* is, or "" for its nearest package; None outside the project."""
        return self.file_of.get(module)


def _imports(tree: ast.AST, module: str, is_pkg: bool) -> List[Tuple[int, str, List[str]]]:
    """(line, module, names imported from it) for every import in *tree*."""
    out: List[Tuple[int, str, List[str]]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                out.append((node.lineno, a.name, []))
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            if node.level:
                pkg = module.split(".") if is_pkg else module.split(".")[:-1]
                pkg = pkg[: len(pkg) - (node.level - 1)] if node.level > 1 else pkg
                base = ".".join([*pkg, base] if base else pkg)
            out.append((node.lineno, base, [a.name for a in node.names]))
    return out


def _context(path: str, contexts: List[Context]) -> Optional[Tuple[str, str, Context]]:
    """(instance name, path inside it, the context) the file belongs to."""
    for c in contexts:
        if c.each:
            parts = path.split("/")
            for k in range(1, len(parts)):
                prefix = "/".join(parts[:k])
                if _glob_re(c.each, below=False).fullmatch(prefix):
                    return f"{c.name}:{prefix}", "/".join(parts[k:]), c
        elif _matches(path, c.paths):
            base = c.paths[0].split("*")[0].rstrip("/")
            rel = path[len(base) + 1 :] if base and path.startswith(base + "/") else path
            return c.name, rel, c
    return None


def _layer(path: str, p: Practices) -> int:
    """The layer *path* is in, or -1. A package's ``__init__.py`` is glue (it re-exports), in a layer
    only where a pattern names ``__init__`` itself: ``_*.py`` means private helpers, not it."""
    init = path.rsplit("/", 1)[-1] == "__init__.py"
    for i, layer in enumerate(p.layers):
        globs = [g for g in layer.paths if not init or "__init__" in g]
        if globs and _matches(path, globs):
            return i
    return -1


def _package_dir(path: str, roots: Iterable[str]) -> str:
    """The top-level package a file is in, as a folder: ``src/call`` for ``src/call/tts/ops.py``;
    ``src`` for ``src/__init__.py``, which is the package every ``src/`` folder is inside."""
    for r in roots:
        prefix = "" if r == "." else r.rstrip("/") + "/"
        if prefix and not path.startswith(prefix):
            continue
        head = path[len(prefix) :].split("/", 1)[0]
        return prefix.rstrip("/") or "." if head == "__init__.py" else prefix + head
    return path.split("/", 1)[0]


def _inside(path: str, folder: str) -> bool:
    return folder == "." or path == folder or path.startswith(folder + "/")


def _own_privates(tree: ast.AST) -> Set[str]:
    """``_names`` this file defines: its own classes' internals may be touched here."""
    own: Set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node.name.startswith("_"):
            own.add(node.name)
        elif isinstance(node, ast.Attribute) and isinstance(node.ctx, ast.Store) and node.attr.startswith("_"):
            if isinstance(node.value, ast.Name) and node.value.id in ("self", "cls"):
                own.add(node.attr)
        elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store) and node.id.startswith("_"):
            own.add(node.id)
    return own


def _private(name: str) -> bool:
    dunder = name.startswith("__") and name.endswith("__")
    return name.startswith("_") and not dunder and name not in PUBLIC_UNDERSCORE


def check(root: Path, p: Practices, files: Optional[List[str]] = None) -> List[Violation]:
    """Every violation of *p*'s rules in the project at *root*, in file order."""
    root = Path(root)
    files = python_files(root) if files is None else files
    index = _Index(root, files)
    found: Dict[str, Violation] = {}

    def add(v: Violation) -> None:
        found.setdefault(v.id, v)

    for path in files:
        if p.exclude and _matches(path, p.exclude):
            continue
        module = index.module_of.get(path)
        if module is None:
            continue
        try:
            tree = ast.parse((root / path).read_text(encoding="utf-8"), filename=path)
        except (SyntaxError, UnicodeDecodeError, OSError):
            continue  # the tests and the linters report a file that does not parse
        top = module.split(".")[0]
        here_pkg = _package_dir(path, index.roots)
        here_layer = _layer(path, p)
        here_ctx = _context(path, p.contexts) if p.contexts else None
        aliases: Dict[str, str] = {}  # a name this file binds by import → that module's top package
        for line, mod, names in _imports(tree, module, path.endswith("__init__.py")):
            targets = [f"{mod}.{n}" for n in names if n != "*"] if names else []
            targets.append(mod)
            for n in names:
                aliases[n] = mod.split(".")[0]
            if not names:
                aliases[mod.split(".")[0]] = mod.split(".")[0]
            # forbidden modules (in the project or not)
            for f in p.forbid:
                if f.layer and (here_layer < 0 or p.layers[here_layer].name != f.layer):
                    continue
                for t in targets:
                    if any(fnmatch.fnmatchcase(t, pat) for pat in f.imports):
                        why = f": {f.why}" if f.why else ""
                        rule = f"the {f.layer} layer must not" if f.layer else "no module may import"
                        add(Violation("forbid", path, line, t, f"{path} imports {t}, which {rule}{why}"))
                        break
            dest = next((index.resolve(t) for t in targets if index.resolve(t)), None)
            # private names of another package: in the project, another top-level package's folder
            other = (not _inside(dest, here_pkg)) if dest else (mod.split(".")[0] != top)
            if p.private == "forbid" and mod and other:
                segs = [s for s in mod.split(".") if _private(s)]
                hidden = [n for n in names if _private(n)]
                if segs or hidden:
                    t = mod if segs else f"{mod}.{hidden[0]}"
                    owner = _package_dir(dest, index.roots).rsplit("/", 1)[-1] if dest else mod.split(".")[0]
                    add(Violation("private", path, line, t, f"{path} imports {t}, private to {owner}: "
                                                            "use its public API"))  # fmt: skip
            # the project's own modules: layers and contexts
            if dest is None or dest == path:
                continue
            if here_layer >= 0:
                there = _layer(dest, p)
                if 0 <= there < here_layer:
                    add(Violation("layer", path, line, index.module_of[dest],
                                  f"{path} ({p.layers[here_layer].name}) imports {index.module_of[dest]} "
                                  f"({p.layers[there].name}): a layer imports only its own and the layers "
                                  "after it"))  # fmt: skip
            if p.contexts:
                there_ctx = _context(dest, p.contexts)
                if there_ctx and here_ctx is not None and here_ctx[0] != there_ctx[0]:
                    _, rel, c = there_ctx
                    shown = (not c.public or _matches(rel, c.public)) and not (c.internal and _matches(rel, c.internal))
                    if not shown:
                        add(Violation("context", path, line, index.module_of[dest],
                                      f"{path} reaches into {there_ctx[0]} through {dest}, which is not its public "
                                      "API"))  # fmt: skip
        if p.private == "forbid":
            own = _own_privates(tree)
            for node in ast.walk(tree):
                if not isinstance(node, ast.Attribute) or not _private(node.attr) or node.attr in own:
                    continue
                v = node.value
                if isinstance(v, ast.Name) and (v.id in ("self", "cls") or aliases.get(v.id) == top):
                    continue
                if isinstance(v, ast.Call) and isinstance(v.func, ast.Name) and v.func.id == "super":
                    continue
                obj = ast.unparse(v) if hasattr(ast, "unparse") else "an object"
                add(Violation("private", path, node.lineno, f"{obj}.{node.attr}",
                              f"{path} touches {obj}.{node.attr}, a private attribute it does not define: "
                              "use the public API"))  # fmt: skip
    return sorted(found.values(), key=lambda v: (v.file, v.line, v.rule))
