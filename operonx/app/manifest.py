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

Shape
-----
``[[serve]]`` names the graph it runs by entry point, directly::

    [[serve]]
    kind         = "websocket"
    path         = "/ws/call"
    graph        = "pipeline.graph:ws_callbot_pipeline"
    session      = "per_connection"
    max_inflight = 4000

``[[graph]]`` is for graphs nothing serves — an example, an experiment, a
subgraph worth linting on its own. A served graph does not need one.

There is deliberately no version key. One was tried and removed: its only
real job was letting manifests written before ``max_inflight`` existed
skip declaring a bound, which is a kindness to files that this project
would rather just update. An unbounded queue behind a socket is how
`operonx.io.Channel` came to exist, so the bound is required of everyone,
always.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

try:  # Python 3.11+
    import tomllib as _toml
except ModuleNotFoundError:  # 3.10
    import tomli as _toml  # type: ignore[no-redef]

from operonx.core.registry.storage.yaml import _interpolate_env_vars

__all__ = [
    "Manifest",
    "ServeSpec",
    "GraphSpec",
    "ManifestError",
    "MANIFEST_FILENAME",
    "STREAM_KINDS",
    "SESSION_MODES",
    "DOOR_CODECS",
    "door_codec",
]

MANIFEST_FILENAME = "operonx.toml"

#: Kinds whose input arrives as a stream of items over time rather than as
#: one request. These are the ones that need a bound: a producer that never
#: stops needs somewhere to push back against.
STREAM_KINDS = frozenset({"websocket", "file", "queue"})

#: How many runs a connection mints, which decides nearly everything else —
#: how failures map to a response, and when the run ends.
SESSION_MODES = frozenset({"per_request", "per_connection", "per_message"})


#: How a door reads what its caller sends (``codec =``): ``"json"`` decodes
#: every body and text frame, ``"text"`` passes text through. Bytes frames
#: are bytes either way. One rule for every door, so a graph served on
#: HTTP and on a websocket receives the same item for the same payload.
DOOR_CODECS = ("json", "text")

#: The kinds whose payload a codec reads.
CODEC_KINDS = frozenset({"http", "websocket", "webhook"})


def door_codec(spec: "ServeSpec") -> str:
    """The codec a door reads its payloads with; ``"json"`` when unset.

    Raises:
        ManifestError: the door declares a codec that is not one of
            :data:`DOOR_CODECS`.
    """
    codec = spec.options.get("codec", "json")
    check_codec(codec, f"[[serve]] {spec.name!r}")
    return codec


def check_codec(codec: Any, label: str) -> None:
    if codec not in DOOR_CODECS:
        raise ManifestError(
            f"{label} has codec {codec!r}; expected one of {', '.join(DOOR_CODECS)} "
            '("json" decodes each body and text frame, "text" passes text through)'
        )


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

    For graphs nothing serves — an example, an experiment, a subgraph
    worth linting on its own. Anything served names its own entry point in
    its ``[[serve]]`` block, and subgraphs are reachable by walking the
    root, so a server project usually has none of these.
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
        on_close: ``module:function(session, handle)`` run after the run
            ends, however it ended. The symmetric half of ``on_session``:
            whatever was opened at the door gets closed here — a counter
            decremented, a record written, a last message sent to a peer
            that may already be gone.
        app: For ``kind = "asgi"``: the foreign app to mount. Health,
            CRUD and admin routes stay someone else's code.
        resume: For an ``http`` door whose runs can stop for a human (an
            agent waiting on an approval): the graph that continues one.
            The door then also answers ``POST <path>/resume`` with it —
            same codec, hooks and streaming as the door itself
            (:meth:`resume_spec`). A websocket door resumes on its own
            connection and takes none.
    """

    name: str
    kind: str
    #: A ``module:attr`` string from the manifest, or the object itself
    #: when the application is declared in Python (`operonx.app.Service`).
    #: Same for ``on_session``, ``on_close`` and ``app``.
    graph: Any = ""
    path: str = "/"
    method: str = "POST"
    host: str = "0.0.0.0"
    port: int = 8000
    session: str = "per_request"
    max_inflight: Optional[int] = None
    on_session: Any = None
    on_close: Any = None
    app: Any = None
    resume: Any = None
    description: str = ""
    options: Dict[str, Any] = field(default_factory=dict)
    # One door, several compiled graphs: each variant binds the graph's
    # build-time parameters (`module:attr` values are loaded, the rest are
    # literals or objects) and `RunRequest.variant` picks one per session.
    variants: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    #: Worker processes behind this endpoint's address. Services sharing an
    #: address share its workers, so they must agree.
    workers: int = 1
    #: Hooks run before the listener accepts, in each of its workers — and
    #: only its: ``module:attr`` strings, or the callables themselves.
    on_startup: Tuple[Any, ...] = ()
    #: The trace consumers the service itself declared (``trace=`` /
    #: ``trace =``), ``None`` when it named none. ``options["trace"]`` is
    #: what it will use once ``[tracing]`` and the application's default
    #: are applied; this is kept so applying them again is not mistaken
    #: for the service's own choice.
    trace_own: Optional[Tuple[Any, ...]] = field(default=None, compare=False)
    #: Where ``options["trace"]`` came from (see `operonx.app.tracing`).
    trace_from: str = field(default="default", compare=False)

    @property
    def is_stream(self) -> bool:
        return self.kind in STREAM_KINDS

    def resume_spec(self) -> Optional["ServeSpec"]:
        """The door ``resume`` adds: ``POST <path>/resume`` running the
        resume graph, named ``<name>.resume``, with this door's codec,
        hooks, trace and address. ``None`` when there is no resume graph."""
        if self.resume is None:
            return None
        return replace(
            self,
            name=f"{self.name}.resume",
            graph=self.resume,
            path=self.path.rstrip("/") + "/resume",
            method="POST",
            resume=None,
            description=f"resumes {self.name}'s runs",
        )

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

    project: Dict[str, Any]
    serves: Tuple[ServeSpec, ...]
    graphs: Tuple[GraphSpec, ...]
    fixtures: Dict[str, Dict[str, Any]]
    resources_overlay: Optional[str]
    source: Optional[Path]
    on_startup: Tuple[Any, ...] = ()
    # `[project] src = ["src"]`: the import roots, relative to `root`.
    # A project that keeps its packages under `src/` says so once here
    # instead of in every entry point.
    src: Tuple[str, ...] = (".",)
    #: Where relative paths resolve when there is no file (an application
    #: declared in Python): the directory the declaration was made in.
    base: Optional[Path] = None
    #: ``[tracing]``: which trace sinks are on (`operonx.app.tracing`).
    tracing: Any = None
    #: ``[[queue]]``: review queues (`operonx.app.evals.queues.QueueSpec`).
    queues: Tuple[Any, ...] = ()
    #: ``[jobs] dir``: where the application's jobs record their runs,
    #: relative to the root (default ``.operonx/jobs``).
    jobs_dir: Optional[str] = None

    @property
    def name(self) -> str:
        return str(self.project.get("name") or "operonx")

    @property
    def root(self) -> Path:
        """The directory the manifest lives in; relative paths resolve here."""
        if self.source:
            return self.source.parent
        return self.base or Path.cwd()

    @property
    def app_entry(self) -> Optional[str]:
        """``[project] app = "module:APP"`` — the application declared in
        Python, which this file then only points at."""
        value = self.project.get("app")
        return str(value) if value else None

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
        except Exception as exc:  # noqa: BLE001
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

        project = dict(raw.get("project") or {})
        # `operonx serve` owns the process, so it owns what has to happen
        # before the first request: sizing a thread pool, warming a model
        # so the first caller does not pay for the connection setup. These
        # are real work with nowhere else to live once the hand-written
        # server is gone.
        on_startup = tuple(str(h) for h in _as_list(project.get("on_startup")))
        for hook in on_startup:
            if not _ENTRY_RE.match(hook):
                raise ManifestError(f"{where}: on_startup {hook!r} is not `module:function`")
        app_entry = project.get("app")
        if app_entry is not None and not _ENTRY_RE.match(str(app_entry)):
            raise ManifestError(f"{where}: [project] app {app_entry!r} is not `module:attr`")
        resources = raw.get("resources") or {}
        overlay = resources.get("overlay") if isinstance(resources, dict) else None
        src = tuple(str(x) for x in _as_list(project.get("src"))) or (".",)

        graphs = tuple(
            _graph_spec(entry, where, i) for i, entry in enumerate(_as_list(raw.get("graph")))
        )

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
            _serve_spec(block, where, i) for i, block in enumerate(_as_list(raw.get("serve")))
        )
        _reject_duplicates(serves, where)
        from .tracing import check_names, parse_tracing, settle_serves

        tracing = parse_tracing(raw.get("tracing"), where)
        # `[project] trace = [...]`: the consumers every service and job
        # uses unless it declares its own — the application's default,
        # spelled in the file.
        if "trace" in project:
            project["trace"] = [str(t) for t in _as_list(project.get("trace"))]
            if tracing is not None and tracing.sinks is not None:
                # Two project-wide lists in one file, at two precedence
                # levels (an `Application(trace=...)` beats the first and
                # loses to the second): whichever one a reader edits, the
                # other may be the one that applies.
                raise ManifestError(
                    f"{where}: [project] trace and [tracing] sinks both set the project's "
                    "sinks — keep [tracing] sinks and remove [project] trace"
                )
        serves = settle_serves(serves, project.get("trace"), tracing)

        if raw.get("job"):
            raise ManifestError(
                f"{where}: [[job]] blocks are gone (operonx 1.17): declare each Job or Eval "
                "in Python, in app/main.py, and list it in Application(jobs=[...])"
            )
        if app_entry is None:
            # With `app =` the services are the object's, checked when it is
            # loaded (`load_declared`).
            check_names(tracing, where, serves, [])
        queues = _queue_specs(_as_list(raw.get("queue")), where)
        jobs_table = raw.get("jobs") or {}
        if not isinstance(jobs_table, dict) or set(jobs_table) - {"dir"}:
            raise ManifestError(f'{where}: [jobs] takes one key, dir = "<folder>"')

        return cls(
            project=project,
            serves=serves,
            graphs=graphs,
            fixtures=fixtures,
            resources_overlay=overlay,
            source=source,
            on_startup=on_startup,
            src=src,
            tracing=tracing,
            queues=queues,
            jobs_dir=str(jobs_table["dir"]) if jobs_table.get("dir") else None,
        )


def with_default_trace(serves: Tuple["ServeSpec", ...], trace: Any) -> Tuple["ServeSpec", ...]:
    """Services that declare no ``trace`` take the application's.

    A service that says ``trace=[]`` keeps its silence: ``None`` inherits,
    an empty list is a decision. ``[tracing]`` aside — see
    `operonx.app.tracing.settle_serves`, which this is with no table."""
    from .tracing import settle_serves

    return settle_serves(serves, trace, None)


# -- parsing helpers -----------------------------------------------------


def check_resume(resume: Any, kind: str, variants: Any, label: str) -> None:
    """``resume`` belongs to a plain ``http`` door: a websocket door resumes
    on its own connection, and a door with variants would need a resume
    graph per variant, which nothing declares."""
    if resume is None:
        return
    if kind != "http":
        raise ManifestError(
            f"{label} has resume= on a {kind} door; resume= is for an http door "
            "(POST <path>/resume) — a websocket door resumes on its own connection"
        )
    if variants:
        raise ManifestError(
            f"{label} has resume= and variants; a door with variants cannot take one "
            "resume graph for all of them — declare a door per variant"
        )
    if isinstance(resume, str) and not _ENTRY_RE.match(resume):
        raise ManifestError(f"{label} resume {resume!r} is not a `module:attr` entry point")


def _variants(raw: Any, where: str, label: str, kind: str) -> Dict[str, Dict[str, Any]]:
    """`[serve.variants]`: a table of tables, each binding parameters of the `@graph`."""
    if raw is None:
        return {}
    if kind == "asgi":
        raise ManifestError(f"{where}: {label} is kind 'asgi' and cannot have variants")
    if not isinstance(raw, dict) or not raw:
        raise ManifestError(f"{where}: {label} variants must be a non-empty table of tables")
    out: Dict[str, Dict[str, Any]] = {}
    for name, bind in raw.items():
        if not isinstance(bind, dict):
            raise ManifestError(
                f"{where}: {label} variant {name!r} must be a table of the graph's "
                f"parameters, got {type(bind).__name__}"
            )
        out[str(name)] = dict(bind)
    return out


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
            f"{where}: [[graph]] {name or index!r} entry {entry!r} is not `module:function`"
        )
    return GraphSpec(
        name=str(name or entry),
        entry=str(entry),
        inputs=dict(block.get("inputs") or {}),
    )


def _serve_spec(block: Any, where: str, index: int) -> ServeSpec:
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
            raise ManifestError(f"{where}: {label} is kind 'asgi' and cannot also name a `graph`")
    else:
        if not graph:
            raise ManifestError(f"{where}: {label} has no `graph`")
        # An entry point, named outright. `[[graph]]` used to exist to give
        # entry points names so `[[serve]]` could refer to them; that was a
        # second place to keep in step for no benefit, and it is gone.
        if not _ENTRY_RE.match(graph):
            raise ManifestError(
                f"{where}: {label} graph {graph!r} is not a `module:function` entry point"
            )

    session = str(block.get("session") or _default_session(kind))
    if session not in SESSION_MODES:
        raise ManifestError(
            f"{where}: {label} has session {session!r}; expected one of "
            f"{', '.join(sorted(SESSION_MODES))}"
        )

    max_inflight = block.get("max_inflight")
    if max_inflight is not None and (not isinstance(max_inflight, int) or max_inflight < 1):
        raise ManifestError(
            f"{where}: {label} has max_inflight={max_inflight!r}; expected a positive integer"
        )
    if kind in STREAM_KINDS and max_inflight is None:
        # Deliberately not defaulted. operonx guards concurrency and, since
        # transient ports, retention — it does not guard volume, and the
        # one place that needs it most is a socket nobody can slow down.
        # A number nobody chose is how an unbounded queue gets shipped.
        #
        raise ManifestError(
            f"{where}: {label} is kind {kind!r} and must set `max_inflight` — "
            f"a stream transport needs a bound to push back against"
        )

    port = block.get("port", 8000)
    try:
        port = int(port)
    except (TypeError, ValueError):
        raise ManifestError(f"{where}: {label} has port {port!r}, which is not a number") from None
    if not 1 <= port <= 65535:
        # Caught here rather than at bind, where it surfaces as an OSError
        # from deep inside the event loop after everything else has already
        # started.
        raise ManifestError(f"{where}: {label} has port {port}, outside the range 1-65535")

    variants = _variants(block.get("variants"), where, label, kind)
    resume = block.get("resume")
    if resume is not None:
        resume = str(resume)
    check_resume(resume, kind, variants, f"{where}: {label}")
    if "inputs" in block:
        raise ManifestError(
            f"{where}: {label} `inputs` is gone — the graph's own runtime parameters are "
            "the door's contract; on_session's RunRequest.inputs is checked against them"
        )
    moved = [k for k in ("ingress", "egress") if k in block]
    if moved:
        raise ManifestError(
            f"{where}: {label} `{'`/`'.join(moved)}` is gone — a door op says what it is "
            'itself: @op(door="ingress") / @op(door="egress")'
        )
    workers = block.get("workers", 1)
    try:
        workers = int(workers)
    except (TypeError, ValueError):
        raise ManifestError(
            f"{where}: {label} has workers {workers!r}, which is not a number"
        ) from None
    if workers < 1:
        raise ManifestError(
            f"{where}: {label} has workers {workers}; a listener needs at least one"
        )
    on_startup = _as_list(block.get("on_startup"))
    for hook in on_startup:
        if not isinstance(hook, str) or not _ENTRY_RE.match(hook):
            raise ManifestError(
                f"{where}: {label} on_startup {hook!r} is not a `module:attr` entry point"
            )

    known_keys = {
        "name",
        "kind",
        "graph",
        "variants",
        "workers",
        "on_startup",
        "path",
        "method",
        "host",
        "port",
        "session",
        "max_inflight",
        "on_session",
        "on_close",
        "app",
        "resume",
        "description",
    }
    options = {k: v for k, v in block.items() if k not in known_keys}
    if "codec" in options:
        check_codec(options["codec"], f"{where}: {label}")
    trace_own = None
    if "trace" in block:
        trace_own = tuple(_as_list(block["trace"]))
        options["trace"] = list(trace_own)

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
        variants=variants,
        workers=workers,
        on_startup=tuple(str(h) for h in on_startup),
        on_close=(str(block["on_close"]) if block.get("on_close") else None),
        app=(str(app) if app else None),
        resume=resume,
        description=str(block.get("description") or ""),
        options=options,
        trace_own=trace_own,
        trace_from="service" if trace_own is not None else "default",
    )


def _queue_specs(blocks: List[Any], where: str) -> Tuple[Any, ...]:
    """``[[queue]]`` blocks as QueueSpecs; names unique."""
    from operonx.app.evals.queues import QueueSpec

    out: List[Any] = []
    for i, block in enumerate(blocks):
        if not isinstance(block, dict):
            raise ManifestError(f"{where}: [[queue]] #{i} is not a table")
        stray = sorted(set(block) - {"name", "rubric", "reviewers", "description"})
        if stray:
            raise ManifestError(f"{where}: [[queue]] #{i} has keys a queue does not read: {stray}")
        try:
            spec = QueueSpec(
                name=str(block.get("name") or ""),
                rubric=dict(block.get("rubric") or {}),
                reviewers=block.get("reviewers", 1),
                description=str(block.get("description") or ""),
            )
        except ValueError as exc:
            raise ManifestError(f"{where}: [[queue]] #{i}: {exc}") from None
        if any(q.name == spec.name for q in out):
            raise ManifestError(f"{where}: two [[queue]] blocks named {spec.name!r}")
        out.append(spec)
    return tuple(out)


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
        route = (
            spec.host,
            spec.port,
            spec.path,
            spec.method if spec.kind in ("http", "webhook") else "*",
        )
        if route in seen_routes:
            raise ManifestError(
                f"{where}: {spec.name!r} and {seen_routes[route]!r} both serve "
                f"{spec.path} on {spec.host}:{spec.port}"
            )
        seen_routes[route] = spec.name
    # Services on one address are one server, so one set of processes.
    workers: Dict[Tuple[str, int], ServeSpec] = {}
    for spec in serves:
        first = workers.setdefault(spec.listener, spec)
        if first.workers != spec.workers:
            raise ManifestError(
                f"{where}: {first.name!r} and {spec.name!r} share {spec.host}:{spec.port} "
                f"but declare workers={first.workers} and workers={spec.workers}"
            )
