"""A manifest, turned into running servers.

This is the hop `operonx.toml` described and nothing performed: uvicorn
calls an ASGI route, the route mints a run. Written once here so that no
project writes it again — the callbot's copy is 437 lines.

Endpoints group onto listeners by ``(host, port)``, so two `[[serve]]`
blocks naming the same port share one server without the manifest needing
a second concept for it.
"""

from __future__ import annotations

import asyncio
import inspect
from contextlib import asynccontextmanager
from typing import Any, Dict, List, Optional, Sequence, Tuple

from operonx.app.declare import ref_name
from operonx.app.manifest import _ENTRY_RE, Manifest, ManifestError, ServeSpec
from operonx.core.loggings import LOGGER

from .asgi import HttpTransport, WebSocketTransport
from .protocol import RunRequest
from .registry import load_object, resolve_ref, resolve_transport
from .runner import ServeRunner

__all__ = [
    "build_app",
    "build_apps",
    "compile_graph",
    "engine_for",
    "engines_for",
    "serve_manifest",
]


def compile_graph(
    entry: Any,
    *,
    bind: Optional[Dict[str, Any]] = None,
    trace: Any = None,
    concurrency: Optional[int] = None,
    where: str = "graph",
) -> Any:
    """An ``Operon`` from a ``module:attr`` entry point, the way anything
    declared in the manifest is compiled.

    Every parameter of the graph factory becomes a runtime input port. A
    graph compiled without declaring them keeps the literal defaults it
    was built with, which is the failure mode where a deep op holds
    ``None`` forever and every call fails on it.

    With ``bind`` — a variant of one door — the named parameters are
    fixed at build time and the rest stay runtime inputs. Each bound
    value that reads as ``module:attr`` is loaded; anything else is a
    literal. A ``@graph`` takes them as its own parameters (the
    decorator passes a static value into the body as-is); a plain
    function is a factory that takes them and returns a ``@graph``.
    """
    from operonx.core import Operon

    graph_fn = resolve_ref(entry, field=f"{where} graph")
    entry = ref_name(entry)
    bound: Dict[str, Any] = {}
    if bind:
        if getattr(graph_fn, "_operonx_graph", False):
            bound = _resolve_bind(bind, where)
        else:
            graph_fn = _bind_factory(graph_fn, entry, bind, where)
    # `Operon(...)` on something that is not a graph fails as
    # `AttributeError: 'str' object has no attribute 'name'`, which names
    # neither the manifest entry nor what was actually wrong.
    if not hasattr(graph_fn, "name") and not callable(graph_fn):
        raise TypeError(
            f"{where} graph {entry!r} resolved to a "
            f"{type(graph_fn).__name__}, which is not a @graph"
        )
    try:
        params = {name: None for name in inspect.signature(graph_fn).parameters}
    except (TypeError, ValueError):
        params = {}
    unknown = set(bound) - set(params)
    if unknown:
        raise TypeError(
            f"{where} graph {entry!r} has no parameter {sorted(unknown)}; "
            f"it takes {sorted(params) or 'none'}"
        )
    params.update(bound)
    runtime = tuple(name for name in params if name not in bound)

    kwargs = {}
    if params:
        kwargs["params"] = params
    if trace:
        kwargs["trace"] = list(trace) if isinstance(trace, (list, tuple)) else [str(trace)]

    engine = Operon(graph_fn, **kwargs)
    if concurrency:
        engine.graph.concurrency = int(concurrency)
    # What a door's RunRequest must carry: the graph's signature is the
    # contract, checked at the gate (`ServeRunner._inputs_fit`).
    engine.inputs_expected = runtime
    return engine


def _resolve_bind(bind: Dict[str, Any], where: str) -> Dict[str, Any]:
    """A variant's values: ``module:attr`` loaded, the rest literal."""
    return {
        name: load_object(value, field=f"{where} bind.{name}")
        if isinstance(value, str) and _ENTRY_RE.match(value)
        else value
        for name, value in bind.items()
    }


def _bind_factory(factory: Any, entry: str, bind: Dict[str, Any], where: str) -> Any:
    """Call a plain-function factory with a variant's bound parameters."""
    resolved = _resolve_bind(bind, where)
    try:
        result = factory(**resolved)
    except TypeError as exc:
        raise TypeError(f"{where} graph {entry!r} could not take {sorted(bind)}: {exc}") from exc
    if not hasattr(result, "name") and not callable(result):
        raise TypeError(f"{where} graph {entry!r} returned a {type(result).__name__}, not a @graph")
    return result


def engine_for(spec: ServeSpec, variant: Optional[str] = None) -> Any:
    """Compile the graph a `[[serve]]` entry names — one of its variants,
    when it declares them.

    `trace` and `concurrency` ride in the spec's free-form options. They
    are engine settings rather than transport settings, but they have to
    be declarable: a manifest that boots the graph and silently drops its
    trace consumers would take a project's observability away as the
    price of adopting the serve layer.
    """
    where = f"[[serve]] {spec.name!r}"
    bind = None
    if variant is not None:
        bind = spec.variants[variant]
        where = f"{where} variant {variant!r}"
    return compile_graph(
        spec.graph,
        bind=bind,
        trace=spec.options.get("trace"),
        concurrency=spec.options.get("concurrency"),
        where=where,
    )


def engines_for(spec: ServeSpec, have: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Every engine a door needs, keyed the way `build_app` keeps them:
    the spec's name, or ``name/variant`` for each declared variant.
    Engines already in ``have`` are reused."""
    have = have or {}
    if not spec.variants:
        key = spec.name
        return {key: have.get(key) or engine_for(spec)}
    out = {}
    for variant in spec.variants:
        key = f"{spec.name}/{variant}"
        out[key] = have.get(key) or engine_for(spec, variant)
    return out


def _meta_from_request(request: Any) -> Dict[str, Any]:
    return {
        "query": dict(request.query_params),
        "headers": dict(request.headers),
        "path": str(request.url.path),
        "client": getattr(request.client, "host", None),
    }


def _default_on_session(spec: ServeSpec):
    """No hook declared: the connection's query string becomes the inputs.

    Enough for a graph whose parameters are scalars, and honest about its
    limits — anything that has to validate, reject, or look a customer up
    declares `on_session` and does it in project code.
    """

    def build(session: Any) -> RunRequest:
        meta = dict(getattr(session, "meta", {}) or {})
        query = dict(meta.get("query") or {})
        return RunRequest(inputs=query, scratch={}, trace_id=query.get("trace_id"))

    return build


def build_app(
    specs: Tuple[ServeSpec, ...],
    engines: Optional[Dict[str, Any]] = None,
    on_startup: Tuple[Any, ...] = (),
    startup: bool = True,
) -> Any:
    """One ASGI app carrying every endpoint bound to a single listener.

    `on_startup` hooks run once, before any endpoint accepts, because
    warming a model after the first caller has arrived is the same as not
    warming it.
    """
    try:
        from starlette.applications import Starlette
        from starlette.responses import JSONResponse
        from starlette.routing import Mount, Route, WebSocketRoute
    except ImportError as exc:  # pragma: no cover
        raise ImportError(
            "the built-in http/websocket transports need the serve extra: "
            'pip install "operonx[serve]"'
        ) from exc

    engines = dict(engines or {})
    routes: List[Any] = []
    runners: List[ServeRunner] = []

    for spec in specs:
        if spec.kind == "asgi":
            # Health, CRUD, admin — not a graph, and operonx must never
            # pretend it is one. The manifest still describes it, so the
            # whole product is in one file.
            routes.append(
                Mount(spec.path, app=resolve_ref(spec.app, field=f"[[serve]] {spec.name!r} app"))
            )
            LOGGER.info(f"[serve:{spec.name}] mounted {ref_name(spec.app)} at {spec.path}")
            continue

        built = engines_for(spec, engines)
        engines.update(built)
        if spec.variants:
            engine = None
            variants = {k.partition("/")[2]: e for k, e in built.items()}
        else:
            engine = built[spec.name]
            variants = None

        if spec.kind == "http":
            transport = HttpTransport(spec)
            routes.append(
                Route(
                    spec.path, _http_endpoint(spec, transport, JSONResponse), methods=[spec.method]
                )
            )
        elif spec.kind == "websocket":
            transport = WebSocketTransport(spec)
            routes.append(WebSocketRoute(spec.path, _ws_endpoint(spec, transport)))
        else:
            # A project's own transport. It does not get an ASGI route —
            # it accepts its own connections — so the runner drives it and
            # this app simply carries the lifespan.
            transport = resolve_transport(spec.kind)(spec)

        runner = ServeRunner(engine, spec, transport=transport, variants=variants)
        if runner._on_session is None:
            runner._on_session = _default_on_session(spec)
        # The websocket route needs to ask the same question the runner
        # would, one step earlier — before the handshake is answered.
        transport.gate = runner._request_for
        runners.append(runner)

    # The application's hooks, then each service's own — once each, in
    # declaration order. A service's hooks run only where it is served.
    hooks: List[Any] = []
    declared = (*on_startup, *(h for spec in specs for h in spec.on_startup)) if startup else ()
    for hook in declared:
        if not any(hook is h or hook == h for h in hooks):
            hooks.append(hook)

    # Lifespan rather than `on_event`: Starlette 1.0 removed the latter.
    # The runners start when the server does and are drained on the way
    # out, so a shutdown lets in-flight runs finish rather than killing
    # them — the same reason a disconnect does not cancel a run.
    @asynccontextmanager
    async def lifespan(_app):
        for hook_ref in hooks:
            hook = resolve_ref(hook_ref, field="on_startup")
            result = hook()
            if inspect.isawaitable(result):
                await result
            LOGGER.info(f"[serve] startup hook done: {ref_name(hook_ref)}")
        tasks = [asyncio.ensure_future(r.run()) for r in runners]
        try:
            yield
        finally:
            for r in runners:
                await r.close()
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)

    app = Starlette(routes=routes, lifespan=lifespan)
    app.state.operonx_runners = runners
    app.state.operonx_engines = engines
    return app


def _http_endpoint(spec: ServeSpec, transport: HttpTransport, JSONResponse):
    async def endpoint(request):
        try:
            payload = await request.json()
        except Exception:  # noqa: BLE001
            payload = (await request.body()).decode("utf-8", "replace")

        session = await transport.handle(payload, meta=_meta_from_request(request))

        if not session.replies:
            # The plan's requirement, and the reason this branch exists: a
            # run that fails must not answer 200 with an empty body. Op
            # exceptions are caught by the scheduler and logged rather than
            # raised, so "produced nothing" is what a failure looks like
            # from out here — and for one caller waiting on one request,
            # nothing is a failure.
            LOGGER.error(f"[serve:{spec.name}] run produced no output; answering 500")
            return JSONResponse(
                {"error": "the graph produced no output", "endpoint": spec.name},
                status_code=500,
            )
        return JSONResponse(session.reply)

    return endpoint


def _ws_endpoint(spec: ServeSpec, transport: WebSocketTransport):
    from .asgi import WebSocketSession

    async def endpoint(websocket):
        meta = {
            "query": dict(websocket.query_params),
            "headers": dict(websocket.headers),
            "path": str(websocket.url.path),
        }
        session = WebSocketSession(websocket, meta=meta, max_inflight=spec.max_inflight)

        # `on_session` decides before the handshake completes. Accepting and
        # then closing is not the same thing as refusing: the peer sees a
        # successful upgrade followed by silence, where a refusal is a 403
        # it can act on. Closing an un-accepted socket is how Starlette
        # sends that.
        gate = getattr(transport, "gate", None)
        if gate is not None:
            request = gate(session)
            if request is None:
                LOGGER.info(f"[serve:{spec.name}] refused a connection at the door")
                await websocket.close(code=4403)
                return
            session.run_request = request

        await websocket.accept()
        transport.offer(session)
        await session.pump_inbound()

    return endpoint


def build_apps(manifest: Manifest) -> Dict[Tuple[str, int], Any]:
    """One app per listener the manifest declares."""
    return {
        addr: build_app(specs, on_startup=manifest.on_startup)
        for addr, specs in manifest.listeners().items()
    }


#: What a worker process reads to load its listener (see `worker_app`).
_ROOT_ENV = "OPERONX_SERVE_ROOT"
_LISTENER_ENV = "OPERONX_SERVE_LISTENER"
_ONLY_ENV = "OPERONX_SERVE_ONLY"


def plan(
    manifest: Manifest, only: Optional[List[str]] = None
) -> List[Tuple[Tuple[str, int], Tuple[ServeSpec, ...], int]]:
    """What `serve_manifest` will start: each listener, its services and
    its worker count, in declaration order."""
    specs = manifest.serves
    if only:
        wanted = set(only)
        specs = tuple(s for s in specs if s.name in wanted)
        missing = wanted - {s.name for s in specs}
        if missing:
            raise ManifestError(f"no serve entry named: {', '.join(sorted(missing))}")
    if not specs:
        raise ManifestError("nothing to serve — the manifest declares no [[serve]] entries")
    grouped: Dict[Tuple[str, int], List[ServeSpec]] = {}
    for spec in specs:
        grouped.setdefault(spec.listener, []).append(spec)
    return [(addr, tuple(group), group[0].workers) for addr, group in grouped.items()]


def serve_manifest(manifest: Manifest, only: Optional[List[str]] = None) -> None:
    """Boot every listener the manifest declares, and block.

    A listener with one worker runs in this process — several of them as
    several uvicorn servers gathered on one loop. A listener with
    ``workers=N`` runs in a child process as uvicorn with N workers; each
    worker loads the application again from ``operonx.toml``
    (`worker_app`), so it compiles its own engines and runs its own
    service's startup hooks. When this process ends, the children do.
    """
    try:
        import uvicorn
    except ImportError as exc:  # pragma: no cover
        raise ImportError('serving needs the extra: pip install "operonx[serve]"') from exc

    listeners = plan(manifest, only)
    pooled = [entry for entry in listeners if entry[2] > 1]
    here = [entry for entry in listeners if entry[2] == 1]
    root = manifest.root
    if pooled and not (root / "operonx.toml").is_file():
        names = ", ".join(s.name for _, group, _ in pooled for s in group)
        raise ManifestError(
            f"{names}: workers > 1 needs an operonx.toml at {root} — each worker "
            "process loads the application from it (`[project] app` for one declared in Python)"
        )

    import multiprocessing
    import signal
    import threading

    if pooled and threading.current_thread() is threading.main_thread():
        # SIGTERM (a container stop, a supervisor) ends Python without
        # running `finally`, which would leave the worker processes
        # serving. As an exception, it unwinds through the cleanup below.
        def _stop(signum, frame):  # noqa: ARG001
            raise SystemExit(0)

        signal.signal(signal.SIGTERM, _stop)

    children = []
    for (host, port), group, workers in pooled:
        proc = multiprocessing.Process(
            target=_run_pooled,
            args=(str(root), host, port, [s.name for s in group], workers),
            name=f"operonx-serve-{port}",
        )
        proc.start()
        children.append(proc)
        LOGGER.info(
            f"[serve] {host}:{port} x{workers} workers -> "
            + ", ".join(f"{s.name}({s.kind}){s.path}" for s in group)
        )

    async def run_here() -> None:
        servers = []
        for (host, port), group, _ in here:
            app = build_app(group, on_startup=manifest.on_startup)
            config = uvicorn.Config(app, host=host, port=port, log_level="info")
            servers.append(uvicorn.Server(config).serve())
            LOGGER.info(
                f"[serve] {host}:{port} -> "
                + ", ".join(f"{s.name}({s.kind}){s.path}" for s in group)
            )
        await asyncio.gather(*servers)

    try:
        if here:
            asyncio.run(run_here())
        else:
            for proc in children:
                proc.join()
    finally:
        for proc in children:
            if proc.is_alive():
                proc.terminate()
                proc.join(timeout=5)


def _run_pooled(root: str, host: str, port: int, names: List[str], workers: int) -> None:
    """A child process: uvicorn, `workers` processes, each loading the app."""
    import os

    import uvicorn

    os.environ[_ROOT_ENV] = root
    os.environ[_LISTENER_ENV] = f"{host}:{port}"
    os.environ[_ONLY_ENV] = ",".join(names)
    uvicorn.run(
        "operonx.app.serve.app:worker_app",
        factory=True,
        host=host,
        port=port,
        workers=workers,
        log_level="info",
    )


def worker_app() -> Any:
    """The ASGI app one worker serves — what uvicorn's factory calls in
    each worker process `serve_manifest` starts for a pooled listener.
    It loads the application from the project root it was handed, builds
    its listener, and runs the application's and those services' startup
    hooks there."""
    import os

    from ..application import Application

    root = os.environ.get(_ROOT_ENV)
    if not root:
        raise RuntimeError(
            f"worker_app() runs in a worker `serve_manifest` started ({_ROOT_ENV} unset)"
        )
    host, _, port = os.environ[_LISTENER_ENV].rpartition(":")
    only = [n for n in os.environ.get(_ONLY_ENV, "").split(",") if n]
    app = Application.find(root)
    return app.asgi(port=int(port), only=only or None)
