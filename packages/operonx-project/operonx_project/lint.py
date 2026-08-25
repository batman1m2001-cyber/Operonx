"""Static checks for the project conventions (C3, C5, C6).

These are the rules that can be decided from source alone. C1 (manifest
resolves) and C2 (construction is cheap) require actually building the
graph and land with the extractor.

The checks mirror how operonx itself behaves at runtime, so a passing
project is one where the static view and the built graph agree:

- **C3** reproduces ``operonx.core.utils.auto_name``. That function recovers
  an op's name from the *assignment statement* and explicitly rejects tuple
  unpacking, augmented assignment, and attribute/subscript targets. When it
  fails, the op falls back to ``unique_name()`` — a per-process UUID — and
  its identity changes on every run.
- **C5** finds ops built or wired inside loops, where no stable mapping
  back from a diagram exists.
- **C6** finds resources and credentials reached in ways a tool cannot
  resolve.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Iterator, List, Optional, Set

__all__ = ["Finding", "lint_path", "lint_source", "SKIP_DIRS"]

SKIP_DIRS = {"__pycache__", ".venv", "venv", ".git", "build", "dist", "site"}

_SECRET_HINTS = ("KEY", "TOKEN", "SECRET", "PASSWORD", "CREDENTIAL")

_LOOPS = (
    ast.For,
    ast.AsyncFor,
    ast.While,
    ast.ListComp,
    ast.SetComp,
    ast.DictComp,
    ast.GeneratorExp,
)


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
    return "op" in _decorator_names(node)


def _is_graph_def(node: ast.AST) -> bool:
    return "graph" in _decorator_names(node)


# ── op-construction detection ────────────────────────────────────────────


class _ModuleFacts:
    """Names defined in this module that construct an op when called."""

    def __init__(self, tree: ast.Module):
        self.op_names: Set[str] = set()
        self.graph_names: Set[str] = set()
        for n in ast.walk(tree):
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if _is_op_def(n):
                    self.op_names.add(n.name)
                elif _is_graph_def(n):
                    self.graph_names.add(n.name)

    def constructs_op(self, node: ast.AST) -> bool:
        """True if calling *node* yields an op instance.

        Covers three idioms: an ``*Op`` class, its ``.of()`` shorthand, and a
        call to a locally-defined ``@op`` / ``@graph``. Cross-module ops are
        invisible here — a lint sees one file at a time, and a false negative
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
    """ids of Call nodes that ``auto_name`` would successfully name.

    Mirrors ``auto_name._parse_assignment``: a single ``Name`` target, or an
    annotated assignment to a ``Name``. Everything else — tuple unpacking,
    augmented assignment, attribute and subscript targets — is rejected
    there and must be rejected here.
    """
    ok: Set[int] = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Assign) and len(n.targets) == 1 and isinstance(n.targets[0], ast.Name):
            ok.add(id(n.value))
        elif (
            isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name) and n.value is not None
        ):
            ok.add(id(n.value))
    return ok


# ── rules ────────────────────────────────────────────────────────────────


def _c3_unstable_names(tree: ast.Module, facts: _ModuleFacts, path: Path) -> Iterator[Finding]:
    """Scoped to ``@graph`` bodies, where a name becomes node identity.

    A root graph instantiated for immediate execution — ``Operon(basic(val=…))``
    in a ``main()`` — is throwaway; nothing persists against its name, and the
    UI identifies a root by its manifest label. Only ops wired *inside* a
    graph get a ``full_name`` that layout, comments and run history key on.
    """
    clean = _clean_assignment_values(tree)
    for fn in ast.walk(tree):
        if not (isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)) and _is_graph_def(fn)):
            continue
        for n in ast.walk(fn):
            if not facts.constructs_op(n):
                continue
            if id(n) in clean or _has_kwarg(n, "name"):
                continue
            yield Finding(
                "C3",
                path,
                n.lineno,
                f"op built inside @graph {fn.name} where auto_name() cannot recover a "
                f"name, so it gets a per-process UUID — assign it to a plain variable "
                f"or pass name=",
            )


def _c3_duplicate_names(tree: ast.Module, path: Path) -> Iterator[Finding]:
    """Two ops sharing a name in one graph: the second silently overwrites."""
    for n in ast.walk(tree):
        if not (isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and _is_graph_def(n)):
            continue
        seen: dict[str, int] = {}
        for stmt in ast.walk(n):
            if isinstance(stmt, ast.Assign) and len(stmt.targets) == 1:
                tgt = stmt.targets[0]
                if isinstance(tgt, ast.Name):
                    if tgt.id in seen:
                        yield Finding(
                            "C3",
                            path,
                            stmt.lineno,
                            f"'{tgt.id}' is reassigned inside @graph {n.name} — if both are "
                            f"ops the second silently overwrites the first (graph_op.py:201)",
                        )
                    seen[tgt.id] = stmt.lineno


def _iterates_static_ops(node: ast.AST) -> bool:
    """True for ``for leaf in (ex, gd, av, fl):`` — a literal of local names.

    Wiring such a loop is still fully determined at build time: the ops
    already exist and are already named, so extraction sees exactly the edges
    it would see unrolled. Only the *write* path is constrained, because
    adding a node in the UI means editing a tuple rather than appending a
    line. That is a warning, not an error.
    """
    it = getattr(node, "iter", None)
    if not isinstance(it, (ast.Tuple, ast.List, ast.Set)):
        return False
    return bool(it.elts) and all(isinstance(e, ast.Name) for e in it.elts)


def _c5_dynamic_wiring(tree: ast.Module, facts: _ModuleFacts, path: Path) -> Iterator[Finding]:
    for fn in ast.walk(tree):
        if not (isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)) and _is_graph_def(fn)):
            continue
        for node in ast.walk(fn):
            if not isinstance(node, _LOOPS):
                continue
            static = _iterates_static_ops(node)
            for sub in ast.walk(node):
                if facts.constructs_op(sub):
                    # Always an error: the node itself does not exist until
                    # the loop runs, so there is nothing to draw or address.
                    yield Finding(
                        "C5",
                        path,
                        sub.lineno,
                        f"op built inside a loop in @graph {fn.name} — a diagram has no "
                        f"stable node to map back to; build it outside the loop",
                    )
                elif isinstance(sub, ast.BinOp) and isinstance(sub.op, ast.RShift):
                    if static:
                        yield Finding(
                            "C5",
                            path,
                            sub.lineno,
                            f"edges wired by looping over a literal in @graph {fn.name} — "
                            f"extraction is fine, but the UI cannot add a node here "
                            f"without editing the tuple",
                            severity="warning",
                        )
                    else:
                        yield Finding(
                            "C5",
                            path,
                            sub.lineno,
                            f"edges wired inside a loop in @graph {fn.name} — the topology "
                            f"is not knowable without running the loop",
                        )


def _is_resource_literal(node: ast.AST) -> bool:
    """A resolvable ``resource=``: one key, or a list of keys.

    ``LLMOp.of(resource=["gpt-4o-mini", "gpt-4o"], ratios=[0.7, 0.3])`` is
    load balancing across two declared resources — every key is still known
    without running anything.
    """
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return True
    if isinstance(node, (ast.List, ast.Tuple)):
        return bool(node.elts) and all(
            isinstance(e, ast.Constant) and isinstance(e.value, str) for e in node.elts
        )
    return False


def _c6_resource_access(tree: ast.Module, facts: _ModuleFacts, path: Path) -> Iterator[Finding]:
    for n in ast.walk(tree):
        if facts.constructs_op(n):
            for kw in n.keywords:
                if kw.arg == "resource" and not _is_resource_literal(kw.value):
                    # A warning, not an error. Extraction builds the graph, so
                    # the resolved key always reaches the IR — callbot's
                    # `resource=agent.llm_resource` is a deliberate injection
                    # that keeps its graph agent-agnostic, and demanding a
                    # literal would mean hardcoding what the design keeps
                    # pluggable. What is genuinely lost is in-place editing:
                    # the UI cannot repoint this op without touching the
                    # thing that supplies it.
                    yield Finding(
                        "C6",
                        path,
                        n.lineno,
                        "resource= is not a string literal, so it is only knowable after "
                        "the graph is built — readable in the IR, but the UI cannot "
                        "repoint this op without editing whatever supplies the value",
                        severity="warning",
                    )
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute):
            if n.func.attr == "getenv" and n.args:
                arg = n.args[0]
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                    if any(h in arg.value.upper() for h in _SECRET_HINTS):
                        yield Finding(
                            "C6",
                            path,
                            n.lineno,
                            f"credential {arg.value!r} read directly — declare it as "
                            f"${{{arg.value}}} in resources.yaml so the env contract is derived",
                            severity="warning",
                        )


# ── entry points ─────────────────────────────────────────────────────────


def lint_source(source: str, path: Path) -> List[Finding]:
    """Check one module. Returns findings sorted by line."""
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
    ]
    return sorted(found, key=lambda f: (f.line, f.rule))


def _python_files(root: Path, skip: Iterable[str] = ()) -> Iterator[Path]:
    skip_all = SKIP_DIRS | set(skip)
    for py in sorted(root.rglob("*.py")):
        if not any(part in skip_all for part in py.parts):
            yield py


def lint_path(root: str | Path, skip: Iterable[str] = ()) -> List[Finding]:
    """Check every module under *root*, skipping venvs and caches."""
    root = Path(root)
    if root.is_file():
        return lint_source(root.read_text(encoding="utf-8"), root)
    out: List[Finding] = []
    for py in _python_files(root, skip):
        out.extend(lint_source(py.read_text(encoding="utf-8"), py))
    return out
