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
from typing import Any, Dict, List, Optional, Tuple

from operonx.core.loggings import LOGGER
from operonx.core.manifest import Manifest, ManifestError, ServeSpec

from .asgi import HttpTransport, WebSocketTransport
from .protocol import RunRequest
from .registry import load_object, resolve_transport
from .runner import ServeRunner

__all__ = ["build_app", "build_apps", "engine_for", "serve_manifest"]


def engine_for(spec: ServeSpec) -> Any:
    """Compile the graph a `[[serve]]` entry names.

    Every parameter of the graph becomes a runtime input port. A graph
    compiled without declaring them keeps the literal defaults it was
    built with, which is the failure mode where a deep op holds ``None``
    forever and every call fails on it.
    """
    from operonx.core import Operon

    graph_fn = load_object(spec.graph)
    try:
        params = {
            name: None
            for name in inspect.signature(graph_fn).parameters
        }
    except (TypeError, ValueError):
        params = {}

    # `trace` and `concurrency` ride in the spec's free-form options. They
    # are engine settings rather than transport settings, but they have to
    # be declarable: a manifest that boots the graph and silently drops its
    # trace consumers would take a project's observability away as the
    # price of adopting the serve layer.
    kwargs = {}
    if params:
        kwargs["params"] = params
    trace = spec.options.get("trace")
    if trace:
        kwargs["trace"] = list(trace) if isinstance(trace, (list, tuple)) else [str(trace)]

    engine = Operon(graph_fn, **kwargs)
    concurrency = spec.options.get("concurrency")
    if concurrency:
        engine.graph.concurrency = int(concurrency)
    return engine


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


def build_app(specs: Tuple[ServeSpec, ...], engines: Optional[Dict[str, Any]] = None,
              on_startup: Tuple[str, ...] = ()) -> Any:
    """One ASGI app carrying every endpoint bound to a single listener.

    `on_startup` hooks run once, before any endpoint accepts, because
    warming a model after the first caller has arrived is the same as not
    warming it.
    """
    try:
        from starlette.applications import Starlette
        from starlette.responses import JSONResponse
        from starlette.routing import Mount, Route, WebSocketRoute
    except ImportError as exc:                          # pragma: no cover
        raise ImportError(
            'the built-in http/websocket transports need the serve extra: '
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
            routes.append(Mount(spec.path, app=load_object(spec.app)))
            LOGGER.info(f"[serve:{spec.name}] mounted {spec.app} at {spec.path}")
            continue

        engine = engines.get(spec.name) or engine_for(spec)
        engines[spec.name] = engine

        if spec.kind == "http":
            transport = HttpTransport(spec)
            routes.append(Route(spec.path, _http_endpoint(spec, transport, JSONResponse),
                                methods=[spec.method]))
        elif spec.kind == "websocket":
            transport = WebSocketTransport(spec)
            routes.append(WebSocketRoute(spec.path, _ws_endpoint(spec, transport)))
        else:
            # A project's own transport. It does not get an ASGI route —
            # it accepts its own connections — so the runner drives it and
            # this app simply carries the lifespan.
            transport = resolve_transport(spec.kind)(spec)

        runner = ServeRunner(engine, spec, transport=transport)
        if runner._on_session is None:
            runner._on_session = _default_on_session(spec)
        runners.append(runner)

    # Lifespan rather than `on_event`: Starlette 1.0 removed the latter.
    # The runners start when the server does and are drained on the way
    # out, so a shutdown lets in-flight runs finish rather than killing
    # them — the same reason a disconnect does not cancel a run.
    @asynccontextmanager
    async def lifespan(_app):
        for hook_path in on_startup:
            hook = load_object(hook_path)
            result = hook()
            if inspect.isawaitable(result):
                await result
            LOGGER.info(f"[serve] startup hook done: {hook_path}")
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
        except Exception:                               # noqa: BLE001
            payload = (await request.body()).decode("utf-8", "replace")

        session = await transport.handle(payload, meta=_meta_from_request(request))

        if not session.replies:
            # The plan's requirement, and the reason this branch exists: a
            # run that fails must not answer 200 with an empty body. Op
            # exceptions are caught by the scheduler and logged rather than
            # raised, so "produced nothing" is what a failure looks like
            # from out here — and for one caller waiting on one request,
            # nothing is a failure.
            LOGGER.error(
                f"[serve:{spec.name}] run produced no output; answering 500"
            )
            return JSONResponse(
                {"error": "the graph produced no output", "endpoint": spec.name},
                status_code=500,
            )
        return JSONResponse(session.reply)

    return endpoint


def _ws_endpoint(spec: ServeSpec, transport: WebSocketTransport):
    async def endpoint(websocket):
        await websocket.accept()
        meta = {
            "query": dict(websocket.query_params),
            "headers": dict(websocket.headers),
            "path": str(websocket.url.path),
        }
        await transport.handle(websocket, meta=meta)

    return endpoint


def build_apps(manifest: Manifest) -> Dict[Tuple[str, int], Any]:
    """One app per listener the manifest declares."""
    return {addr: build_app(specs, on_startup=manifest.on_startup)
            for addr, specs in manifest.listeners().items()}


def serve_manifest(manifest: Manifest, only: Optional[List[str]] = None) -> None:
    """Boot every listener the manifest declares, and block.

    More than one listener means more than one uvicorn server in the same
    process, which is why they are gathered rather than run in turn.
    """
    try:
        import uvicorn
    except ImportError as exc:                          # pragma: no cover
        raise ImportError('serving needs the extra: pip install "operonx[serve]"') from exc

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

    async def run_all() -> None:
        servers = []
        for (host, port), group in grouped.items():
            app = build_app(tuple(group), on_startup=manifest.on_startup)
            config = uvicorn.Config(app, host=host, port=port, log_level="info")
            servers.append(uvicorn.Server(config).serve())
            LOGGER.info(
                f"[serve] {host}:{port} -> " + ", ".join(f"{s.name}({s.kind}){s.path}" for s in group)
            )
        await asyncio.gather(*servers)

    asyncio.run(run_all())
