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
import json
from contextlib import asynccontextmanager
from typing import Any, Dict, List, Optional, Sequence, Tuple

from operonx.app.declare import ref_name
from operonx.app.manifest import (
    _ENTRY_RE,
    CODEC_KINDS,
    STREAM_KINDS,
    Manifest,
    ManifestError,
    ServeSpec,
    door_codec,
)
from operonx.core.loggings import LOGGER

from .asgi import DoorDecodeError, HttpTransport, WebSocketTransport, decode_payload
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

    With ``bind`` — a variant of one door — the named parameters of the
    ``@graph`` are fixed at build time and the rest stay runtime inputs.
    Each bound value that reads as ``module:attr`` is loaded; anything
    else is a literal; the decorator passes a static value into the body
    as-is. A plain function that builds and returns a graph is refused:
    graphs are defined at module level (guide 05).
    """
    from operonx.core import Operon

    graph_fn = resolve_ref(entry, field=f"{where} graph")
    entry = ref_name(entry)
    bound: Dict[str, Any] = {}
    if bind:
        if not getattr(graph_fn, "_operonx_graph", False):
            raise TypeError(
                f"{where} graph {entry!r} is a plain function; variants bind a @graph's own "
                f"parameters {sorted(bind)}. Define the @graph at module level with them as "
                "parameters, not built inside a function (operonx guide 05)."
            )
        bound = _resolve_bind(bind, where)
    # `Operon(...)` on something that is not a graph fails as
    # `AttributeError: 'str' object has no attribute 'name'`, which names
    # neither the manifest entry nor what was actually wrong.
    if not hasattr(graph_fn, "name") and not callable(graph_fn):
        raise TypeError(
            f"{where} graph {entry!r} resolved to a "
            f"{type(graph_fn).__name__}, which is not a @graph"
        )
    try:
        signature = inspect.signature(graph_fn).parameters
    except (TypeError, ValueError):
        signature = {}
    params = {name: None for name in signature}
    defaults = {
        name: p.default for name, p in signature.items() if p.default is not inspect.Parameter.empty
    }
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

    if getattr(graph_fn, "_operonx_graph", False):
        # Named after the graph, explicitly: built inside `Operon(...)` it
        # took the name of the local below, and every service run was
        # called `engine`.
        graph_fn = graph_fn(name=graph_fn.__name__, **params)
        kwargs.pop("params", None)
    engine = Operon(graph_fn, **kwargs)
    if concurrency:
        engine.graph.concurrency = int(concurrency)
    # What a door's RunRequest must carry: the graph's signature is the
    # contract, checked at the gate (`ServeRunner._inputs_fit`).
    engine.inputs_expected = runtime
    # What a doorless door fills a parameter with when the caller leaves it
    # out: the @graph's own default (`operonx.app.doors.serve_inputs`).
    engine.inputs_defaults = {k: v for k, v in defaults.items() if k in runtime}
    return engine


def _warn_doorless_stream(spec: ServeSpec, engines: Dict[str, Any]) -> None:
    """A stream door's graph should read its items through an ingress door:
    a stream has many items over the run's lifetime, and a graph's
    parameters take one set. Such a graph runs as it always has — once per
    connection, reading nothing — with a warning; refused from 2.0."""
    from operonx.app.doors import has_doors

    for key, engine in engines.items():
        if not has_doors(engine):
            LOGGER.warning(
                f"[serve:{key}] a {spec.kind} listener whose graph {ref_name(spec.graph)} has "
                "no ingress op: it runs once per connection and reads none of its items. "
                "Read them with `ingress()` and answer with `egress()`; a graph without "
                "doors belongs on http, webhook or schedule. Refused from operonx 2.0."
            )


def _resolve_bind(bind: Dict[str, Any], where: str) -> Dict[str, Any]:
    """A variant's values: ``module:attr`` loaded, the rest literal."""
    return {
        name: load_object(value, field=f"{where} bind.{name}")
        if isinstance(value, str) and _ENTRY_RE.match(value)
        else value
        for name, value in bind.items()
    }


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
        # a webhook mints the run id it answered with (`meta["trace_id"]`)
        return RunRequest(
            inputs=query, scratch={}, trace_id=query.get("trace_id") or meta.get("trace_id")
        )

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

    from operonx.app.tracing import check_sinks

    # A door's resume route is a door of its own (`ServeSpec.resume_spec`):
    # its own engine, runner and runs, filed under `<name>.resume`.
    specs = tuple(x for spec in specs for x in (spec, spec.resume_spec()) if x is not None)
    # before any engine is compiled: a missing sink named with the level
    # that chose it, not a KeyError from inside the first `Operon(...)`
    check_sinks("serve", specs)
    engines = dict(engines or {})
    routes: List[Any] = []
    runners: List[Any] = []  # a ServeRunner per door, a JobClock per scheduled job

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

        if spec.options.get("job") is not None:
            # a scheduled job: its ticks run the job, not a graph
            from .triggers import JobClock

            runners.append(JobClock(spec))
            continue

        if spec.kind in CODEC_KINDS:
            door_codec(spec)  # an unknown codec fails the build, not the first request
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
        elif spec.kind == "webhook":
            from .triggers import WebhookTransport

            transport = WebhookTransport(spec)
            routes.append(
                Route(spec.path, _webhook_endpoint(spec, transport, JSONResponse), methods=["POST"])
            )
        else:
            # A project's own transport. It does not get an ASGI route —
            # it accepts its own connections — so the runner drives it and
            # this app simply carries the lifespan.
            transport = resolve_transport(spec.kind)(spec)

        if spec.kind in STREAM_KINDS:
            _warn_doorless_stream(spec, built)
        runner = ServeRunner(engine, spec, transport=transport, variants=variants)
        if runner._on_session is None:
            runner._on_session = _default_on_session(spec)
        transport.precheck = runner.precheck
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

    # A health route for a load balancer, unless the listener's own code
    # answers it: a service on that path, or a mounted app (its own routes).
    if not any(spec.path == HEALTH_PATH or spec.kind == "asgi" for spec in specs):
        names = [spec.name for spec in specs]

        async def healthz(_request):
            return JSONResponse({"ok": True, "services": names})

        routes.insert(0, Route(HEALTH_PATH, healthz, methods=["GET"]))

    app = Starlette(routes=routes, lifespan=lifespan)
    app.state.operonx_runners = runners
    app.state.operonx_engines = engines
    return app


#: The route every listener answers ``GET`` on with ``{"ok": true, ...}``.
HEALTH_PATH = "/healthz"

#: The response header naming the run an HTTP reply came from.
TRACE_HEADER = "x-operonx-trace-id"


def _trace_headers(trace_id: Optional[str]) -> Dict[str, str]:
    return {TRACE_HEADER: trace_id} if trace_id else {}


async def _read_body(request: Any, spec: ServeSpec, JSONResponse) -> Tuple[Any, Any]:
    """``(payload, None)``, or ``(None, a 400 response)`` when the door's
    codec cannot read the body — answered before any run is minted."""
    try:
        return decode_payload(await request.body(), door_codec(spec)), None
    except DoorDecodeError as exc:
        LOGGER.info(f"[serve:{spec.name}] refused a request: {exc}")
        return None, JSONResponse({"error": str(exc), "endpoint": spec.name}, status_code=400)


#: The media type a caller accepts to read an http door's run as it happens.
EVENT_STREAM = "text/event-stream"


def wants_stream(request: Any) -> bool:
    """The caller accepts ``text/event-stream``: it reads the run's items as
    server-sent events, one per item, as they are sent."""
    return EVENT_STREAM in request.headers.get("accept", "")


def sse_frame(item: Any, seq: Optional[int] = None) -> str:
    """One item as one server-sent event: its JSON on a ``data:`` line —
    the same encoding a JSON reply gives it — and its number on an ``id:``
    line, which a reader that dropped sends back (``after_seq`` or
    ``Last-Event-ID``) to read on from there."""
    head = f"id: {seq}\n" if seq is not None else ""
    return f"{head}data: {json.dumps(item, ensure_ascii=False, default=str)}\n\n"


def _no_output(spec: ServeSpec, trace_id: Optional[str], JSONResponse):
    # The plan's requirement, and the reason this branch exists: a run
    # that fails must not answer 200 with an empty body. An op exception
    # is recorded on the run (`handle.errors`) and logged rather than
    # raised, so "produced nothing" is what a failure looks like from out
    # here — and for one caller waiting on one request, nothing is a
    # failure. The error text stays in the log and the trace: it is a
    # traceback, and it goes nowhere near a client. The run's id does: it
    # is how the client's report finds the trace (also in
    # `x-operonx-trace-id`, for a client that keeps only the body).
    LOGGER.error(f"[serve:{spec.name}] run produced no output; answering 500")
    body = {"error": "the graph produced no output", "endpoint": spec.name}
    if trace_id:
        body["trace_id"] = trace_id
    return JSONResponse(body, status_code=500, headers=_trace_headers(trace_id))


async def _stream_reply(
    spec: ServeSpec, transport: HttpTransport, payload: Any, meta, JSONResponse
):
    """The run's items as server-sent events, each written as it is sent.

    The response starts with the run's first item, so a run that sends
    nothing is still the 500 a JSON caller gets — never a stream that
    opens and ends empty. A caller that leaves mid-stream does not stop
    the run (a disconnect never does, `operonx.app.serve.protocol`); what
    the run sends after that is dropped and reported as not delivered.
    """
    from starlette.responses import StreamingResponse

    session = transport.open_stream(payload, meta=meta)
    frames = session.follow(0)
    try:
        first = await frames.__anext__()
    except StopAsyncIteration:
        if session.refusal is not None:
            return JSONResponse(session.refusal[1], status_code=session.refusal[0])
        return _no_output(spec, session.trace_id, JSONResponse)

    async def body():
        try:
            yield sse_frame(first[1], first[0])
            async for seq, item in frames:
                yield sse_frame(item, seq)
        finally:
            await frames.aclose()

    headers = {**_trace_headers(session.trace_id), "cache-control": "no-cache"}
    return StreamingResponse(body(), media_type=EVENT_STREAM, headers=headers)


def _reconnect(spec: ServeSpec, transport: HttpTransport, request: Any, JSONResponse):
    """A stream read again: ``?run_id=R`` with ``after_seq=N`` (or a
    ``Last-Event-ID: N`` header) gives the run's items after the N-th —
    those sent while the reader was away, then the rest as they come."""
    from starlette.responses import StreamingResponse

    run_id = request.query_params["run_id"]
    raw = request.query_params.get("after_seq") or request.headers.get("last-event-id") or "0"
    try:
        after = int(raw)
    except ValueError:
        return JSONResponse({"error": f"after_seq={raw!r} is not a number"}, status_code=400)
    session = transport.stream_of(run_id)
    if session is None:
        return JSONResponse(
            {
                "error": f"no stream of run {run_id!r} here: it ended more than "
                "15 minutes ago, never streamed, or ran on another replica",
                "endpoint": spec.name,
            },
            status_code=404,
        )

    async def body():
        frames = session.follow(after)
        try:
            async for seq, item in frames:
                yield sse_frame(item, seq)
        finally:
            await frames.aclose()

    headers = {**_trace_headers(run_id), "cache-control": "no-cache"}
    return StreamingResponse(body(), media_type=EVENT_STREAM, headers=headers)


def _http_endpoint(spec: ServeSpec, transport: HttpTransport, JSONResponse):
    async def endpoint(request):
        if wants_stream(request) and "run_id" in request.query_params:
            return _reconnect(spec, transport, request, JSONResponse)
        payload, refusal = await _read_body(request, spec, JSONResponse)
        if refusal is not None:
            return refusal
        meta = _meta_from_request(request)
        if wants_stream(request):
            return await _stream_reply(spec, transport, payload, meta, JSONResponse)

        session = await transport.handle(payload, meta=meta)
        headers = _trace_headers(session.trace_id)

        if session.refusal is not None:
            return JSONResponse(session.refusal[1], status_code=session.refusal[0])
        if not session.replies:
            return _no_output(spec, session.trace_id, JSONResponse)
        return JSONResponse(session.reply, headers=headers)

    return endpoint


def _webhook_endpoint(spec: ServeSpec, transport: Any, JSONResponse):
    async def endpoint(request):
        payload, refusal = await _read_body(request, spec, JSONResponse)
        if refusal is not None:
            return refusal
        from .triggers import Refused

        meta = _meta_from_request(request)
        # a doorless graph refuses a body that does not fit before the 202
        precheck = getattr(transport, "precheck", None)
        problem = precheck(payload, meta) if precheck is not None else None
        if problem is not None:
            LOGGER.info(f"[serve:{spec.name}] refused a request: {problem}")
            return JSONResponse(
                {"error": str(problem), "endpoint": spec.name, "field": problem.field},
                status_code=400,
            )
        try:
            run_id = await transport.submit(payload, meta=meta)
        except Refused as no:
            return JSONResponse(no.body, status_code=no.status)
        if run_id is None:
            # Stopping, or full: the sender retries. Queueing without bound
            # behind a slow flow is how a burst becomes an outage.
            return JSONResponse({"accepted": False, "service": spec.name}, status_code=429)
        return JSONResponse(
            {"accepted": True, "service": spec.name, "run_id": run_id},
            status_code=202,
            headers=_trace_headers(run_id),
        )

    return endpoint


def _ws_endpoint(spec: ServeSpec, transport: WebSocketTransport):
    from .asgi import WebSocketSession

    async def endpoint(websocket):
        meta = {
            "query": dict(websocket.query_params),
            "headers": dict(websocket.headers),
            "path": str(websocket.url.path),
        }
        session = WebSocketSession(
            websocket, meta=meta, max_inflight=spec.max_inflight, codec=door_codec(spec)
        )

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


def serve_manifest(
    manifest: Manifest,
    only: Optional[List[str]] = None,
    host: Optional[str] = None,
    port: Optional[int] = None,
) -> None:
    """Boot every listener the manifest declares, and block.

    A listener with one worker runs in this process — several of them as
    several uvicorn servers gathered on one loop. A listener with
    ``workers=N`` runs in a child process as uvicorn with N workers; each
    worker loads the application again from ``operonx.toml``
    (`worker_app`), so it compiles its own engines and runs its own
    service's startup hooks. When this process ends, the children do.

    ``host`` / ``port`` bind somewhere else than the manifest says (the
    CLI's ``--host`` / ``--port``); ``port`` only when one listener runs.
    """
    try:
        import uvicorn
    except ImportError as exc:  # pragma: no cover
        raise ImportError('serving needs the extra: pip install "operonx[serve]"') from exc

    from operonx.app.tracing import check_sinks

    listeners = plan(manifest, only)
    if port is not None and len(listeners) > 1:
        raise ManifestError(
            f"--port binds one listener; this would serve {len(listeners)} "
            f"({', '.join(f'{h}:{p}' for (h, p), _, _ in listeners)}) — choose with --only"
        )

    def bind(h: str, p: int) -> Tuple[str, int]:
        return host or h, port or p

    # here too, before a pooled listener's workers fail one by one in
    # processes of their own
    check_sinks(manifest.name, [s for _, group, _ in listeners for s in group])
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
    for (lhost, lport), group, workers in pooled:
        bhost, bport = bind(lhost, lport)
        proc = multiprocessing.Process(
            target=_run_pooled,
            args=(str(root), lhost, lport, [s.name for s in group], workers, bhost, bport),
            name=f"operonx-serve-{bport}",
        )
        proc.start()
        children.append(proc)
        LOGGER.info(
            f"[serve] {bhost}:{bport} x{workers} workers -> "
            + ", ".join(f"{s.name}({s.kind}){s.path}" for s in group)
        )

    async def run_here() -> None:
        servers = []
        for (lhost, lport), group, _ in here:
            bhost, bport = bind(lhost, lport)
            app = build_app(group, on_startup=manifest.on_startup)
            config = uvicorn.Config(app, host=bhost, port=bport, log_level="info")
            servers.append(uvicorn.Server(config).serve())
            LOGGER.info(
                f"[serve] {bhost}:{bport} -> "
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


def _run_pooled(
    root: str,
    host: str,
    port: int,
    names: List[str],
    workers: int,
    bind_host: Optional[str] = None,
    bind_port: Optional[int] = None,
) -> None:
    """A child process: uvicorn, `workers` processes, each loading the app.
    *host*:*port* name the listener; *bind_host*/*bind_port* where it binds."""
    import os

    import uvicorn

    os.environ[_ROOT_ENV] = root
    os.environ[_LISTENER_ENV] = f"{host}:{port}"
    os.environ[_ONLY_ENV] = ",".join(names)
    uvicorn.run(
        "operonx.app.serve.app:worker_app",
        factory=True,
        host=bind_host or host,
        port=bind_port or port,
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
