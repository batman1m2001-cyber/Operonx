"""Declaring the application in Python: a service is a listener, a graph
and the two door hooks, written where a reader can see all three.

::

    APP = Application(
        "callbot",
        services=[
            Service("call", websocket("/ws/call", port=env("WS_PORT", 9922)),
                    graph=ws_callbot_pipeline, session="per_connection", max_inflight=4000,
                    inputs=["script_data", "vad_state"],
                    variants={"educa_hr": dict(agent=educa_hr.AGENT, turn=educa_hr.graph.turn)},
                    ingress=["audio_in"], egress=["play", "store_record"],
                    on_session=open_call, on_close=close_call),
            Service("admin", asgi("/", port=9923), app=admin.app),
        ],
        on_startup=[startup.warmup],
    )

`operonx.toml` then says only what is not code — the project's name, its
import roots, and ``app = "app.main:APP"`` so ``operonx-serve``, the
jobs CLI and the studio find the object. The same declarations in TOML
keep working; :func:`Service` builds the same :class:`ServeSpec` the
manifest parser does, so nothing downstream knows which way it came.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Sequence, Union

from .manifest import SESSION_MODES, STREAM_KINDS, ManifestError, ServeSpec, _default_session

__all__ = ["Listener", "Service", "asgi", "env", "http", "websocket"]


def env(name: str, default: Any = None) -> Any:
    """``${NAME:default}`` for Python: the variable's value, cast to the
    default's type when the default is a number."""
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    if isinstance(default, bool):
        return raw.strip().lower() in ("1", "true", "yes", "on")
    if isinstance(default, int):
        return int(raw)
    if isinstance(default, float):
        return float(raw)
    return raw


@dataclass(frozen=True)
class Listener:
    """Where a service is reached: a transport kind and an address."""

    kind: str
    path: str = "/"
    port: int = 8000
    host: str = "0.0.0.0"
    method: str = "POST"


def websocket(path: str, port: int = 8000, host: str = "0.0.0.0") -> Listener:
    return Listener("websocket", path, int(port), host)


def http(method: str, path: str, port: int = 8000, host: str = "0.0.0.0") -> Listener:
    return Listener("http", path, int(port), host, str(method).upper())


def asgi(path: str = "/", port: int = 8000, host: str = "0.0.0.0") -> Listener:
    return Listener("asgi", path, int(port), host)


def Service(  # noqa: N802 — reads as a declaration
    name: str,
    listener: Listener,
    *,
    graph: Any = None,
    app: Any = None,
    session: Optional[str] = None,
    max_inflight: Optional[int] = None,
    concurrency: Optional[int] = None,
    trace: Optional[Sequence[str]] = None,
    inputs: Optional[Sequence[str]] = None,
    variants: Optional[Mapping[str, Mapping[str, Any]]] = None,
    ingress: Sequence[str] = (),
    egress: Sequence[str] = (),
    on_session: Any = None,
    on_close: Any = None,
    description: str = "",
    **options: Any,
) -> ServeSpec:
    """One endpoint, as :class:`ServeSpec` — the same record ``[[serve]]``
    parses to, with objects where the manifest has ``module:attr``.

    ``graph`` is a ``@graph`` (or a factory, with ``variants``), ``app`` an
    ASGI app for an ``asgi`` listener. ``inputs`` names the graph's runtime
    inputs the door will build — checked against the graph when it is
    compiled, so a mismatch fails at boot and names the parameter.
    ``ingress`` / ``egress`` name the door ops, for whoever draws the graph.
    """
    label = f"Service({name!r})"
    kind = listener.kind
    if kind == "asgi":
        if app is None:
            raise ManifestError(f"{label} is an asgi listener and needs app=")
        if graph is not None:
            raise ManifestError(f"{label} is an asgi listener and cannot also take a graph")
    elif graph is None:
        raise ManifestError(f"{label} needs graph=")
    elif app is not None:
        raise ManifestError(f"{label} takes a graph or an app, not both")

    session = session or _default_session(kind)
    if session not in SESSION_MODES:
        raise ManifestError(
            f"{label} has session {session!r}; expected one of {', '.join(sorted(SESSION_MODES))}"
        )
    if kind in STREAM_KINDS and max_inflight is None:
        raise ManifestError(
            f"{label} is a {kind} listener and must set max_inflight= — "
            f"a stream transport needs a bound to push back against"
        )
    if max_inflight is not None and (not isinstance(max_inflight, int) or max_inflight < 1):
        raise ManifestError(
            f"{label} has max_inflight={max_inflight!r}; expected a positive integer"
        )

    variants_out: Dict[str, Dict[str, Any]] = {}
    for v_name, bind in (variants or {}).items():
        if not isinstance(bind, Mapping):
            raise ManifestError(f"{label} variant {v_name!r} must be a mapping of parameters")
        variants_out[str(v_name)] = dict(bind)

    opts: Dict[str, Any] = dict(options)
    if concurrency is not None:
        opts["concurrency"] = int(concurrency)
    if trace:
        opts["trace"] = list(trace)

    return ServeSpec(
        name=name,
        kind=kind,
        graph=graph if graph is not None else "",
        path=listener.path,
        method=listener.method,
        host=listener.host,
        port=int(listener.port),
        session=session,
        max_inflight=max_inflight,
        on_session=on_session,
        on_close=on_close,
        app=app,
        description=description,
        options=opts,
        variants=variants_out,
        inputs=tuple(inputs or ()),
        ingress=tuple(ingress),
        egress=tuple(egress),
    )


def ref_name(value: Union[str, Any]) -> str:
    """How an object is written down: ``module:qualname`` for a function or
    class, its ``repr`` otherwise. A string is already a reference."""
    if isinstance(value, str):
        return value
    module = getattr(value, "__module__", None)
    qual = getattr(value, "__qualname__", None) or getattr(value, "__name__", None)
    if module and qual:
        return f"{module}:{qual}"
    return repr(value)


# ── what `Application(...)` delegates to ────────────────────────────────


def project_root(start: Path) -> Path:
    """The nearest directory at or above *start* holding ``operonx.toml``,
    else *start* itself: where relative paths of a Python-declared
    application resolve."""
    for candidate in (start, *start.parents):
        if (candidate / "operonx.toml").is_file():
            return candidate
    return start


def manifest_from(
    name: str,
    *,
    services: Sequence[ServeSpec],
    on_startup: Sequence[Any],
    root: Path,
    src: Sequence[str],
    resources: Optional[str],
    description: str,
) -> Any:
    """The :class:`Manifest` a Python declaration amounts to — the same
    record ``operonx.toml`` parses to, so nothing downstream knows which
    way the application came."""
    from .manifest import Manifest, _reject_duplicates

    specs = tuple(services)
    for spec in specs:
        if not isinstance(spec, ServeSpec):
            raise ManifestError(f"services= takes Service(...) entries, got {type(spec).__name__}")
    _reject_duplicates(specs, f"Application({name!r})")
    overlay = resources if resources and (root / resources).is_file() else None
    return Manifest(
        project={"name": name, "description": description},
        serves=specs,
        graphs=(),
        fixtures={},
        resources_overlay=overlay,
        source=None,
        on_startup=tuple(on_startup),
        jobs=(),
        src=tuple(src),
        base=root,
    )


def describe_service(s: ServeSpec) -> Dict[str, Any]:
    return {
        "name": s.name,
        "kind": s.kind,
        "path": s.path,
        "host": s.host,
        "port": s.port,
        "session": s.session,
        "graph": ref_name(s.graph) if s.graph else None,
        "variants": list(s.variants),
        "inputs": list(s.inputs),
        "ingress": list(s.ingress),
        "egress": list(s.egress),
        "on_session": ref_name(s.on_session) if s.on_session else None,
        "on_close": ref_name(s.on_close) if s.on_close else None,
        "app": ref_name(s.app) if s.app else None,
        "description": s.description,
    }


def describe_job(job: Any) -> Dict[str, Any]:
    """A `Job` or `Runbook` object as the same plain data a ``[[job]]``
    block describes to."""
    if hasattr(job, "jobs") and not hasattr(job, "source"):  # a Runbook
        return {
            "name": job.name,
            "kind": "runbook",
            "graph": None,
            "runbook": ref_name(job),
            "session": None,
            "source": None,
            "sink": None,
            "schedule": getattr(job, "schedule", None),
            "description": getattr(job, "description", "") or "",
        }
    d = job.describe()
    return {
        "name": job.name,
        "kind": "job",
        "graph": d.get("graph"),
        "runbook": None,
        "session": d.get("session"),
        "source": d.get("source"),
        "sink": d.get("sink"),
        "schedule": d.get("schedule"),
        "description": d.get("description") or "",
    }


def load_declared(manifest: Any) -> Any:
    """The `Application` an ``operonx.toml`` points at with ``[project]
    app = "module:APP"``, carrying the file's own root, import roots and
    resources overlay. The file is where the project is found; the object
    is what it declares."""
    from dataclasses import replace

    from .serve.registry import load_object

    obj = load_object(manifest.app_entry, field="[project] app")
    from .application import Application

    if not isinstance(obj, Application):
        raise ManifestError(
            f"[project] app {manifest.app_entry!r} is a {type(obj).__name__}, not an Application"
        )
    project = {**manifest.project, **{k: v for k, v in obj.manifest.project.items() if v}}
    obj.manifest = replace(
        obj.manifest,
        project=project,
        source=manifest.source,
        src=manifest.src,
        graphs=obj.manifest.graphs or manifest.graphs,
        fixtures={**manifest.fixtures, **obj.manifest.fixtures},
        resources_overlay=obj.manifest.resources_overlay or manifest.resources_overlay,
    )
    obj._bootstrapped = False  # the file's roots may differ from the object's guess
    return obj


def build_job(spec: Any, root: Path) -> Any:
    """A ``[[job]]`` block as its `Job` or `Runbook` object."""
    from .jobs import Job, Runbook
    from .serve.registry import load_object

    if spec.runbook:
        runbook = load_object(spec.runbook, field=f"[[job]] {spec.name!r} runbook")
        if not isinstance(runbook, Runbook):
            raise ManifestError(
                f"[[job]] {spec.name!r} runbook {spec.runbook!r} is a "
                f"{type(runbook).__name__}, not a Runbook"
            )
        if spec.record_dir:
            rd = Path(spec.record_dir)
            runbook.record_dir = rd if rd.is_absolute() else root / rd
        return runbook
    return Job.from_spec(spec, root)


def describe_jobspec(j: Any) -> Dict[str, Any]:
    """A ``[[job]]`` block as plain data, without importing the project."""
    return {
        "name": j.name,
        "kind": "runbook" if j.runbook else "job",
        "graph": j.graph or None,
        "runbook": j.runbook,
        "session": None if j.runbook else j.session,
        "source": j.source,
        "sink": j.sink,
        "schedule": j.schedule,
        "description": j.description,
    }


def graph_refs(manifest: Any) -> list:
    """Every graph the manifest names, once each, as
    ``(name, entry, used_by, bind, graph_object)`` — the ``[[graph]]``
    blocks, then whatever services and jobs point at; a door with variants
    contributes one per variant."""
    by_entry: Dict[str, Any] = {}
    variants: list = []
    for g in manifest.graphs:
        by_entry.setdefault(g.entry, [g.name, [], None])
    for s in manifest.serves:
        if not s.graph:
            continue
        entry = ref_name(s.graph)
        base = entry.rpartition(":")[2]
        if s.variants:
            for v, bind in s.variants.items():
                variants.append((f"{base}[{v}]", entry, (f"serve:{s.name}",), dict(bind), s.graph))
        else:
            by_entry.setdefault(entry, [base, [], s.graph])[1].append(f"serve:{s.name}")
    for j in manifest.jobs:
        if j.graph:
            by_entry.setdefault(j.graph, [j.graph.rpartition(":")[2], [], None])[1].append(
                f"job:{j.name}"
            )
    plain = [(n, e, tuple(u), {}, obj) for e, (n, u, obj) in by_entry.items()]
    return plain + variants
