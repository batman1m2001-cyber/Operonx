"""`Application` — the loaded manifest, and what production does with it.

An Operon is compute. An application is what puts work into one: the
services that listen (``[[serve]]``), the jobs that read a source
(``[[job]]``), and the resources both reach. ``operonx.toml`` declares
it; this object is that declaration loaded, with one method per thing
production does::

    app = Application.find()            # or .load("operonx.toml")
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
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union

from .manifest import JobSpec, Manifest, ManifestError, ServeSpec

__all__ = ["Application", "GraphRef"]


@dataclass(frozen=True)
class GraphRef:
    """A graph the application knows: by name, by entry point, and by who
    uses it. Compiled on demand, never at load."""

    name: str
    entry: str
    used_by: Tuple[str, ...] = field(default_factory=tuple)

    def compile(self, **kwargs: Any) -> Any:
        """An ``Operon`` for this graph, the way a served graph is compiled:
        every factory parameter becomes a runtime input."""
        from .serve.app import compile_graph

        return compile_graph(self.entry, where=f"graph {self.name!r}", **kwargs)


class Application:
    """See the module docstring."""

    def __init__(self, manifest: Manifest):
        self.manifest = manifest
        self._jobs: Optional[Dict[str, Any]] = None
        self._bootstrapped = False

    # -- loading -----------------------------------------------------------

    @classmethod
    def load(cls, path: Union[str, Path]) -> "Application":
        return cls(Manifest.from_file(path))

    @classmethod
    def find(cls, start: Union[str, Path] = ".") -> "Application":
        """The nearest ``operonx.toml`` at or above *start*."""
        return cls(Manifest.find(start))

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
        """Make the project importable and its resources resolvable. Once.

        The manifest's directory goes on ``sys.path`` so ``module:attr``
        entry points resolve; the resources overlay installs the hub. A hub
        that is already installed is left alone.
        """
        if self._bootstrapped:
            return
        root = str(self.root)
        if root not in sys.path:
            sys.path.insert(0, root)
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
        """Every graph the manifest names, once each, with who uses it:
        the ``[[graph]]`` blocks, then whatever services and jobs point at."""
        by_entry: Dict[str, Tuple[str, List[str]]] = {}
        for g in self.manifest.graphs:
            by_entry.setdefault(g.entry, (g.name, []))
        for s in self.services:
            if s.graph:
                name = by_entry.get(s.graph, (s.graph.rpartition(":")[2], []))[0]
                by_entry.setdefault(s.graph, (name, []))[1].append(f"serve:{s.name}")
        for j in self.manifest.jobs:
            if j.graph:
                name = by_entry.get(j.graph, (j.graph.rpartition(":")[2], []))[0]
                by_entry.setdefault(j.graph, (name, []))[1].append(f"job:{j.name}")
        return [GraphRef(name, entry, tuple(users)) for entry, (name, users) in by_entry.items()]

    @property
    def jobs(self) -> List[Any]:
        """The ``[[job]]`` blocks as ``Job`` and ``Runbook`` objects, built
        once. Building imports the project, so the app is bootstrapped."""
        if self._jobs is None:
            self.bootstrap()
            from .jobs import Job, Runbook
            from .serve.registry import load_object

            built: Dict[str, Any] = {}
            for spec in self.manifest.jobs:
                built[spec.name] = self._build_job(spec, Job, Runbook, load_object)
            self._jobs = built
        return list(self._jobs.values())

    def _build_job(self, spec: JobSpec, Job: Any, Runbook: Any, load_object: Any) -> Any:
        if spec.runbook:
            runbook = load_object(spec.runbook, field=f"[[job]] {spec.name!r} runbook")
            if not isinstance(runbook, Runbook):
                raise ManifestError(
                    f"[[job]] {spec.name!r} runbook {spec.runbook!r} is a "
                    f"{type(runbook).__name__}, not a Runbook"
                )
            if spec.record_dir:
                rd = Path(spec.record_dir)
                runbook.record_dir = rd if rd.is_absolute() else self.root / rd
            return runbook
        return Job.from_spec(spec, self.root)

    def job(self, name: str) -> Any:
        self.manifest.job(name)  # a ManifestError names the known ones
        return next(j for j in self.jobs if j.name == name)

    # -- what production does ----------------------------------------------

    def serve(self, only: Optional[Sequence[str]] = None) -> None:
        """Boot every listener the manifest declares, and block."""
        from .serve.app import serve_manifest

        self.bootstrap()
        serve_manifest(self.manifest, only=list(only) if only else None)

    def asgi(self, *, port: Optional[int] = None, only: Optional[Sequence[str]] = None) -> Any:
        """One listener's ASGI app — for a process that runs uvicorn itself,
        or a test client. Picks the listener by *port*, or the only one."""
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
        return build_app(tuple(specs), on_startup=self.manifest.on_startup)

    async def run(self, name: str, *, resume: bool = False) -> Any:
        """Run a job or runbook once and return its record."""
        return await self.job(name).run(resume=resume)

    def run_sync(self, name: str, *, resume: bool = False) -> Any:
        return asyncio.run(self.run(name, resume=resume))

    # -- describing --------------------------------------------------------

    def describe(self) -> Dict[str, Any]:
        """The three lists as plain data: what ``--list`` and the studio read.
        Names and entry points only; nothing is imported to answer this."""
        return {
            "name": self.name,
            "root": str(self.root),
            "graphs": [
                {"name": g.name, "entry": g.entry, "used_by": list(g.used_by)} for g in self.graphs
            ],
            "services": [
                {
                    "name": s.name,
                    "kind": s.kind,
                    "path": s.path,
                    "port": s.port,
                    "session": s.session,
                    "graph": s.graph or None,
                    "app": s.app,
                    "description": s.description,
                }
                for s in self.services
            ],
            "jobs": [
                {
                    "name": j.name,
                    "kind": "runbook" if j.runbook else "job",
                    "graph": j.graph or None,
                    "runbook": j.runbook,
                    "session": j.session,
                    "source": j.source,
                    "sink": j.sink,
                    "schedule": j.schedule,
                    "description": j.description,
                }
                for j in self.manifest.jobs
            ],
        }

    def __repr__(self) -> str:
        return (
            f"Application({self.name!r}, graphs={len(self.graphs)}, "
            f"services={len(self.services)}, jobs={len(self.manifest.jobs)})"
        )
