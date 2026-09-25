"""`Application` — what production does with the declaration.

An Operon is compute. An application is what puts work into one: the
services that listen, the jobs that read a source, and the resources
both reach. It is declared in Python (`Service`, `Job`) or in
``operonx.toml``; this object is that declaration, with one method per
thing production does::

    APP = Application("callbot", services=[...], jobs=[...])   # app/main.py
    app = Application.find()            # the nearest operonx.toml — or the
                                        # APP it points at with [project] app
    app.serve(only=["call"])            # what operonx-serve does
    app.run_sync("nightly")             # what operonx-run does
    app.describe()                      # what --list and the studio read

It is a composition root and nothing more: three lists and three
methods. Nothing inside a graph ever sees it, and it holds no lifecycle
of its own — the engine runs graphs, the transports mint runs, the jobs
keep records. Keep it small; if it grows past the code it replaced, it
has become a framework object, which is the thing it must not be.
"""

from __future__ import annotations

import asyncio
import inspect
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union

from .declare import (
    build_job,
    describe_job,
    describe_jobspec,
    describe_service,
    graph_refs,
    load_declared,
    manifest_from,
    project_root,
    ref_name,
)
from .manifest import Manifest, ManifestError, ServeSpec

__all__ = ["Application", "GraphRef"]


@dataclass(frozen=True)
class GraphRef:
    """A graph the application knows: by name, by entry point, and by who
    uses it. Compiled on demand, never at load."""

    name: str
    entry: str
    used_by: Tuple[str, ...] = field(default_factory=tuple)
    #: A variant's bound parameters (see `[serve.variants]`); empty otherwise.
    bind: Dict[str, Any] = field(default_factory=dict, compare=False)
    #: The graph object itself when the application was declared in Python.
    graph: Any = field(default=None, compare=False, repr=False)

    def compile(self, **kwargs: Any) -> Any:
        """An ``Operon`` for this graph, the way a served graph is compiled:
        every unbound parameter becomes a runtime input."""
        from .serve.app import compile_graph

        target = self.graph if self.graph is not None else self.entry
        return compile_graph(target, bind=self.bind or None, where=f"graph {self.name!r}", **kwargs)


class Application:
    """See the module docstring."""

    def __init__(
        self,
        manifest: Union[Manifest, str],
        *,
        services: Sequence[ServeSpec] = (),
        jobs: Sequence[Any] = (),
        on_startup: Sequence[Any] = (),
        root: Union[str, Path, None] = None,
        src: Sequence[str] = ("src", "."),
        resources: Optional[str] = "resources.yaml",
        description: str = "",
    ):
        if isinstance(manifest, Manifest):
            self.manifest = manifest
            self._jobs: Optional[Dict[str, Any]] = None
        else:
            here = Path(inspect.stack()[1].filename).resolve().parent
            base = Path(root).resolve() if root else project_root(here)
            self.manifest = manifest_from(
                manifest,
                services=services,
                on_startup=on_startup,
                root=base,
                src=src,
                resources=resources,
                description=description,
            )
            self._jobs = {j.name: j for j in jobs}
        self._bootstrapped = False

    # -- loading -----------------------------------------------------------

    @classmethod
    def load(cls, path: Union[str, Path]) -> "Application":
        return cls._from(Manifest.from_file(path))

    @classmethod
    def find(cls, start: Union[str, Path] = ".") -> "Application":
        """The nearest ``operonx.toml`` at or above *start* — or, when that
        file says ``[project] app = "module:APP"``, the object it points at."""
        return cls._from(Manifest.find(start))

    @classmethod
    def _from(cls, manifest: Manifest) -> "Application":
        app = cls(manifest)
        if manifest.app_entry:
            app.bootstrap()
            return load_declared(manifest)
        return app

    @classmethod
    def from_dict(cls, raw: Dict[str, Any], source: Optional[Path] = None) -> "Application":
        return cls(Manifest.from_dict(raw, source=source))

    @property
    def name(self) -> str:
        return self.manifest.name

    @property
    def root(self) -> Path:
        return self.manifest.root

    def bootstrap(self) -> None:
        """Make the project importable and its resources resolvable. Once."""
        if self._bootstrapped:
            return
        for src in reversed(self.manifest.src):
            path = str((self.root / src).resolve())
            if path not in sys.path:
                sys.path.insert(0, path)
        overlay = self.manifest.resources_overlay
        if overlay and (self.root / overlay).exists():
            import operonx

            operonx.bootstrap(resources=self.root / overlay)
        self._bootstrapped = True

    # -- the three lists ---------------------------------------------------

    @property
    def services(self) -> Tuple[ServeSpec, ...]:
        return self.manifest.serves

    def service(self, name: str) -> ServeSpec:
        return self.manifest.serve(name)

    @property
    def graphs(self) -> List[GraphRef]:
        """Every graph the manifest names, once each, with who uses it; a door
        with variants contributes one graph per variant."""
        return [GraphRef(*ref) for ref in graph_refs(self.manifest)]

    @property
    def jobs(self) -> List[Any]:
        """The jobs — declared as objects, or built once from ``[[job]]``."""
        if self._jobs is None:
            self.bootstrap()
            self._jobs = {spec.name: build_job(spec, self.root) for spec in self.manifest.jobs}
        return list(self._jobs.values())

    def job(self, name: str) -> Any:
        for j in self.jobs:
            if j.name == name:
                return j
        known = ", ".join(j.name for j in self.jobs) or "none"
        raise ManifestError(f"no job named {name!r} (have: {known})")

    # -- what production does ----------------------------------------------

    def serve(self, only: Optional[Sequence[str]] = None) -> None:
        """Boot every listener the manifest declares, and block."""
        from .serve.app import serve_manifest

        self.bootstrap()
        serve_manifest(self.manifest, only=list(only) if only else None)

    def asgi(
        self,
        *,
        port: Optional[int] = None,
        only: Optional[Sequence[str]] = None,
        startup: bool = True,
    ) -> Any:
        """One listener's ASGI app — for a process that runs uvicorn itself, or
        a test client. By *port*, or the only one. ``startup=False`` leaves the
        ``on_startup`` hooks to another process (an admin listener beside the
        call workers does not warm the model twice)."""
        from .serve.app import build_app

        self.bootstrap()
        specs = [s for s in self.services if s.kind != "asgi" or s.app]
        if only:
            specs = [s for s in specs if s.name in set(only)]
        if port is not None:
            specs = [s for s in specs if s.port == port]
        listeners = {s.listener for s in specs}
        if len(listeners) != 1:
            raise ManifestError(
                f"asgi() needs exactly one listener; {self.name} has "
                f"{sorted(listeners) or 'none'} — pass port= or only="
            )
        return build_app(tuple(specs), on_startup=self.manifest.on_startup if startup else ())

    async def run(self, name: str, *, resume: bool = False) -> Any:
        """Run a job or runbook once and return its record."""
        return await self.job(name).run(resume=resume)

    def run_sync(self, name: str, *, resume: bool = False) -> Any:
        return asyncio.run(self.run(name, resume=resume))

    # -- describing --------------------------------------------------------

    def describe(self) -> Dict[str, Any]:
        """The three lists as plain data: what ``--list`` and the studio read.
        Names of things only; a TOML application is described without
        importing the project."""
        if self.manifest.jobs:
            jobs = [describe_jobspec(j) for j in self.manifest.jobs]
        else:
            jobs = [describe_job(j) for j in (self._jobs or {}).values()]
        return {
            "name": self.name,
            "root": str(self.root),
            "graphs": [
                {
                    "name": g.name,
                    "entry": g.entry,
                    "used_by": list(g.used_by),
                    "bind": {k: ref_name(v) for k, v in g.bind.items()},
                }
                for g in self.graphs
            ],
            "services": [describe_service(s) for s in self.services],
            "jobs": jobs,
        }

    def __repr__(self) -> str:
        return (
            f"Application({self.name!r}, graphs={len(self.graphs)}, "
            f"services={len(self.services)}, jobs={len(self.jobs) if self._jobs else len(self.manifest.jobs)})"
        )
