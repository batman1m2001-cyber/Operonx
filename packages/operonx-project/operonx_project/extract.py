"""Build a project's graphs and serialise them to the Project IR.

The IR is **derived and disposable** — never committed. Git carries source;
this is a cache the UI reads so it never has to parse Python.

Two sources, deliberately:

* **The built graph supplies semantics.** Ops produced by an ``@op_factory``
  do not exist until something is injected, so nothing short of building can
  see them. This is why extraction executes project code, and why one
  process handles one project.
* **The AST supplies source anchors.** ``BaseOp`` records no construction
  site. For a ``FuncOp`` we can recover one from ``code_fn.__code__``, but a
  class-based op (``LLMOp``, ``EmitOp``) has none — its class lives in
  operonx, not in the project. C3 guarantees an op's name is its assignment
  variable, so the two views line up by name.

Reading values is done through ``object.__getattribute__`` against a type
whitelist. ``Ref.__getattr__`` *builds a new Ref* for any attribute asked of
it, so an ordinary ``hasattr`` probe fabricates objects instead of answering
a question (finding S9).
"""

from __future__ import annotations

import ast
import inspect
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

try:  # tomllib is stdlib from 3.11
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - exercised on 3.10 only
    import tomli as tomllib  # type: ignore[no-redef]

from operonx_project.lint import SKIP_DIRS
from operonx_project.manifest import GraphSpec, Manifest

__all__ = [
    "IR_VERSION",
    "extract_project",
    "extract_graph",
    "extract_dependencies",
    "build_entry",
    "ExtractError",
]

IR_VERSION = 1

_ENV_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::([^}]*))?\}")

# Types we are willing to reach inside. Anything else is rendered by repr,
# never probed — see the S9 note above.
_REF = "Ref"
_SCRATCH_REF = "ScratchRef"


class ExtractError(Exception):
    """A declared graph could not be built."""


def _slot(obj: Any, name: str, default: Any = None) -> Any:
    """Read one attribute without triggering ``__getattr__``."""
    try:
        return object.__getattribute__(obj, name)
    except AttributeError:
        return default


# ── bindings ─────────────────────────────────────────────────────────────


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    return repr(value)


def _source_id(source: Any) -> Optional[str]:
    """Identify what a ``Ref`` points at.

    ``Ref._source`` holds the producing **op object**, not a name — its
    ``repr`` renders the full name, which is easy to mistake for the stored
    value. Ops are safe to read normally; only ``Ref`` fabricates attributes.
    """
    if source is None or isinstance(source, str):
        return source
    full = _slot(source, "full_name")
    return full if isinstance(full, str) else repr(source)[:200]


def _binding(value: Any) -> Dict[str, Any]:
    """Describe where an input comes from, without probing unknown objects."""
    kind = type(value).__name__
    if kind == _REF:
        return {
            "kind": "ref",
            "from": _source_id(_slot(value, "_source")),
            "output": _slot(value, "var"),
            "transforms": len(_slot(value, "_transforms") or []),
        }
    if kind == _SCRATCH_REF:
        return {"kind": "scratch", "key": _slot(value, "key")}
    if value is None:
        return {"kind": "unset"}
    if isinstance(value, (bool, int, float, str, list, tuple, dict)):
        return {"kind": "literal", "value": _json_safe(value)}
    return {"kind": "opaque", "repr": repr(value)[:200]}


# ── source anchors ───────────────────────────────────────────────────────


def _anchors_for_module(path: Path) -> Dict[str, Dict[str, int]]:
    """``{lookup_name: {op_variable_name: line}}`` for one file.

    Each ``@graph`` body is filed under its own name *and* under the name of
    any plain function enclosing it. A manifest entry for the builder
    pattern names the **builder** (``build_ws_callbot_pipeline``), while the
    body belongs to the ``@graph`` inside it (``ws_callbot_pipeline``);
    without the second key, every builder-style project silently loses its
    ``wired_at`` anchors — and a class-based op such as ``LLMOp`` has no
    other source location at all.
    """
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (SyntaxError, OSError):
        return {}

    enclosing: Dict[int, Optional[ast.AST]] = {}

    def walk(node: ast.AST, inside: Optional[ast.AST]) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                enclosing[id(child)] = inside
                walk(child, child)
            else:
                walk(child, inside)

    walk(tree, None)

    out: Dict[str, Dict[str, int]] = {}
    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        decorated = {
            (d.func if isinstance(d, ast.Call) else d).id
            for d in fn.decorator_list
            if isinstance((d.func if isinstance(d, ast.Call) else d), ast.Name)
        }
        if "graph" not in decorated:
            continue
        names: Dict[str, int] = {}
        for stmt in ast.walk(fn):
            if isinstance(stmt, ast.Assign) and len(stmt.targets) == 1:
                tgt = stmt.targets[0]
                if isinstance(tgt, ast.Name):
                    names.setdefault(tgt.id, stmt.lineno)
        out[fn.name] = names
        outer = enclosing.get(id(fn))
        if isinstance(outer, (ast.FunctionDef, ast.AsyncFunctionDef)):
            # A builder may wrap more than one graph; first one wins rather
            # than merging, so a reported line is never a blend of two bodies.
            out.setdefault(outer.name, names)
    return out


def collect_anchors(
    root: Path, src: Sequence[str] = (".",)
) -> Dict[str, Dict[str, Dict[str, int]]]:
    """``{module: {graph_fn: {op_var: line}}}`` across a project.

    Module names are computed relative to the declared **source roots**, not
    to the project root. With callbot's ``src/`` layout the two disagree —
    ``src/callbot/graph.py`` is imported as ``callbot.graph``, and keying it
    as ``src.callbot.graph`` means every anchor lookup misses silently.
    """
    roots = [(root / entry).resolve() for entry in (src or (".",))]
    out: Dict[str, Dict[str, Dict[str, int]]] = {}
    for py in sorted(root.rglob("*.py")):
        if any(part in SKIP_DIRS for part in py.parts):
            continue
        found = _anchors_for_module(py)
        if not found:
            continue
        resolved = py.resolve()
        for base in roots:
            try:
                relative = resolved.relative_to(base)
            except ValueError:
                continue
            # setdefault: the first (most specific) source root wins, matching
            # how the import system resolves the same name.
            out.setdefault(".".join(relative.with_suffix("").parts), found)
    return out


def _source_of(
    op: Any, root: Path, anchor_line: Optional[int], module: str
) -> Optional[Dict[str, Any]]:
    """Where this node came from — two different questions, both useful.

    ``defined_at`` is where the op's body lives, available only for a
    ``FuncOp`` via ``code_fn.__code__``. ``wired_at`` is where it was
    constructed inside the graph, recovered from the AST — the only anchor a
    class-based op such as ``InterruptOp`` or ``LLMOp`` has, since its class
    lives in operonx rather than the project.
    """
    out: Dict[str, Any] = {}
    code_fn = _slot(op, "code_fn")
    code = getattr(code_fn, "__code__", None) if code_fn is not None else None
    if code is not None:
        try:
            rel = str(Path(code.co_filename).resolve().relative_to(root))
        except ValueError:
            rel = code.co_filename
        out["defined_at"] = {"file": rel, "line": code.co_firstlineno}
    if anchor_line is not None:
        out["wired_at"] = {"file": module.replace(".", "/") + ".py", "line": anchor_line}
    return out or None


# ── nodes, edges, loops ──────────────────────────────────────────────────


def _edge_origin(edge: Any) -> str:
    """Which decision made this edge soft — the author's or the compiler's."""
    if getattr(edge, "pinned_hard", False):
        return "pinned_hard"
    if getattr(edge, "auto_soft", False):
        return "auto_soft"
    if getattr(edge, "soft", False):
        return "authored_soft"
    return "authored"


def _node(op: Any, root: Path, anchors: Dict[str, int], module: str) -> Dict[str, Any]:
    inputs = []
    for name, param in (_slot(op, "inputs") or {}).items():
        inputs.append(
            {
                "name": name,
                "binding": _binding(_slot(param, "value")),
                "required": bool(_slot(param, "required", False)),
            }
        )
    node: Dict[str, Any] = {
        "id": op.full_name,
        "name": op.name,
        "kind": type(op).__name__,
        "bound": _slot(op, "bound"),
        "start": bool(_slot(op, "start", False)),
        "end": bool(_slot(op, "end", False)),
        "inputs": inputs,
        "outputs": sorted((_slot(op, "outputs") or {}).keys()),
        "source": _source_of(op, root, anchors.get(op.name), module),
    }
    for extra in ("resource", "channel"):
        value = _slot(op, extra)
        if value is not None:
            node[extra] = _json_safe(value)
    if _slot(op, "_ops"):
        node["graph"] = _subgraph(op, root, anchors, module)
    return node


def _loop_of(op: Any) -> Optional[Dict[str, Any]]:
    """Loop metadata, if this node is one. Three kinds render differently."""
    mode = _slot(op, "_loop_mode")
    config = _slot(op, "_loop_config")
    if mode is None and config is None:
        return None
    return {
        "mode": mode or "classic",
        "synthetic": bool(_slot(op, "_synthetic", False)),
        # Cycle rewriting DELETES back-edges from _edges, so this is the only
        # record of what the author actually wrote.
        "back_edges": [list(e) for e in (_slot(op, "_back_edges") or [])],
        "until": _json_safe(_slot(config, "until")) if config is not None else None,
        "max_iterations": _slot(config, "max_iterations") if config is not None else None,
    }


def _subgraph(g: Any, root: Path, anchors: Dict[str, int], module: str) -> Dict[str, Any]:
    nodes, loops = [], {}
    for name, op in (_slot(g, "_ops") or {}).items():
        nodes.append(_node(op, root, anchors, module))
        loop = _loop_of(op)
        if loop is not None:
            loops[op.full_name] = loop
    edges = [
        {
            "from": edge.from_node,
            "to": edge.to_node,
            "type": edge.type,
            "soft": bool(edge.soft),
            "origin": _edge_origin(edge),
        }
        for edge in (_slot(g, "_edges") or {}).values()
    ]
    out: Dict[str, Any] = {
        "nodes": nodes,
        "edges": edges,
        "entries": list(_slot(g, "entries") or []),
        "exits": list(_slot(g, "exits") or []),
    }
    if loops:
        out["loops"] = loops
    rewritten = _slot(g, "_rewritten_from")
    if rewritten:
        out["rewritten_from"] = _json_safe(rewritten)
    return out


# ── building ─────────────────────────────────────────────────────────────


def build_entry(spec: GraphSpec, root: Path) -> Any:
    """Resolve and build one declared graph.

    An entry is either a ``@graph`` — every parameter is a runtime input
    port, wired here to ``PARENT`` — or a builder taking build-time
    injections, which ``bind`` supplies.

    The instance is renamed to the manifest label *before* building.
    ``auto_name`` reads the caller's frame, so without this the graph would
    be named after a local variable in this function.
    """
    from operonx.core import PARENT

    target = spec.resolve(root)
    if spec.bind:
        # Each bound reference is used **as-is**, never called. An earlier
        # draft called a zero-argument provider, which is ambiguous the
        # moment a dependency is itself a callable: callbot's
        # `build_mock_chat_pipeline(agent, sink_op)` takes an op *as a
        # value*, and calling it would inject the wrong thing entirely.
        # Projects that need construction expose a module-level instance,
        # which is what callbot already does (`agents.ahamove_hr:agent`).
        try:
            target = target(**spec.resolve_bind(root))
        except Exception as exc:  # noqa: BLE001 — surface any project failure
            raise ExtractError(f"graph '{spec.name}': builder raised {exc!r}") from exc

    try:
        params = list(inspect.signature(target).parameters)
    except (TypeError, ValueError) as exc:
        raise ExtractError(f"graph '{spec.name}': entry is not callable — {exc}") from exc

    try:
        instance = target(**{p: PARENT[p] for p in params})
    except Exception as exc:  # noqa: BLE001
        raise ExtractError(f"graph '{spec.name}': construction raised {exc!r}") from exc

    instance.name = spec.name
    instance.build()
    return instance


def extract_graph(
    spec: GraphSpec, root: Path, anchors: Dict[str, Dict[str, Dict[str, int]]]
) -> Dict[str, Any]:
    """Build one declared graph and serialise it."""
    instance = build_entry(spec, root)
    module, attr = spec.entry.split(":")
    by_graph = anchors.get(module, {})
    # The @graph function name, not the manifest label — the label is ours.
    fn_anchors = by_graph.get(attr, {})
    out = {"name": spec.name, "entry": spec.entry, "inputs": _json_safe(spec.inputs)}
    out.update(_subgraph(instance, root, fn_anchors, module))
    return out


# ── resources and env ────────────────────────────────────────────────────


def _scan_env(text: str) -> Tuple[List[str], Dict[str, str]]:
    required, optional = set(), {}
    for name, default in _ENV_PATTERN.findall(text):
        if default == "":
            # ``${VAR}`` has no colon; ``${VAR:}`` means empty default.
            required.add(name)
        else:
            optional[name] = default
    return sorted(required), dict(sorted(optional.items()))


def _parse_resources(text: str) -> Tuple[List[str], str]:
    """Top-level keys, and the live content to scan for env variables.

    Parsed as YAML rather than scanned line by line. A commented-out block
    looks exactly like a declaration to a scanner — ex16 has both a live
    ``doc_store:corpus`` and a commented one — and it would equally report
    the ``${PG_DSN}`` inside that dead block as a required variable. Telling
    someone to set a variable nothing reads is the same class of lie as the
    install hints in S12.

    Returns the keys and a re-serialised copy of the parsed document, so the
    env scan sees only what survives parsing. ``pyyaml`` is a base operonx
    dependency, so this costs nothing.
    """
    try:
        import yaml

        loaded = yaml.safe_load(text)
        if isinstance(loaded, dict):
            return [str(k) for k in loaded], yaml.safe_dump(loaded, default_flow_style=False)
    except Exception:  # noqa: BLE001 - malformed file, fall back below
        pass
    # Unparseable: report no keys rather than guessing, but still scan the
    # raw text so a broken file does not silently hide its env contract.
    return [], text


def extract_resources(manifest: Manifest) -> Dict[str, Any]:
    """Declared resource keys and the env contract they imply.

    Values are never read into the IR — only which keys exist and which
    variables they demand. The ``.env`` form is derived from this, so there
    is no second place to keep it in step.
    """
    keys: List[str] = []
    required: List[str] = []
    optional: Dict[str, str] = {}
    for path in manifest.resources.files(manifest.root):
        found, live = _parse_resources(path.read_text(encoding="utf-8"))
        keys.extend(found)
        req, opt = _scan_env(live)
        required.extend(req)
        optional.update(opt)
    return {
        "keys": sorted(set(keys)),
        "env": {"required": sorted(set(required)), "optional": dict(sorted(optional.items()))},
    }


def extract_dependencies(root: Path) -> Dict[str, Any]:
    """The project's declared dependencies, from its ``pyproject.toml``.

    Declarations only — never a resolved lockfile and never what happens to
    be installed. Those are machine state; the IR stays a function of the
    source so two extractions of the same commit agree byte for byte.
    """
    path = root / "pyproject.toml"
    if not path.exists():
        return {"declared": False}
    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError:
        return {"declared": False, "error": "pyproject.toml is not valid TOML"}

    project = raw.get("project") or {}
    extras = project.get("optional-dependencies") or {}
    return {
        "declared": True,
        "name": project.get("name"),
        "requires_python": project.get("requires-python"),
        "dependencies": sorted(project.get("dependencies") or []),
        "extras": {k: sorted(v) for k, v in sorted(extras.items())},
    }


def extract_project(manifest: Manifest) -> Dict[str, Any]:
    """The whole project as one IR document."""
    anchors = collect_anchors(manifest.root, manifest.src)
    return {
        "ir_version": IR_VERSION,
        "project": manifest.name,
        "description": manifest.description,
        "graphs": [extract_graph(spec, manifest.root, anchors) for spec in manifest.graphs],
        "resources": extract_resources(manifest),
        "dependencies": extract_dependencies(manifest.root),
    }
