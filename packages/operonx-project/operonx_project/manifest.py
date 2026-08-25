"""``operonx.toml`` — the project manifest.

A manifest answers the three questions no tool can infer from source alone:

1. **Which graphs are entry points?** A module may define components and
   runnable graphs side by side; only the author knows which is which.
2. **What must be injected to build them?** Ops produced by an
   ``@op_factory`` do not exist until a dependency is supplied, so a graph
   cannot be constructed — let alone drawn — without a declared binding.
3. **Where do resources come from?** An optional shared base hub merged
   under a project-specific overlay.

Everything else about a project is derived: input ports come from the entry
signature, required env keys come from ``${VAR}`` in the resource files.

Example::

    [project]
    name = "callbot"
    src  = ["src", "."]     # import roots; defaults to ["."]

    [resources]
    base    = "~/.operonx/common.yaml"
    overlay = "resources.yaml"

    [[graph]]
    name  = "ws_callbot_pipeline"
    entry = "callbot.graph:build_ws_callbot_pipeline"
    [graph.bind]
    agent = "agents.ahamove_hr.agent:AhamoveHRAgent"
"""

from __future__ import annotations

import importlib
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Sequence, Tuple

try:  # tomllib is stdlib from 3.11
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - exercised on 3.10 only
    import tomli as tomllib  # type: ignore[no-redef]

__all__ = ["Manifest", "GraphSpec", "ResourceSpec", "ManifestError", "MANIFEST_NAME"]

MANIFEST_NAME = "operonx.toml"


class ManifestError(Exception):
    """The manifest is missing, malformed, or points at something unimportable."""


def _target(ref: str, where: str) -> Tuple[str, str]:
    """Split a ``"module:attr"`` reference, or raise with useful context."""
    if ref.count(":") != 1:
        raise ManifestError(f"{where}: expected 'module:attr', got {ref!r}")
    module, attr = ref.split(":")
    if not module or not attr:
        raise ManifestError(f"{where}: expected 'module:attr', got {ref!r}")
    return module, attr


def _check_not_foreign(module_name: str, where: str, root: Path) -> None:
    """Refuse a module already imported from *outside* this project.

    Projects routinely share top-level module names — every tutorial example
    defines ``main`` — so a second project resolved in the same interpreter
    would silently receive the first project's module and fail with a
    baffling ``AttributeError``. One process handles one project; this turns
    the violation into a message that says so.
    """
    existing = sys.modules.get(module_name)
    if existing is None:
        return
    origin = getattr(existing, "__file__", None)
    if not origin:
        return
    if Path(origin).resolve().is_relative_to(root):
        return
    raise ManifestError(
        f"{where}: module {module_name!r} is already imported from {origin}, "
        f"which is outside this project ({root}). Resolve one project per "
        f"process — module names collide across projects."
    )


def _import(ref: str, where: str, root: Path, src: Sequence[str] = (".",)) -> Any:
    """Import ``module:attr`` with the project's source roots on ``sys.path``.

    A project's packages do not have to sit at its root — callbot keeps them
    under ``src/`` and declares ``pythonpath = ["src", "."]`` for pytest.
    Without the same list here, ``callbot.graph`` simply does not import.
    """
    module_name, attr = _target(ref, where)
    _check_not_foreign(module_name.split(".")[0], where, root)
    added = []
    for entry in src or (".",):
        candidate = str((root / entry).resolve())
        if candidate not in sys.path:
            sys.path.insert(0, candidate)
            added.append(candidate)
    try:
        module = importlib.import_module(module_name)
    except ImportError as exc:
        raise ManifestError(
            f"{where}: cannot import module {module_name!r} — {exc}. "
            f"Source roots tried: {list(src or ('.',))}"
        ) from exc
    finally:
        for candidate in added:
            if candidate in sys.path:
                sys.path.remove(candidate)
    try:
        return getattr(module, attr)
    except AttributeError as exc:
        raise ManifestError(f"{where}: {module_name!r} has no attribute {attr!r}") from exc


@dataclass(frozen=True)
class ResourceSpec:
    """Where a project's resource definitions come from.

    ``overlay`` is merged over ``base`` by key, so a project can inherit a
    shared hub and override only what differs. Both are optional: a project
    that uses no resources declares neither.
    """

    base: str | None = None
    overlay: str | None = None

    def files(self, root: Path) -> list[Path]:
        """Existing resource files, base first, in merge order."""
        out = []
        for ref in (self.base, self.overlay):
            if not ref:
                continue
            path = Path(ref).expanduser()
            if not path.is_absolute():
                path = root / path
            if path.exists():
                out.append(path)
        return out


@dataclass(frozen=True)
class GraphSpec:
    """One runnable graph.

    ``entry`` points at either a ``@graph`` function — in which case every
    parameter is a runtime input port — or a plain builder function, whose
    parameters are build-time injections and must appear in ``bind``.

    ``inputs`` holds sample values so the UI has something to run with; they
    are documentation, never required for extraction.
    """

    name: str
    entry: str
    bind: Dict[str, str] = field(default_factory=dict)
    inputs: Dict[str, Any] = field(default_factory=dict)
    src: Tuple[str, ...] = (".",)

    def resolve(self, root: Path) -> Any:
        """Import and return the entry object."""
        return _import(self.entry, f"graph '{self.name}' entry", root, self.src)

    def resolve_bind(self, root: Path) -> Dict[str, Any]:
        """Import every declared injection, keyed by parameter name.

        The referenced object is used exactly as it is found — never called.
        A dependency may itself be a callable that the builder expects to
        receive rather than invoke, so "call it if it is callable" would
        silently inject the wrong value. Projects needing construction
        expose a module-level instance.
        """
        return {
            param: _import(ref, f"graph '{self.name}' bind.{param}", root, self.src)
            for param, ref in self.bind.items()
        }


@dataclass(frozen=True)
class Manifest:
    """A parsed ``operonx.toml``."""

    name: str
    root: Path
    description: str = ""
    resources: ResourceSpec = field(default_factory=ResourceSpec)
    graphs: Tuple[GraphSpec, ...] = ()
    src: Tuple[str, ...] = (".",)

    @classmethod
    def load(cls, root: str | Path) -> "Manifest":
        """Read ``operonx.toml`` from *root*.

        Raises:
            ManifestError: file missing, unparseable, or structurally invalid.
        """
        root = Path(root).resolve()
        path = root / MANIFEST_NAME
        if not path.exists():
            raise ManifestError(f"no {MANIFEST_NAME} in {root}")
        try:
            raw = tomllib.loads(path.read_text(encoding="utf-8"))
        except tomllib.TOMLDecodeError as exc:
            raise ManifestError(f"{path}: {exc}") from exc

        project = raw.get("project") or {}
        name = project.get("name")
        if not name:
            raise ManifestError(f"{path}: [project] name is required")

        declared_src = project.get("src") or ["."]
        if isinstance(declared_src, str):
            declared_src = [declared_src]
        src = tuple(str(entry) for entry in declared_src)

        res = raw.get("resources") or {}
        graphs = []
        seen: set[str] = set()
        for i, entry in enumerate(raw.get("graph") or []):
            g_name = entry.get("name")
            g_entry = entry.get("entry")
            where = f"{path}: [[graph]] #{i + 1}"
            if not g_name:
                raise ManifestError(f"{where} is missing 'name'")
            if not g_entry:
                raise ManifestError(f"{where} ('{g_name}') is missing 'entry'")
            # Fail here rather than at import time — a typo in the reference
            # should be a manifest error, not a confusing ImportError later.
            _target(g_entry, f"{where} ('{g_name}')")
            if g_name in seen:
                raise ManifestError(f"{path}: duplicate graph name {g_name!r}")
            seen.add(g_name)
            graphs.append(
                GraphSpec(
                    name=g_name,
                    entry=g_entry,
                    bind=dict(entry.get("bind") or {}),
                    inputs=dict(entry.get("inputs") or {}),
                    src=src,
                )
            )

        if not graphs:
            raise ManifestError(f"{path}: at least one [[graph]] is required")

        return cls(
            name=name,
            root=root,
            description=project.get("description", ""),
            resources=ResourceSpec(base=res.get("base"), overlay=res.get("overlay")),
            graphs=tuple(graphs),
            src=src,
        )

    def graph(self, name: str) -> GraphSpec:
        """The :class:`GraphSpec` called *name*."""
        for g in self.graphs:
            if g.name == name:
                return g
        known = ", ".join(g.name for g in self.graphs)
        raise ManifestError(f"{self.name}: no graph {name!r} (have: {known})")
