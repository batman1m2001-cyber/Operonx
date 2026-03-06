"""HushApp — serve Hush workflow graphs as HTTP APIs."""

import inspect
from typing import Callable, List, Optional, Union

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from hush.core import PARENT, GraphOp
from hush.core.tracing import Tracer
from hush.serve.config import AppConfig, EndpointConfig
from hush.serve.endpoint import Endpoint
from hush.serve.jobs import JobStore
from hush.serve.middleware import RequestIDMiddleware, TimingMiddleware
from hush.serve.routes.batch_handler import create_batch_handler
from hush.serve.routes.job_handler import create_job_status_handler, create_submit_handler
from hush.serve.routes.stream_handler import create_stream_handler
from hush.serve.routes.sync_handler import create_sync_handler
from hush.serve.routes.ws_handler import create_ws_handler
from hush.serve.schema import graph_schema_info


class HushApp:
    """Application that serves Hush workflow graphs as HTTP API endpoints.

    Example::

        from hush.core import GraphOp, op, START, END, PARENT
        from hush.serve import HushApp

        @op
        def double(x: int):
            return {"result": x * 2}

        with GraphOp(name="doubler") as graph:
            step = double(x=PARENT["x"])
            START >> step >> END

        app = HushApp()
        app.endpoint("/double", graph=graph)
        app.serve()
    """

    def __init__(
        self,
        *,
        title: str = "Hush API",
        description: str = "",
        version: str = "0.1.0",
        tracer: Optional[Union[Tracer, List[Tracer]]] = None,
        cors: bool = True,
        cors_origins: Optional[List[str]] = None,
    ):
        self._config = AppConfig(
            title=title,
            description=description,
            version=version,
            cors=cors,
            cors_origins=cors_origins or ["*"],
        )
        self._tracer = tracer
        self._endpoints: List[Endpoint] = []
        self._job_store = JobStore()
        self._fastapi: Optional[FastAPI] = None

    def endpoint(
        self,
        path: str,
        *,
        graph: Optional[GraphOp] = None,
        stream: Optional[bool] = None,
        batch: bool = True,
        jobs: bool = False,
        websocket: bool = False,
        methods: Optional[List[str]] = None,
        tags: Optional[List[str]] = None,
        summary: Optional[str] = None,
        tracer: Optional[Union[Tracer, List[Tracer]]] = None,
    ) -> Union[None, Callable]:
        """Register a workflow graph as an API endpoint.

        Can be used as a method call or decorator:

            # Method call
            app.endpoint("/score", graph=score_graph)

            # Decorator
            @app.endpoint("/chat")
            @graph
            def chatbot(query): ...
        """
        config = EndpointConfig(
            path=path,
            stream=stream,
            batch=batch,
            jobs=jobs,
            websocket=websocket,
            methods=methods or ["POST"],
            tags=tags or [],
            summary=summary or "",
        )
        ep_tracer = tracer or self._tracer

        if graph is not None:
            ep = Endpoint(graph=graph, config=config, tracer=ep_tracer)
            self._endpoints.append(ep)
            self._fastapi = None  # Invalidate cached FastAPI app
            return None

        # Decorator mode: return a wrapper that captures the graph
        def decorator(graph_or_func):
            target = graph_or_func
            # If it's a callable (@graph factory), call it with PARENT refs
            if callable(target) and not isinstance(target, GraphOp):
                sig = inspect.signature(target)
                parent_kwargs = {
                    name: PARENT[name] for name in sig.parameters if name not in ("self", "cls")
                }
                target = target(**parent_kwargs)
            if not isinstance(target, GraphOp):
                raise TypeError(
                    f"Expected a GraphOp or @graph-decorated function, got {type(target).__name__}."
                )
            ep = Endpoint(graph=target, config=config, tracer=ep_tracer)
            self._endpoints.append(ep)
            self._fastapi = None
            return target

        return decorator

    @property
    def fastapi(self) -> FastAPI:
        """Access the underlying FastAPI app for customization."""
        if self._fastapi is None:
            self._fastapi = self._build_fastapi()
        return self._fastapi

    def _build_fastapi(self) -> FastAPI:
        """Build the FastAPI application from registered endpoints."""
        app = FastAPI(
            title=self._config.title,
            description=self._config.description,
            version=self._config.version,
        )

        # Middleware
        app.add_middleware(TimingMiddleware)
        app.add_middleware(RequestIDMiddleware)

        if self._config.cors:
            app.add_middleware(
                CORSMiddleware,
                allow_origins=self._config.cors_origins,
                allow_methods=["*"],
                allow_headers=["*"],
            )

        # Global routes
        endpoints_info = []
        has_jobs = False

        for ep in self._endpoints:
            path = ep.config.path
            tags = ep.config.tags
            info = graph_schema_info(ep.graph)
            info["path"] = path
            info["stream"] = ep.config.stream
            info["batch"] = ep.config.batch
            info["websocket"] = ep.config.websocket
            info["jobs"] = ep.config.jobs
            endpoints_info.append(info)

            # POST /path — sync handler (always)
            app.add_api_route(
                path,
                create_sync_handler(ep),
                methods=ep.config.methods,
                tags=tags,
                summary=ep.config.summary or f"Execute {ep.graph.name}",
                response_model=ep.response_model,
            )

            # POST /path/stream — SSE (if streaming)
            if ep.config.stream:
                app.add_api_route(
                    f"{path}/stream",
                    create_stream_handler(ep),
                    methods=["POST"],
                    tags=tags,
                    summary=f"Stream {ep.graph.name} via SSE",
                )

            # WS /path/ws — WebSocket (if enabled)
            if ep.config.websocket:
                app.add_websocket_route(f"{path}/ws", create_ws_handler(ep))

            # POST /path/batch — batch (if enabled)
            if ep.config.batch:
                app.add_api_route(
                    f"{path}/batch",
                    create_batch_handler(ep),
                    methods=["POST"],
                    tags=tags,
                    summary=f"Batch execute {ep.graph.name}",
                )

            # POST /path/submit — job submission (if enabled)
            if ep.config.jobs:
                has_jobs = True
                app.add_api_route(
                    f"{path}/submit",
                    create_submit_handler(ep, self._job_store),
                    methods=["POST"],
                    tags=tags,
                    summary=f"Submit {ep.graph.name} as background job",
                )

        # GET /jobs/{job_id} — job status (if any endpoint has jobs)
        if has_jobs:
            app.add_api_route(
                "/jobs/{job_id}",
                create_job_status_handler(self._job_store),
                methods=["GET"],
                tags=["jobs"],
                summary="Get job status",
            )

        # GET /health
        @app.get("/health", tags=["system"])
        def health():
            return {
                "status": "ok",
                "endpoints": [e["path"] for e in endpoints_info],
            }

        # GET /endpoints
        @app.get("/endpoints", tags=["system"])
        def list_endpoints():
            return {"endpoints": endpoints_info}

        return app

    def serve(
        self,
        *,
        host: str = "0.0.0.0",
        port: int = 8000,
        workers: int = 1,
        reload: bool = False,
        log_level: str = "info",
        backend: str = "python",
    ) -> None:
        """Start the API server.

        Args:
            host: Bind address.
            port: Bind port.
            workers: Number of uvicorn workers.
            reload: Enable auto-reload (dev mode).
            log_level: Logging level.
            backend: "python" (FastAPI/uvicorn) or "rust" (Axum, future).
        """
        if backend == "rust":
            from hush.serve._rust_bridge import serve_rust

            serve_rust(self, host, port)
            return

        import uvicorn

        uvicorn.run(
            self.fastapi,
            host=host,
            port=port,
            workers=workers,
            reload=reload,
            log_level=log_level,
        )
