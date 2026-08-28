"""`operonx.toml` — what a project *is*, parsed by operonx itself.

Until now this file was written for other people's tools. `operonx-lint`,
`operonx-extract` and `operonx-studio` read it; operonx did not. Its
`[[serve]]` block existed because nothing derived from a graph can say
what puts work into it — uvicorn calls an ASGI route, which calls
``engine.start()``, and that hop is not an op — so a human wrote the hop
down by hand and the studio drew an arrow from it.

Declaring the entry point and then not using it is the gap this module
closes. The same block becomes what boots.

Two files, two jobs, and the split is worth stating because it is the
question people ask first:

* ``resources.yaml`` is the resource **hub** — what the project reaches
  out to. LLM gateways, STT and TTS endpoints, ONNX models, thread pools,
  trace sinks. Resolved into objects that *ops* call. Outbound.
* ``operonx.toml`` is the project **manifest** — what the project is. Its
  graphs, and how work arrives at them. Inbound. No op ever asks the hub
  for a serve entry.

Schema
------
``schema = 2`` opts into the layout where ``[[serve]]`` names its graph by
entry point and carries session semantics. A manifest with no ``schema``
key is version 1 — the historical layout, where ``[[graph]]`` names entry
points and ``[[serve]]`` refers to one of those names. Both parse here;
version 1 is normalised into the same objects so callers never branch.

The stamp exists so that an *older* tool meeting a newer manifest fails
loudly. Without it, a stale `operonx-studio` reading a schema-2 file finds
no ``[[graph]]`` blocks and reports "this project has no graphs" — a
silently wrong answer in place of "this manifest is newer than I am".
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

try:                                                  # Python 3.11+
    import tomllib as _toml
except ModuleNotFoundError:                           # 3.10
    import tomli as _toml                             # type: ignore[no-redef]

from operonx.core.loggings import LOGGER
from operonx.core.registry.storage.yaml import _interpolate_env_vars

__all__ = [
    "Manifest",
    "ServeSpec",
    "GraphSpec",
    "ManifestError",
    "MANIFEST_FILENAME",
    "SUPPORTED_SCHEMA",
    "STREAM_KINDS",
    "SESSION_MODES",
]

MANIFEST_FILENAME = "operonx.toml"

#: The highest schema this build understands. A manifest declaring more is
#: refused by name rather than misread.
SUPPORTED_SCHEMA = 2

#: Kinds whose input arrives as a stream of items over time rather than as
#: one request. These are the ones that need a bound: a producer that never
#: stops needs somewhere to push back against.
STREAM_KINDS = frozenset({"websocket", "file", "queue"})

#: How many runs a connection mints, which decides nearly everything else —
#: how failures map to a response, and when the run ends.
SESSION_MODES = frozenset({"per_request", "per_connection", "per_message"})

_ENTRY_RE = re.compile(r"^[\w.]+:[\w.]+$")


class ManifestError(ValueError):
    """A manifest that cannot be trusted to describe a deployment.

    Raised eagerly at parse time. Every message names the file and the
    block, because the alternative is a server that boots and serves
    something subtly other than what was written down.
    """


@dataclass(frozen=True)
class GraphSpec:
    """A graph the project wants a tool to know about.

    In schema 2 this is only for graphs *unreachable* from any endpoint —
    an experiment kept under lint. Anything served names its own entry
    point, and subgraphs are reachable by walking the root, so most
    projects have none of these.
    """

    name: str
    entry: str
    inputs: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ServeSpec:
    """One endpoint: a transport, a graph, and how runs are minted.

    Attributes:
        name: Identifies the endpoint in logs, metrics and
            ``operonx serve --only``. Defaults to the path when absent.
        kind: A registered transport name (``websocket``, ``http``,
            ``asgi``, ``file``, ``queue``) or a ``module:Class`` import
            path to a transport this project wrote itself. The built-ins
            hold no privileged position — they register the same way.
        graph: ``module:function`` entry point of the graph to run.
        session: One of :data:`SESSION_MODES`. Decides run cardinality,
            and with it what an unhandled op failure means: a
            ``per_request`` run has exactly one caller waiting and maps a
            failure to 5xx, where a ``per_connection`` run is a phone call
            that must survive a bad turn.
        max_inflight: Bound on undelivered items. Required for
            :data:`STREAM_KINDS`, because an unbounded queue behind a
            network socket is how this project's Channels came to exist.
        on_session: ``module:function`` returning a ``RunRequest`` from a
            connection. Where ``?call_id=&customer_info=`` becomes real
            inputs — project logic no amount of TOML expresses.
        app: For ``kind = "asgi"``: the foreign app to mount. Health,
            CRUD and admin routes stay someone else's code.
    """

    name: str
    kind: str
    graph: str = ""
    path: str = "/"
    method: str = "POST"
    host: str = "0.0.0.0"
    port: int = 8000
    session: str = "per_request"
    max_inflight: Optional[int] = None
    on_session: Optional[str] = None
    app: Optional[str] = None
    description: str = ""
    options: Dict[str, Any] = field(default_factory=dict)

    @property
    def is_stream(self) -> bool:
        return self.kind in STREAM_KINDS

    @property
    def listener(self) -> Tuple[str, int]:
        """Endpoints sharing this pair share one server.

        Grouping by ``(host, port)`` rather than by a declared server name
        keeps a second concept out of the manifest: a listener is only ever
        an address, so let the address be the identity.
        """
        return (self.host, self.port)


@dataclass(frozen=True)
class Manifest:
    """A parsed, validated `operonx.toml`."""

    schema: int
    project: Dict[str, Any]
    serves: Tuple[ServeSpec, ...]
    graphs: Tuple[GraphSpec, ...]
    fixtures: Dict[str, Dict[str, Any]]
    resources_overlay: Optional[str]
    source: Optional[Path]

    @property
    def name(self) -> str:
        return str(self.project.get("name") or "operonx")

    def serve(self, name: str) -> ServeSpec:
        for spec in self.serves:
            if spec.name == name:
                return spec
        known = ", ".join(s.name for s in self.serves) or "none"
        raise ManifestError(f"no serve entry named {name!r} (have: {known})")

    def listeners(self) -> Dict[Tuple[str, int], Tuple[ServeSpec, ...]]:
        """Endpoints grouped onto the servers that will carry them."""
        out: Dict[Tuple[str, int], list] = {}
        for spec in self.serves:
            out.setdefault(spec.listener, []).append(spec)
        return {addr: tuple(specs) for addr, specs in out.items()}

    # -- loading ---------------------------------------------------------

    @classmethod
    def from_file(cls, path: Path | str) -> "Manifest":
        path = Path(path)
        if not path.is_file():
            raise ManifestError(f"no manifest at {path}")
        try:
            raw = _toml.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:                       # noqa: BLE001
            raise ManifestError(f"{path}: not valid TOML — {exc}") from exc
        return cls.from_dict(raw, source=path)

    @classmethod
    def find(cls, start: Path | str = ".") -> "Manifest":
        """Walk up from `start` to the first `operonx.toml`."""
        here = Path(start).resolve()
        if here.is_file():
            here = here.parent
        for candidate in (here, *here.parents):
            found = candidate / MANIFEST_FILENAME
            if found.is_file():
                return cls.from_file(found)
        raise ManifestError(f"no {MANIFEST_FILENAME} at or above {here}")

    @classmethod
    def from_dict(cls, raw: Dict[str, Any], source: Optional[Path] = None) -> "Manifest":
        where = str(source) if source else "<manifest>"

        # `${VAR}` and `${VAR:default}` land here the same way they do in
        # resources.yaml, using the same implementation — a port that moves
        # between environments should not need a second convention to
        # express. Structure stays in the file; the environment supplies
        # the bindings.
        raw = _interpolate_env_vars(raw)

        schema = raw.get("schema", 1)
        if not isinstance(schema, int):
            raise ManifestError(f"{where}: `schema` must be an integer, got {schema!r}")
        if schema > SUPPORTED_SCHEMA:
            raise ManifestError(
                f"{where}: manifest schema {schema} needs a newer operonx "
                f"(this build understands up to {SUPPORTED_SCHEMA})"
            )
        if schema < 1:
            raise ManifestError(f"{where}: `schema` must be 1 or greater, got {schema}")

        project = dict(raw.get("project") or {})
        resources = raw.get("resources") or {}
        overlay = resources.get("overlay") if isinstance(resources, dict) else None

        graphs = tuple(
            _graph_spec(entry, where, i) for i, entry in enumerate(_as_list(raw.get("graph")))
        )
        by_name = {g.name: g for g in graphs}

        fixtures: Dict[str, Dict[str, Any]] = {}
        for i, block in enumerate(_as_list(raw.get("fixture"))):
            if not isinstance(block, dict):
                raise ManifestError(f"{where}: [[fixture]] #{i} is not a table")
            fname = str(block.get("name") or f"fixture{i}")
            fixtures[fname] = dict(block.get("inputs") or {})
        # Schema 1 kept sample inputs on the graph itself, for lint and
        # studio to run with. They are fixtures wherever they are written,
        # so they are carried as fixtures and `serve` ignores them.
        for g in graphs:
            if g.inputs:
                fixtures.setdefault(g.name, dict(g.inputs))

        serves = tuple(
            _serve_spec(block, where, i, by_name, schema)
            for i, block in enumerate(_as_list(raw.get("serve")))
        )
        _reject_duplicates(serves, where)

        return cls(
            schema=schema,
            project=project,
            serves=serves,
            graphs=graphs,
            fixtures=fixtures,
            resources_overlay=overlay,
            source=source,
        )


# -- parsing helpers -----------------------------------------------------

def _as_list(value: Any) -> list:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def _graph_spec(block: Any, where: str, index: int) -> GraphSpec:
    if not isinstance(block, dict):
        raise ManifestError(f"{where}: [[graph]] #{index} is not a table")
    entry = block.get("entry")
    name = block.get("name")
    if not entry:
        raise ManifestError(f"{where}: [[graph]] {name or index!r} has no `entry`")
    if not _ENTRY_RE.match(str(entry)):
        raise ManifestError(
            f"{where}: [[graph]] {name or index!r} entry {entry!r} is not "
            f"`module:function`"
        )
    return GraphSpec(
        name=str(name or entry),
        entry=str(entry),
        inputs=dict(block.get("inputs") or {}),
    )


def _serve_spec(
    block: Any,
    where: str,
    index: int,
    by_name: Dict[str, GraphSpec],
    schema: int,
) -> ServeSpec:
    if not isinstance(block, dict):
        raise ManifestError(f"{where}: [[serve]] #{index} is not a table")

    kind = str(block.get("kind") or "").strip()
    if not kind:
        raise ManifestError(f"{where}: [[serve]] #{index} has no `kind`")

    path = str(block.get("path") or "/")
    name = str(block.get("name") or path.strip("/").replace("/", "_") or kind)
    label = f"[[serve]] {name!r}"

    graph = str(block.get("graph") or "")
    app = block.get("app")

    if kind == "asgi":
        # A mount point, not a graph. This is the seam that lets health,
        # CRUD and admin routes stay in the project's own code while the
        # manifest still describes the whole product.
        if not app:
            raise ManifestError(f"{where}: {label} is kind 'asgi' but has no `app`")
        if graph:
            raise ManifestError(
                f"{where}: {label} is kind 'asgi' and cannot also name a `graph`"
            )
    else:
        if not graph:
            raise ManifestError(f"{where}: {label} has no `graph`")
        # Schema 1 pointed at a `[[graph]]` by name; schema 2 names the
        # entry point outright. Both normalise to an entry point here so
        # nothing downstream has to know which file it came from.
        if not _ENTRY_RE.match(graph):
            known = by_name.get(graph)
            if known is None:
                have = ", ".join(sorted(by_name)) or "no [[graph]] blocks"
                raise ManifestError(
                    f"{where}: {label} names graph {graph!r}, which is neither "
                    f"a `module:function` entry point nor a declared graph "
                    f"({have})"
                )
            graph = known.entry

    session = str(block.get("session") or _default_session(kind))
    if session not in SESSION_MODES:
        raise ManifestError(
            f"{where}: {label} has session {session!r}; expected one of "
            f"{', '.join(sorted(SESSION_MODES))}"
        )

    max_inflight = block.get("max_inflight")
    if max_inflight is not None and (not isinstance(max_inflight, int) or max_inflight < 1):
        raise ManifestError(
            f"{where}: {label} has max_inflight={max_inflight!r}; expected a "
            f"positive integer"
        )
    if kind in STREAM_KINDS and max_inflight is None:
        # Deliberately not defaulted. operonx guards concurrency and, since
        # transient ports, retention — it does not guard volume, and the
        # one place that needs it most is a socket nobody can slow down.
        # A number nobody chose is how an unbounded queue gets shipped.
        #
        # Required from schema 2 forward only. Schema 1 manifests exist and
        # work today; a parser that refuses to read the files it was
        # written for is no use to anyone, so those get the warning and the
        # unbounded behaviour they already have.
        if schema >= 2:
            raise ManifestError(
                f"{where}: {label} is kind {kind!r} and must set `max_inflight` — "
                f"a stream transport needs a bound to push back against"
            )
        LOGGER.warning(
            f"{where}: {label} is kind {kind!r} with no `max_inflight`; the "
            f"input is unbounded. Set one when moving to schema 2."
        )

    port = block.get("port", 8000)
    try:
        port = int(port)
    except (TypeError, ValueError):
        raise ManifestError(f"{where}: {label} has port {port!r}, which is not a number") from None

    known_keys = {
        "name", "kind", "graph", "path", "method", "host", "port", "session",
        "max_inflight", "on_session", "app", "description",
    }
    options = {k: v for k, v in block.items() if k not in known_keys}

    return ServeSpec(
        name=name,
        kind=kind,
        graph=graph,
        path=path,
        method=str(block.get("method") or "POST").upper(),
        host=str(block.get("host") or "0.0.0.0"),
        port=port,
        session=session,
        max_inflight=max_inflight,
        on_session=(str(block["on_session"]) if block.get("on_session") else None),
        app=(str(app) if app else None),
        description=str(block.get("description") or ""),
        options=options,
    )


def _default_session(kind: str) -> str:
    """The session mode a transport means when it does not say.

    A stream transport holds one connection open and mints one run for it;
    everything else answers one caller at a time.
    """
    return "per_connection" if kind in STREAM_KINDS else "per_request"


def _reject_duplicates(serves: Tuple[ServeSpec, ...], where: str) -> None:
    seen_names: Dict[str, int] = {}
    seen_routes: Dict[Tuple[str, int, str, str], str] = {}
    for spec in serves:
        if spec.name in seen_names:
            raise ManifestError(f"{where}: two [[serve]] blocks named {spec.name!r}")
        seen_names[spec.name] = 1
        route = (spec.host, spec.port, spec.path, spec.method if spec.kind == "http" else "*")
        if route in seen_routes:
            raise ManifestError(
                f"{where}: {spec.name!r} and {seen_routes[route]!r} both serve "
                f"{spec.path} on {spec.host}:{spec.port}"
            )
        seen_routes[route] = spec.name
