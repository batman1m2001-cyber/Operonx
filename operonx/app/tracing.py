"""`[tracing]` in ``operonx.toml`` — which trace sinks are on, in one place.

::

    [tracing]
    sinks = ["local", "trace_langfuse:edupia", "trace_clickhouse:default"]

    [tracing.services.call]        # one service, overridden
    sinks = ["local", "trace_langfuse:edupia"]

    [tracing.jobs.backfill_call_logs]
    sinks = []                     # this job is not traced

The file says *which* sinks a run goes to; ``resources.yaml`` keeps how to
reach each one. ``"local"`` is the built-in local consumer (the one a job
records to when nothing is configured); any other entry is a resource key,
resolved through the hub exactly as ``trace=[...]`` entries are.

Precedence, most specific first:

1. ``[tracing.services.<name>]`` / ``[tracing.jobs.<name>]``;
2. the service's or job's own ``trace=`` (Python, or ``trace =`` on its
   ``[[serve]]`` / ``[[job]]`` block);
3. ``[tracing] sinks``;
4. ``Application(trace=...)`` (or ``[project] trace``);
5. the built-in default: a job records locally, a service traces nothing.

The toml is the operator's switch, so its per-service entry beats
everything; a setting on one service beats any project-wide one, wherever
it was written. ``sinks = []`` is "not traced" at its level, never "not
set".

Every level resolves to one list handed to one engine, and the engine
hands its one ``WorkflowTrace`` to each consumer, so every sink of a run
sees the same trace id — the caller's ``?trace_id=`` included.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from .manifest import ManifestError, ServeSpec

__all__ = [
    "LOCAL",
    "Tracing",
    "check_names",
    "check_sinks",
    "parse_tracing",
    "pick",
    "project_sinks",
    "settle_serves",
    "sink_name",
]

#: The built-in local consumer, by the name ``[tracing]`` gives it.
LOCAL = "local"

# `category:name`, the shape of every resource key.
_SINK_RE = re.compile(r"^[A-Za-z_][\w-]*:[\w.\-]+$")

_KEYS = ("jobs", "services", "sinks")


@dataclass(frozen=True)
class Tracing:
    """A parsed ``[tracing]`` table. ``sinks`` is ``None`` when the table
    does not set it — the levels below then decide."""

    sinks: Optional[Tuple[str, ...]] = None
    services: Dict[str, Tuple[str, ...]] = field(default_factory=dict)
    jobs: Dict[str, Tuple[str, ...]] = field(default_factory=dict)


def parse_tracing(raw: Any, where: str) -> Optional[Tracing]:
    """``[tracing]`` as a :class:`Tracing`, or ``None`` when absent.

    Every mistake raises here, naming the key: a typo such as ``sink =``
    must not read as "nothing configured" and quietly trace nowhere."""
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ManifestError(f"{where}: [tracing] must be a table")
    _no_stray(raw, _KEYS, f"{where}: [tracing]")
    sinks = _sinks(raw["sinks"], f"{where}: [tracing] sinks") if "sinks" in raw else None
    overrides = {}
    for kind in ("services", "jobs"):
        table = raw.get(kind, {})
        if not isinstance(table, dict):
            raise ManifestError(
                f"{where}: [tracing.{kind}] must be a table of [tracing.{kind}.<name>]"
            )
        out: Dict[str, Tuple[str, ...]] = {}
        for name, block in table.items():
            label = f"{where}: [tracing.{kind}.{name}]"
            if not isinstance(block, dict):
                raise ManifestError(f"{label} must be a table with `sinks`")
            _no_stray(block, ("sinks",), label)
            if "sinks" not in block:
                raise ManifestError(f"{label} has no `sinks` (write `sinks = []` to trace nothing)")
            out[str(name)] = _sinks(block["sinks"], f"{label} sinks")
        overrides[kind] = out
    return Tracing(sinks=sinks, services=overrides["services"], jobs=overrides["jobs"])


def _no_stray(table: Dict[str, Any], known: Sequence[str], label: str) -> None:
    for key in table:
        if key not in known:
            raise ManifestError(f"{label} has unknown key {key!r} (known: {', '.join(known)})")


def _sinks(value: Any, label: str) -> Tuple[str, ...]:
    if not isinstance(value, list):
        raise ManifestError(
            f"{label} must be a list of sink names, got {type(value).__name__} {value!r}"
        )
    seen: List[str] = []
    for item in value:
        if not isinstance(item, str) or not (item == LOCAL or _SINK_RE.match(item)):
            raise ManifestError(
                f'{label}: {item!r} is neither "local" nor `category:name` '
                "(a resource key in resources.yaml)"
            )
        if item in seen:
            raise ManifestError(f"{label} lists {item!r} twice")
        seen.append(item)
    return tuple(seen)


def check_names(
    tracing: Optional[Tracing],
    where: str,
    services: Optional[Sequence[ServeSpec]],
    jobs: Optional[Iterable[str]],
) -> None:
    """Every ``[tracing.services.<n>]`` names a service with runs, every
    ``[tracing.jobs.<n>]`` a job. ``None`` skips that check — a runbook's
    members, say, are not known until it is imported."""
    if tracing is None:
        return
    by_name = {s.name: s for s in services or ()}
    for name in tracing.services if services is not None else ():
        spec = by_name.get(name)
        if spec is None:
            have = ", ".join(by_name) or "none"
            raise ManifestError(
                f"{where}: [tracing.services.{name}] names no service (have: {have})"
            )
        if spec.kind == "asgi":
            raise ManifestError(
                f"{where}: [tracing.services.{name}] is an asgi mount — it runs no graph, "
                "so it has no runs to trace"
            )
    if jobs is None:
        return
    known = list(dict.fromkeys(jobs))
    for name in tracing.jobs:
        if name not in known:
            raise ManifestError(
                f"{where}: [tracing.jobs.{name}] names no job (have: {', '.join(known) or 'none'})"
            )


def pick(
    *,
    overrides: Sequence[Tuple[Optional[Sequence[Any]], str]],
    own: Optional[Sequence[Any]],
    own_label: str,
    tracing: Optional[Tracing],
    app: Optional[Sequence[Any]],
) -> Tuple[Optional[List[Any]], str]:
    """The sinks one service or job uses, and the level they came from.

    ``overrides`` are the ``[tracing.*.<name>]`` lists that name it, most
    specific first (a runbook member's own entry, then its runbook's).
    ``None`` at a level means "not set there"; ``[]`` is a choice. Returns
    ``(None, "default")`` when no level says anything."""
    for sinks, label in overrides:
        if sinks is not None:
            return list(sinks), label
    if own is not None:
        return list(own), own_label
    if tracing is not None and tracing.sinks is not None:
        return list(tracing.sinks), "[tracing]"
    if app is not None:
        return list(app), "application"
    return None, "default"


def settle_serves(
    serves: Sequence[ServeSpec], app: Optional[Sequence[Any]], tracing: Optional[Tracing]
) -> Tuple[ServeSpec, ...]:
    """Each service with ``options["trace"]`` set to what it will use.

    A pure function of what was declared (``trace_own``), so applying it
    again — the file's ``[tracing]`` after a Python application already
    took its own default — gives the same answer as applying it once."""
    out = []
    for spec in serves:
        if spec.kind == "asgi":
            out.append(spec)
            continue
        own = spec.trace_own
        if own is None and spec.trace_from == "default" and "trace" in spec.options:
            # a ServeSpec built by hand, with `options={"trace": ...}`:
            # that is its own choice, not something inherited
            own = tuple(spec.options["trace"] or ())
        override = tracing.services.get(spec.name) if tracing else None
        sinks, source = pick(
            overrides=[(override, f"[tracing.services.{spec.name}]")],
            own=own,
            own_label="service",
            tracing=tracing,
            app=app,
        )
        options = {k: v for k, v in spec.options.items() if k != "trace"}
        if sinks is not None:
            options["trace"] = sinks
        out.append(replace(spec, options=options, trace_own=own, trace_from=source))
    return tuple(out)


def job_override(tracing: Optional[Tracing], name: str, runbook: Optional[str]) -> list:
    """The ``[tracing.jobs.*]`` levels that name a job, most specific first."""
    if tracing is None:
        return []
    out = [(tracing.jobs.get(name), f"[tracing.jobs.{name}]")]
    if runbook is not None:
        out.append((tracing.jobs.get(runbook), f"[tracing.jobs.{runbook}]"))
    return out


def check_sinks(app: str, services: Sequence[ServeSpec] = (), jobs: Sequence[Any] = ()) -> None:
    """Every trace sink these services and jobs name is in the resource
    hub — checked as they start, so a sink missing from
    ``resources.yaml`` stops the start naming the key and the level
    that chose it, instead of surfacing as a stack trace from inside an
    engine (or, in a pooled worker, in a log nobody reads)."""
    # (key, level that chose it) -> who uses it
    wanted: Dict[Tuple[str, str], List[str]] = {}
    for s in services:
        for sink in s.options.get("trace") or []:
            if isinstance(sink, str) and sink != LOCAL:
                wanted.setdefault((sink, s.trace_from), []).append(f"service {s.name!r}")
    for job in jobs:
        members = job.jobs if hasattr(job, "jobs") and not hasattr(job, "source") else [job]
        for j in members:
            for sink in getattr(j, "trace", None) or []:
                if isinstance(sink, str) and sink != LOCAL:
                    source = getattr(j, "_trace_from", "job")
                    wanted.setdefault((sink, source), []).append(f"job {j.name!r}")
    _check_wanted(app, wanted)


def _check_wanted(app: str, wanted: Dict[Tuple[str, str], List[str]]) -> None:
    """Raise naming each ``(key, level)`` in *wanted* the hub does not
    declare, and who uses it."""
    if not wanted:
        return
    from operonx.core.registry import ResourceHub

    try:
        hub = ResourceHub.instance()
    except RuntimeError:
        hub = None
    # `declares`, not `has`: nothing is parsed (and cached) before the
    # project has registered its own sink categories
    missing = [ks for ks in wanted if hub is None or not hub.declares(ks[0])]
    if not missing:
        return
    where = str(hub.source_path or "the resource hub") if hub else None
    lines = [
        f"  {key!r} (from {source}) is not in "
        f"{where or 'a resources.yaml — none is loaded'}; used by "
        + ", ".join(dict.fromkeys(wanted[(key, source)]))
        for key, source in missing
    ]
    if hub is not None:
        try:
            have = sorted(k for k in hub._storage.load_all() if k.startswith(("trace", "run_")))
        except Exception:  # noqa: BLE001 — only a hint; the error above is the point
            have = []
        if have:
            lines.append(f"  trace resources it has: {', '.join(have)}")
    raise ManifestError(f"{app}: a trace sink is missing from the resources:\n" + "\n".join(lines))


def project_sinks() -> List[str]:
    """The sinks ``Operon(trace="project")`` uses: the project's own.

    The project is the one an ``Application`` bootstrapped, else the
    nearest ``operonx.toml`` at or above the working directory. Its sinks
    are chosen as a service's or a job's are, minus the levels a script
    has no name for: ``[tracing] sinks``, else ``[project] trace``, else
    — as for a job, since a run that asked to be traced must not go
    untraced by omission — the local consumer. ``sinks = []`` traces
    nothing. An ``Application(trace=...)`` written in Python is not
    read: that would import the application from inside an engine.

    Every resource key is checked against the hub here, so a missing one
    fails naming the key and the level that chose it.
    """
    from operonx.core.workflow_trace import active_project

    from .manifest import MANIFEST_FILENAME, Manifest

    root = active_project()
    if root is None:
        raise ValueError(
            f'trace="project" traces where the project says, but there is no project: '
            f"no {MANIFEST_FILENAME} at or above {Path.cwd()}, and no Application "
            f'was bootstrapped. Run from inside the project, or pass trace="local" '
            f"or a list of sinks."
        )
    manifest = Manifest.from_file(Path(root) / MANIFEST_FILENAME)
    sinks, source = pick(
        overrides=[],
        own=None,
        own_label="script",
        tracing=getattr(manifest, "tracing", None),
        app=manifest.project.get("trace"),
    )
    if sinks is None:
        sinks, source = [LOCAL], "default"
    where = str(manifest.source or root)
    _check_wanted(
        where,
        {(s, source): ['trace="project"'] for s in sinks if isinstance(s, str) and s != LOCAL},
    )
    return list(sinks)


def sink_name(sink: Any) -> str:
    """How a sink is shown: its key, ``local`` for the local consumer, or
    the type of any other consumer object."""
    if isinstance(sink, str):
        return sink
    from operonx.telemetry.consumers.local import LocalConsumer

    if type(sink) is LocalConsumer:
        return LOCAL
    return type(sink).__name__
