"""The static rules: what an operonx project's conventions look like in source.

Each rule is decided from one module's AST (C12 from the project's files).
They mirror how operonx behaves at runtime, or a rule of the project's
``AGENTS.md`` that a reviewer would otherwise have to remember:

- **C3** reproduces ``operonx.core.utils.auto_name``: an op's name comes
  from a plain assignment, and tuple unpacking, augmented assignment or an
  attribute/subscript target give it a per-process UUID instead.
- **C5** ops built or edges wired inside loops: no stable node to map back to.
- **C6** resources and credentials reached in ways a tool cannot resolve.
- **C7** a ``@graph`` defined inside a function (a graph factory).
- **C8** ``print()`` outside tests and terminal tools (``scripts/``, ``bench/``): log with ``LOGGER``.
- **C9** a retry loop in an op: retry is ``[retry]`` / ``@op(retry=…)``.
- **C10** logic in a graph's body: a Python ``if``, ``try``, ``with``,
  ``await`` or I/O; and helper functions in ``graph.py``.
- **C11** a graph that builds ops without writing ``START >> … >> END``.
- **C12** an environment name used by ``resources.yaml`` or read as a
  credential that ``.env.example`` does not list.

A line can opt out of a rule with ``# operonx: allow[C8]`` (several:
``allow[C8,C10]``), for the rare place a rule is wrong on purpose.

This module is dependency-free (``ast`` only), so it runs in any project's
environment without importing the project.
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Iterator, List, Set

__all__ = ["Finding", "SKIP_DIRS", "lint_env", "lint_path", "lint_source"]

SKIP_DIRS = {
    "__pycache__",
    ".venv",
    "venv",
    ".git",
    "build",
    "dist",
    "site",
    "node_modules",
    ".operonx",
}

# a credential's name ends with one of these (LLM_API_KEY, VAULT_TOKEN), so a
# token budget (AGENT_DAILY_TOKENS) or a path (MF_SECRETS_DIR) is not one
_SECRET_SUFFIXES = ("KEY", "TOKEN", "SECRET", "PASSWORD", "PASSWD", "CREDENTIALS", "CREDENTIAL")

_LOOPS = (
    ast.For,
    ast.AsyncFor,
    ast.While,
    ast.ListComp,
    ast.SetComp,
    ast.DictComp,
    ast.GeneratorExp,
)

_ALLOW = re.compile(r"#\s*operonx:\s*allow\[([A-Za-z0-9_,\s]+)\]")


@dataclass(frozen=True)
class Finding:
    """One convention violation, located in source."""

    rule: str
    file: Path
    line: int
    message: str
    severity: str = "error"

    def __str__(self) -> str:
        return f"{self.file}:{self.line}: [{self.rule}] {self.message}"


# ── decorator helpers ────────────────────────────────────────────────────


def _decorator_names(node: ast.AST) -> Set[str]:
    out: Set[str] = set()
    for d in getattr(node, "decorator_list", []):
        f = d.func if isinstance(d, ast.Call) else d
        if isinstance(f, ast.Name):
            out.add(f.id)
        elif isinstance(f, ast.Attribute):
            out.add(f.attr)
    return out


def _is_op_def(node: ast.AST) -> bool:
    return isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and "op" in _decorator_names(
        node
    )


def _is_graph_def(node: ast.AST) -> bool:
    return isinstance(
        node, (ast.FunctionDef, ast.AsyncFunctionDef)
    ) and "graph" in _decorator_names(node)


def _graphs(tree: ast.AST) -> Iterator[ast.FunctionDef]:
    for n in ast.walk(tree):
        if _is_graph_def(n):
            yield n  # type: ignore[misc]


def _is_test_file(path: Path) -> bool:
    name = path.name
    return (
        name.startswith("test_")
        or name.endswith("_test.py")
        or name == "conftest.py"
        or "tests" in path.parts
    )


_TOOL_DIRS = {"scripts", "bench", "benchmarks", "tools"}


def _is_script(path: Path) -> bool:
    """A tool a person runs at a terminal (``scripts/``, ``bench/``): its output is the point."""
    return bool(_TOOL_DIRS & set(path.parts))


# ── op-construction detection ────────────────────────────────────────────


class _ModuleFacts:
    """Names defined in this module that construct an op when called."""

    def __init__(self, tree: ast.Module):
        self.op_names: Set[str] = set()
        self.graph_names: Set[str] = set()
        for n in ast.walk(tree):
            if _is_op_def(n):
                self.op_names.add(n.name)  # type: ignore[attr-defined]
            elif _is_graph_def(n):
                self.graph_names.add(n.name)  # type: ignore[attr-defined]

    def constructs_op(self, node: ast.AST) -> bool:
        """True if calling *node* yields an op instance.

        Covers three idioms: an ``*Op`` class, its ``.of()`` shorthand, and a
        call to a locally-defined ``@op`` / ``@graph``. Cross-module ops are
        invisible here: a lint sees one file at a time, and a false negative
        is far better than a false positive on an unrelated call.
        """
        if not isinstance(node, ast.Call):
            return False
        f = node.func
        if isinstance(f, ast.Attribute):
            if f.attr == "of":
                return isinstance(f.value, ast.Name) and f.value.id.endswith("Op")
            return f.attr.endswith("Op")
        if isinstance(f, ast.Name):
            return f.id.endswith("Op") or f.id in self.op_names or f.id in self.graph_names
        return False


def _has_kwarg(call: ast.Call, name: str) -> bool:
    return any(k.arg == name for k in call.keywords)


def _clean_assignment_values(tree: ast.Module) -> Set[int]:
    """ids of Call nodes that ``auto_name`` would successfully name: a single
    ``Name`` target, or an annotated assignment to a ``Name``."""
    ok: Set[int] = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Assign) and len(n.targets) == 1 and isinstance(n.targets[0], ast.Name):
            ok.add(id(n.value))
        elif (
            isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name) and n.value is not None
        ):
            ok.add(id(n.value))
    return ok


# ── C3, C5, C6 (from operonx-studio's operonx-lint) ──────────────────────


def _c3_unstable_names(tree: ast.Module, facts: _ModuleFacts, path: Path) -> Iterator[Finding]:
    """Scoped to ``@graph`` bodies, where a name becomes node identity."""
    clean = _clean_assignment_values(tree)
    for fn in _graphs(tree):
        for n in ast.walk(fn):
            if not facts.constructs_op(n):
                continue
            if id(n) in clean or _has_kwarg(n, "name"):  # type: ignore[arg-type]
                continue
            yield Finding(
                "C3",
                path,
                n.lineno,  # type: ignore[attr-defined]
                f"op built inside @graph {fn.name} where auto_name() cannot recover a "
                f"name, so it gets a per-process UUID: assign it to a plain variable "
                f"or pass name=",
            )


def _c3_duplicate_names(tree: ast.Module, path: Path) -> Iterator[Finding]:
    """Two ops sharing a name in one graph: the second silently overwrites."""
    for fn in _graphs(tree):
        seen: dict = {}
        for stmt in ast.walk(fn):
            if isinstance(stmt, ast.Assign) and len(stmt.targets) == 1:
                tgt = stmt.targets[0]
                if isinstance(tgt, ast.Name):
                    if tgt.id in seen:
                        yield Finding(
                            "C3",
                            path,
                            stmt.lineno,
                            f"'{tgt.id}' is reassigned inside @graph {fn.name}: if both are "
                            f"ops the second silently overwrites the first",
                        )
                    seen[tgt.id] = stmt.lineno


def _iterates_static_ops(node: ast.AST) -> bool:
    """True for ``for leaf in (ex, gd, av, fl):``, a literal of local names:
    the wiring is still fully determined at build time."""
    it = getattr(node, "iter", None)
    if not isinstance(it, (ast.Tuple, ast.List, ast.Set)):
        return False
    return bool(it.elts) and all(isinstance(e, ast.Name) for e in it.elts)


def _c5_dynamic_wiring(tree: ast.Module, facts: _ModuleFacts, path: Path) -> Iterator[Finding]:
    for fn in _graphs(tree):
        for node in ast.walk(fn):
            if not isinstance(node, _LOOPS):
                continue
            static = _iterates_static_ops(node)
            for sub in ast.walk(node):
                if facts.constructs_op(sub):
                    yield Finding(
                        "C5",
                        path,
                        sub.lineno,  # type: ignore[attr-defined]
                        f"op built inside a loop in @graph {fn.name}: a diagram has no "
                        f"stable node to map back to; build it outside the loop",
                    )
                elif isinstance(sub, ast.BinOp) and isinstance(sub.op, ast.RShift):
                    if static:
                        yield Finding(
                            "C5",
                            path,
                            sub.lineno,
                            f"edges wired by looping over a literal in @graph {fn.name}: "
                            f"extraction is fine, but the UI cannot add a node here "
                            f"without editing the tuple",
                            severity="warning",
                        )
                    else:
                        yield Finding(
                            "C5",
                            path,
                            sub.lineno,
                            f"edges wired inside a loop in @graph {fn.name}: the topology "
                            f"is not knowable without running the loop",
                        )


def _is_resource_literal(node: ast.AST) -> bool:
    """One key, or a list of keys (load balancing across declared resources)."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return True
    if isinstance(node, (ast.List, ast.Tuple)):
        return bool(node.elts) and all(
            isinstance(e, ast.Constant) and isinstance(e.value, str) for e in node.elts
        )
    return False


def _env_reads(tree: ast.AST) -> Iterator[tuple]:
    """``(name, line)`` for ``os.getenv("X")``, ``os.environ.get("X")`` and
    ``os.environ["X"]``."""
    for n in ast.walk(tree):
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.args:
            arg = n.args[0]
            if not (isinstance(arg, ast.Constant) and isinstance(arg.value, str)):
                continue
            f = n.func
            if f.attr == "getenv" or (
                f.attr == "get" and isinstance(f.value, ast.Attribute) and f.value.attr == "environ"
            ):
                yield arg.value, n.lineno
        elif (
            isinstance(n, ast.Subscript)
            and isinstance(n.value, ast.Attribute)
            and n.value.attr == "environ"
        ):
            key = n.slice
            if isinstance(key, ast.Constant) and isinstance(key.value, str):
                yield key.value, n.lineno


def _is_secret(name: str) -> bool:
    return name.upper().rsplit("_", 1)[-1] in _SECRET_SUFFIXES


def _c6_resource_access(tree: ast.Module, facts: _ModuleFacts, path: Path) -> Iterator[Finding]:
    for n in ast.walk(tree):
        if facts.constructs_op(n):
            for kw in n.keywords:  # type: ignore[attr-defined]
                if kw.arg == "resource" and not _is_resource_literal(kw.value):
                    # deliberate injection is allowed (a graph kept agent-agnostic);
                    # what is lost is repointing the op from a tool
                    yield Finding(
                        "C6",
                        path,
                        n.lineno,  # type: ignore[attr-defined]
                        "resource= is not a string literal, so it is only knowable after "
                        "the graph is built: the UI cannot repoint this op without editing "
                        "whatever supplies the value",
                        severity="warning",
                    )
    for name, line in _env_reads(tree):
        if _is_secret(name):
            yield Finding(
                "C6",
                path,
                line,
                f"credential {name!r} read directly: declare it as ${{{name}}} in resources.yaml",
                severity="warning",
            )


# ── C7–C11 ───────────────────────────────────────────────────────────────


def _c7_nested_graph(tree: ast.Module, path: Path) -> Iterator[Finding]:
    """A ``@graph`` inside a function or a class: a graph factory."""

    def visit(node: ast.AST, inside: str) -> Iterator[Finding]:
        for child in ast.iter_child_nodes(node):
            if _is_graph_def(child) and inside:
                yield Finding(
                    "C7",
                    path,
                    child.lineno,  # type: ignore[attr-defined]
                    f"@graph {child.name} is defined inside {inside}: define every graph at "  # type: ignore[attr-defined]
                    f"module level, settings as graph inputs",
                )
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                yield from visit(child, inside or f"function {child.name}")
            elif isinstance(child, ast.ClassDef):
                yield from visit(child, inside or f"class {child.name}")
            else:
                yield from visit(child, inside)

    yield from visit(tree, "")


def _c8_print(tree: ast.Module, path: Path) -> Iterator[Finding]:
    if _is_test_file(path) or _is_script(path):
        return
    for n in ast.walk(tree):
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "print":
            yield Finding(
                "C8",
                path,
                n.lineno,
                "print(): log with `from operonx.core.loggings import LOGGER` instead",
            )


_SLEEPS = {"sleep"}
_RETRY_WORDS = ("attempt", "retry", "retries", "tries", "try_")


def _calls_named(node: ast.AST, names: Set[str]) -> bool:
    for n in ast.walk(node):
        if isinstance(n, ast.Call):
            f = n.func
            name = (
                f.attr if isinstance(f, ast.Attribute) else f.id if isinstance(f, ast.Name) else ""
            )
            if name in names:
                return True
    return False


def _loop_names(loop: ast.AST) -> str:
    target = getattr(loop, "target", None)
    words = (
        [n.id for n in ast.walk(target) if isinstance(n, ast.Name)] if target is not None else []
    )
    test = getattr(loop, "test", None)
    if test is not None:
        words += [n.id for n in ast.walk(test) if isinstance(n, ast.Name)]
    return " ".join(words).lower()


def _is_bookkeeping(stmt: ast.stmt) -> bool:
    """A statement a retry loop carries besides its try: a pause, a counter,
    a log line, a break."""
    if isinstance(stmt, (ast.Pass, ast.Break, ast.Continue, ast.AugAssign)):
        return True
    if isinstance(stmt, ast.Expr):
        v = stmt.value.value if isinstance(stmt.value, ast.Await) else stmt.value
        if isinstance(v, ast.Call):
            f = v.func
            name = (
                f.attr if isinstance(f, ast.Attribute) else f.id if isinstance(f, ast.Name) else ""
            )
            owner = (
                f.value.id if isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name) else ""
            )
            return name in _SLEEPS or owner.upper() in ("LOGGER", "LOG", "LOGGING")
    if isinstance(stmt, ast.Assign) and isinstance(stmt.value, (ast.Constant, ast.Name, ast.BinOp)):
        return True
    return False


def _c9_retry_loop(tree: ast.Module, path: Path) -> Iterator[Finding]:
    """A loop in an op that catches an error and goes round again."""
    for fn in ast.walk(tree):
        if not _is_op_def(fn):
            continue
        for loop in ast.walk(fn):
            if not isinstance(loop, (ast.For, ast.AsyncFor, ast.While)):
                continue
            tries = [
                t
                for stmt in loop.body
                for t in ast.walk(stmt)
                if isinstance(t, ast.Try) and t.handlers
            ]
            if not tries:
                continue
            # a retry goes round the same work again; a loop over items that
            # skips a bad one (`for row in rows: try … except: continue`) does not
            handler_sleeps = any(_calls_named(h, _SLEEPS) for t in tries for h in t.handlers)
            repeats = isinstance(loop, ast.While) or (
                isinstance(loop.iter, ast.Call)  # type: ignore[union-attr]
                and isinstance(loop.iter.func, ast.Name)  # type: ignore[union-attr]
                and loop.iter.func.id == "range"  # type: ignore[union-attr]
            )
            exits_on_success = any(
                isinstance(s, (ast.Break, ast.Return))
                for t in tries
                for part in (t.body, t.orelse)
                for s in part
            ) or any(isinstance(s, (ast.Break, ast.Return)) for s in loop.body)
            named = any(w in _loop_names(loop) for w in _RETRY_WORDS)
            # a timer tick (`while …: try: await wait_for(…) except Timeout: pass`
            # and then the real work) is not a retry: the try must be the
            # loop's whole job
            only_try = all(isinstance(st, ast.Try) or _is_bookkeeping(st) for st in loop.body)
            if handler_sleeps or (repeats and (named or (exits_on_success and only_try))):
                yield Finding(
                    "C9",
                    path,
                    loop.lineno,
                    f"a retry loop in @op {fn.name}: retry with [retry] in operonx.toml or "  # type: ignore[attr-defined]
                    f"@op(retry=Retry(...)), and give a side effect @op(retry=False)",
                )


_IO_MODULES = {
    "os",
    "subprocess",
    "requests",
    "httpx",
    "socket",
    "shutil",
    "urllib",
    "json",
    "sqlite3",
    "time",
}
_IO_CALLS = {"open", "print", "input"}
_IO_METHODS = {"read_text", "write_text", "read_bytes", "write_bytes", "mkdir", "unlink"}


def _io_call(n: ast.AST) -> str:
    if not isinstance(n, ast.Call):
        return ""
    f = n.func
    if isinstance(f, ast.Name) and f.id in _IO_CALLS:
        return f.id + "()"
    if isinstance(f, ast.Attribute):
        root = f.value
        while isinstance(root, ast.Attribute):
            root = root.value
        if isinstance(root, ast.Name) and root.id in _IO_MODULES:
            return f"{root.id}.{f.attr}()"
        if f.attr in _IO_METHODS:
            return f".{f.attr}()"
    return ""


def _c10_logic_in_graph(tree: ast.Module, path: Path) -> Iterator[Finding]:
    """A graph's body is wiring: ops, edges and ``if_`` branches."""
    for fn in _graphs(tree):
        for n in ast.walk(fn):
            if n is fn:
                continue
            what = ""
            if isinstance(n, (ast.If, ast.IfExp)):
                what = "a Python `if` (branch with if_(cond, a).else_(b) in the >> chain)"
            elif isinstance(n, ast.Try):
                what = "a `try` (an op's error is in the run's $errors)"
            elif isinstance(n, (ast.With, ast.AsyncWith)):
                what = "a `with` block"
            elif isinstance(n, ast.Await):
                what = "an `await`"
            elif _io_call(n):
                what = f"I/O ({_io_call(n)})"
            if what:
                yield Finding(
                    "C10",
                    path,
                    n.lineno,  # type: ignore[attr-defined]
                    f"{what} in @graph {fn.name}: a graph only wires ops; the logic goes in ops.py",
                )
    if path.name == "graph.py":
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and not (
                _is_graph_def(node) or _is_op_def(node)
            ):
                yield Finding(
                    "C10",
                    path,
                    node.lineno,
                    f"function {node.name} in graph.py: graph.py holds wiring only; move it to ops.py "
                    f"or a _helper module",
                    severity="warning",
                )


def _names_in(node: ast.AST) -> Set[str]:
    out: Set[str] = set()
    for n in ast.walk(node):
        if isinstance(n, ast.Name):
            out.add(n.id)
        elif isinstance(n, ast.Attribute):
            out.add(n.attr)
    return out


def _c11_explicit_edges(tree: ast.Module, path: Path) -> Iterator[Finding]:
    """A graph that builds ops writes its edges out: ``START >> a >> END``."""
    for fn in _graphs(tree):
        builds = any(
            isinstance(s, (ast.Assign, ast.AnnAssign))
            and isinstance(getattr(s, "value", None), ast.Call)
            for s in ast.walk(fn)
        )
        if not builds:
            continue
        chains = [
            n for n in ast.walk(fn) if isinstance(n, ast.BinOp) and isinstance(n.op, ast.RShift)
        ]
        seen: Set[str] = set()
        for c in chains:
            seen |= _names_in(c)
        missing = [w for w in ("START", "END") if w not in seen]
        if missing:
            yield Finding(
                "C11",
                path,
                fn.lineno,
                f"@graph {fn.name} builds ops but never wires {' or '.join(missing)}: write the "
                f"edges out, START >> … >> END",
            )


# ── C12: the environment contract ────────────────────────────────────────

_ENV_REF = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::[^}]*)?\}")
_ENV_LISTED = re.compile(r"^\s*#?\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=", re.M)


def _yaml_env_names(text: str) -> List[tuple]:
    out = []
    for i, line in enumerate(text.splitlines(), 1):
        code = line.split("#", 1)[0]
        for m in _ENV_REF.finditer(code):
            out.append((m.group(1), i))
    return out


def lint_env(root: Path, sources: Iterable[Path] = ()) -> List[Finding]:
    """C12: every ``${NAME}`` in ``resources.yaml`` and every credential the
    code reads is listed in ``.env.example`` (commented counts:
    ``# LLM_BASE_URL=`` documents an optional one)."""
    root = Path(root)
    example = root / ".env.example"
    listed = (
        set(_ENV_LISTED.findall(example.read_text(encoding="utf-8")))
        if example.is_file()
        else set()
    )
    wanted: List[tuple] = []
    resources = root / "resources.yaml"
    if resources.is_file():
        wanted += [
            (n, resources, line)
            for n, line in _yaml_env_names(resources.read_text(encoding="utf-8"))
        ]
    for py in sources:
        if _is_test_file(py):
            continue
        try:
            tree = ast.parse(py.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):
            continue
        wanted += [(n, py, line) for n, line in _env_reads(tree) if _is_secret(n)]
    if wanted and not example.is_file():
        names = sorted({n for n, _, _ in wanted})
        name, where, line = wanted[0]
        shown = ", ".join(names[:6]) + (f" and {len(names) - 6} more" if len(names) > 6 else "")
        msg = f"the project uses {len(names)} environment name(s) ({shown}) but has no .env.example listing them"
        return [Finding("C12", where, line, msg)]
    out: List[Finding] = []
    reported: Set[str] = set()
    for name, where, line in wanted:
        if name in listed or name in reported:
            continue
        reported.add(name)
        out.append(
            Finding(
                "C12",
                where,
                line,
                f"{name} is used but .env.example does not list it: add `{name}=`",
            )
        )
    return out


# ── entry points ─────────────────────────────────────────────────────────


def _allowed(source: str) -> dict:
    """line → the rules opted out on that line."""
    out: dict = {}
    for i, line in enumerate(source.splitlines(), 1):
        m = _ALLOW.search(line)
        if m:
            out[i] = {r.strip().upper() for r in m.group(1).split(",") if r.strip()}
    return out


def lint_source(source: str, path: Path) -> List[Finding]:
    """Check one module. Returns findings sorted by line."""
    path = Path(path)
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        return [Finding("E00", path, exc.lineno or 1, f"cannot parse: {exc.msg}")]
    facts = _ModuleFacts(tree)
    found = [
        *_c3_unstable_names(tree, facts, path),
        *_c3_duplicate_names(tree, path),
        *_c5_dynamic_wiring(tree, facts, path),
        *_c6_resource_access(tree, facts, path),
        *_c7_nested_graph(tree, path),
        *_c8_print(tree, path),
        *_c9_retry_loop(tree, path),
        *_c10_logic_in_graph(tree, path),
        *_c11_explicit_edges(tree, path),
    ]
    allow = _allowed(source)
    found = [f for f in found if f.rule not in allow.get(f.line, ())]
    return sorted(set(found), key=lambda f: (f.line, f.rule, f.message))


def python_files(root: Path, skip: Iterable[str] = ()) -> Iterator[Path]:
    skip_all = SKIP_DIRS | set(skip)
    for py in sorted(Path(root).rglob("*.py")):
        if not any(part in skip_all for part in py.relative_to(root).parts):
            yield py


def lint_path(root, skip: Iterable[str] = ()) -> List[Finding]:
    """Check every module under *root* (skipping venvs and caches), and the
    project's environment contract (C12) when *root* is a directory."""
    root = Path(root)
    if root.is_file():
        return lint_source(root.read_text(encoding="utf-8"), root)
    out: List[Finding] = []
    files = list(python_files(root, skip))
    for py in files:
        out.extend(lint_source(py.read_text(encoding="utf-8"), py))
    out.extend(lint_env(root, files))
    return out
