"""Operon - Workflow execution engine.

This module provides the Operon class, an execution engine that runs
GraphOp workflows with state management and observability.

Example:
    ```python
    from operon.core import Operon, GraphOp, START, END, PARENT
    from operon.core.ops import FuncOp

    # Define graph
    with GraphOp(name="my-workflow") as graph:
        node = FuncOp(name="processor", ...)
        START >> node >> END

    # Create engine and run
    engine = Operon(graph)
    result = await engine.run(inputs={"query": "hello"})
    print(result["answer"])  # workflow output
    print(result["$state"])  # access state for debugging/tracing
    ```
"""

import asyncio
import json
import sys
import uuid
from typing import TYPE_CHECKING, Any, Callable, Dict, Iterator, List, Optional, Union

from operon.core.loggings import LOGGER, format_event
from operon.core.middleware import Middleware
from operon.core.ops.graph.graph_op import GraphOp
from operon.core.states import StateSchema

if TYPE_CHECKING:
    from operon.core.ops.base import BaseOp
    from operon.core.registry import ResourceHub
    from operon.core.tracing import Tracer


_MISSING = object()


class ExecutionHandle:
    """Async-iterable handle for a running workflow execution.

    Usage::

        handle = engine.start(inputs={"text": "hello"})

        # Stream every frame as it arrives (one per op yield)
        async for op, ctx, data in handle:
            print(op, data)

        # Wait for a specific output
        answer = await handle["llm", "content"]

        # Collect all outputs grouped by key (lists)
        outputs = await handle.collect()

        # Collect with single-value unwrapping
        outputs = await handle.collect(unwrap=True)

        # Collect as flat list of frame dicts
        frames = await handle.collect("flat")
    """

    def __init__(self, queue: asyncio.Queue, task: asyncio.Task, state: Any = None) -> None:
        self._queue = queue  # fed by root Scheduler
        self._scheduler_task = task  # task running the workflow
        self.state = state  # MemoryState for this execution (tracing access)
        self._frames: list[tuple[str, Any, dict[str, Any]]] = []
        self._idx: int = 0  # index for __anext__, tracks how many frames have been consumed
        self._done: bool = False  # becomes True when the execution is complete
        self._error: BaseException | None = None  # set if the execution raises an error
        self._cond = asyncio.Condition()
        self._waiters: dict[tuple[str, str], list[asyncio.Future[Any]]] = {}
        self._pump_task = asyncio.create_task(self._pump())

    # ---background drain-------------------------------------------------------------
    async def _pump(self) -> None:
        """Drain queue -> _frames, resolve waiters."""
        try:
            while True:
                item = await self._queue.get()
                async with self._cond:
                    if item is None:
                        self._done = True
                        self._resolve_all_waiters(None)
                        self._cond.notify_all()
                        return
                    if isinstance(item, BaseException):
                        self._error = item
                        self._done = True
                        self._resolve_all_waiters(item)
                        self._cond.notify_all()
                        return
                    op, ctx, data = item
                    self._frames.append(item)
                    self._cond.notify_all()
                    self._match_waiters(op, data)
        except Exception as exc:
            async with self._cond:
                self._error = exc
                self._done = True
                self._resolve_all_waiters(exc)
                self._cond.notify_all()

    def _resolve_all_waiters(self, exc: BaseException | None) -> None:
        """Resolve or reject every pending future, then clear."""
        for futs in self._waiters.values():
            for fut in futs:
                if fut.done():
                    continue
                if exc is None:
                    fut.set_result(_MISSING)
                else:
                    fut.set_exception(exc)
        self._waiters.clear()

    def _match_waiters(self, op: str, data: dict[str, Any]) -> None:
        """Check if any waiters are waiting for this op's outputs, and resolve them."""
        for var, val in data.items():
            key = (op, var)
            if key in self._waiters:
                for fut in self._waiters.pop(key, []):
                    if not fut.done():
                        fut.set_result(val)

    # ---async iteration----------------------------------------------------------------
    def __aiter__(self) -> "ExecutionHandle":
        return self

    async def __anext__(self) -> tuple[str, Any, dict[str, Any]]:
        """Yield the next frame (op, ctx, data) as it arrives. Waits if no frames are available yet."""
        async with self._cond:
            while self._idx >= len(self._frames):
                if self._done:
                    if self._error:
                        raise self._error
                    raise StopAsyncIteration
                await self._cond.wait()
            frame = self._frames[self._idx]
            self._idx += 1
            return frame

    # ---point query----------------------------------------------------------------------
    def __getitem__(self, key: tuple[str, str]):
        """Return awaitable for the last value of (op, var)"""
        op, var = key
        return self._await_output(op, var)  # caller does: val = await handle["op", "var"]

    async def _await_output(self, op: str, var: str) -> Any:
        async with self._cond:
            # scan buffered frames (last value wins)
            last: Any = _MISSING
            for f_op, _, data in self._frames:
                if f_op == op and var in data:
                    last = data[var]
            if last is not _MISSING:
                return last
            if self._done:
                if self._error:
                    raise self._error
                return None

            loop = asyncio.get_running_loop()
            fut: asyncio.Future[Any] = loop.create_future()
            self._waiters.setdefault((op, var), []).append(fut)

        val = await fut
        return None if val is _MISSING else val

    # ---convenience-----------------------------------------------------------------------
    @property
    def frame_count(self) -> int:
        """Number of frames received so far."""
        return len(self._frames)

    async def collect(
        self, mode: str = "group", unwrap: bool = False
    ) -> dict[str, Any] | list[dict[str, Any]]:
        """Consume all frames and return collected output.

        Args:
            mode: ``"group"`` merges values by key into lists (default).
                  ``"flat"`` returns an ordered list of frame dicts.
            unwrap: When *True*, single-item lists become scalars.
        """
        if mode == "flat":
            frames: list[dict[str, Any]] = []
            async for _, _, data in self:
                frames.append(data)
            return frames

        # mode == "group"
        out: dict[str, list[Any]] = {}
        async for _, _, data in self:
            for k, v in data.items():
                out.setdefault(k, []).append(v)

        if unwrap:
            return {k: v[0] if len(v) == 1 else v for k, v in out.items()}
        return out

    async def result(self, unwrap: bool = True) -> Dict[str, Any]:
        """Build result from all buffered frames (does not consume).

        Safe to call after ``async for`` iteration — reads from the
        internal buffer rather than re-iterating.
        """
        # Wait for execution to complete if still running
        if not self._done:
            async with self._cond:
                while not self._done:
                    await self._cond.wait()
        if self._error:
            raise self._error
        out: dict[str, list[Any]] = {}
        for _, _, data in self._frames:
            for k, v in data.items():
                out.setdefault(k, []).append(v)
        if unwrap:
            return {k: v[0] if len(v) == 1 else v for k, v in out.items()}
        return out

    def cancel(self) -> None:
        """Cancel the workflow execution."""
        self._scheduler_task.cancel()
        self._pump_task.cancel()


class Operon:
    """Workflow execution engine.

    Operon takes a GraphOp and provides execution capabilities:
    - Builds and validates the graph structure
    - Creates state schema for data flow
    - Executes workflows with fresh state per run
    - Integrates with tracers for observability

    Attributes:
        graph: The GraphOp to execute
        name: Workflow name (from graph)
        schema: State schema for the workflow

    Example:
        ```python
        # Define graph
        with GraphOp(name="chatbot") as graph:
            prompt = PromptOp(name="prompt", ...)
            llm = LLMOp(name="llm", ...)
            START >> prompt >> llm >> END

        # Create engine (builds automatically)
        engine = Operon(graph)

        # Run multiple times with fresh state
        result = await engine.run(inputs={"query": "Hello!"})
        print(result["response"])      # workflow output
        print(result["$state"])        # MemoryState for debugging

        # Or use callable syntax
        result = await engine({"query": "Goodbye!"})
        ```
    """

    __slots__ = ["graph", "name", "_schema", "_collector", "_middleware", "_tracer", "resources"]

    def __init__(
        self,
        graph: Union[GraphOp, Callable[..., GraphOp]],
        *,
        params: Optional[Dict[str, Any]] = None,
        resources: Optional[str] = None,
        tracer: Optional[Union["Tracer", List["Tracer"]]] = None,
    ):
        """Initialize Operon engine with a GraphOp or a graph factory.

        Loads ``./.env`` from CWD (existing env vars are preserved), then
        loads ``resources.yaml`` into the global ResourceHub (required).

        Args:
            graph: A GraphOp workflow, or a callable that returns one.
                   When a callable is passed, env/resources are loaded first,
                   then the callable is invoked with ``**params``.
            params: Keyword arguments passed to the graph factory. Ignored
                    when *graph* is already a GraphOp. Defaults to ``{}``.
            resources: Path to resources.yaml. If None, uses ``./resources.yaml``.
                       File must exist — raises FileNotFoundError otherwise.
            tracer: Default tracer(s) for all run() calls. Can be overridden per-run.
        """
        # 1. Auto-load .env (non-override, preserves existing env)
        self._load_env()

        # 2. Load resources.yaml (required)
        self.resources = self._load_resources(resources)

        # 3. Resolve graph — call factory after env/resources are loaded
        if callable(graph) and not isinstance(graph, GraphOp):
            graph = graph(**(params or {}))

        self.graph = graph
        self.name = graph.name
        self._tracer = tracer

        # Build graph and create schema immediately
        self.graph.build()
        self._schema = StateSchema(self.graph)

        # Precompute trace collector (graph metadata, topo order)
        from operon.core.tracing import TraceCollector

        self._collector = TraceCollector(self.graph)
        self._middleware: List[Middleware] = []

        # Eagerly init backends if a hub is already configured
        self._warmup_ops()

        LOGGER.debug(
            "Operon engine initialized for workflow [highlight]%s[/highlight]",
            self.name,
        )

    def _warmup_ops(self) -> None:
        """Eagerly initialize all provider ops now that the hub is loaded.

        Called once from ``__init__`` after ``graph.build()`` so every op is
        fully wired with its backend instance.
        """
        for op in self._iter_all_ops(self.graph):
            op.warmup()

    @staticmethod
    def _iter_all_ops(graph: GraphOp) -> Iterator["BaseOp"]:
        """Yield every op in *graph* recursively (depth-first)."""
        for op in graph._ops.values():
            yield op
            if isinstance(op, GraphOp):
                yield from Operon._iter_all_ops(op)

    @staticmethod
    def _load_env() -> None:
        """Auto-load ./.env from CWD if it exists. Preserves existing env vars."""
        try:
            from dotenv import load_dotenv
        except ImportError:
            return
        from pathlib import Path

        env_path = Path.cwd() / ".env"
        if env_path.exists():
            load_dotenv(env_path, override=False)

    @staticmethod
    def _load_resources(resources: Optional[str]) -> "ResourceHub":
        """Load ResourceHub from resources.yaml (required).

        - If *resources* is None, uses ``./resources.yaml`` in CWD.
        - File must exist — raises FileNotFoundError otherwise.
        - Missing ``${VAR}`` env interpolations raise at load time.
        """
        from pathlib import Path

        from operon.core.registry import ResourceHub

        path = Path(resources) if resources else Path.cwd() / "resources.yaml"

        if not path.exists():
            raise FileNotFoundError(
                f"resources.yaml not found at: {path.resolve()}\n"
                f"  Create one at your project root, or pass an explicit path:\n"
                f"    Operon(graph, resources='path/to/resources.yaml')"
            )
        hub = ResourceHub.from_yaml(path)
        ResourceHub.set_instance(hub)
        return hub

    @property
    def schema(self) -> StateSchema:
        """Access the workflow state schema."""
        return self._schema

    def use(self, middleware: Middleware) -> "Operon":
        """Add middleware to the engine. Returns self for chaining.

        Args:
            middleware: A Middleware instance to add.

        Returns:
            self, for fluent chaining: ``engine.use(m1).use(m2)``
        """
        self._middleware.append(middleware)
        return self

    def start(
        self,
        inputs: Dict[str, Any],
        *,
        user_id: Optional[str] = None,
        session_id: Optional[str] = None,
        request_id: Optional[str] = None,
        tracer: Optional[Union["Tracer", List["Tracer"]]] = None,
    ) -> "ExecutionHandle":
        """Start workflow execution and return a streaming handle immediately.

        Does not block — the graph runs in the background. Use the handle to
        stream frames, await specific outputs, or collect the final result.

        Tracer flush happens automatically when the scheduler completes — no
        explicit finalize step needed.

        Args:
            inputs: Input data for the workflow
            user_id: Optional user identifier (auto-generated if not provided)
            session_id: Optional session identifier (auto-generated if not provided)
            request_id: Optional request identifier (auto-generated if not provided)
            tracer: Optional tracer(s) — overrides engine default for this execution.

        Returns:
            ExecutionHandle — async-iterable, supports ``await handle["op","var"]``
            and ``await handle.collect()``
        """
        user_id = user_id or str(uuid.uuid4())
        session_id = session_id or str(uuid.uuid4())
        request_id = request_id or str(uuid.uuid4())

        # Resolve tracers: per-call overrides engine default
        effective = tracer if tracer is not None else self._tracer
        tracers = (effective if isinstance(effective, list) else [effective]) if effective else []

        state = self._schema.create_state(
            inputs=inputs,
            user_id=user_id,
            session_id=session_id,
            request_id=request_id,
        )
        state.tracing = bool(tracers)  # skip per-op metrics/datetime when no tracer

        LOGGER.info(format_event("workflow_start", request_id=request_id, graph_name=self.name))

        # Capture references for the closure
        collector = self._collector
        graph_name = self.name

        queue: asyncio.Queue = asyncio.Queue()

        async def _run() -> None:
            try:
                await self.graph._scheduler.run(state, ("main",), output_queue=queue)
            except Exception as e:
                queue.put_nowait(e)
            except asyncio.CancelledError:
                queue.put_nowait(None)
                raise
            finally:
                # Flush tracers in background thread (fire-and-forget).
                # State is complete at this point — safe to collect traces.
                if tracers:
                    from operon.core.tracing import get_flush_worker

                    get_flush_worker().submit(tracers, collector, state)
                LOGGER.info(
                    format_event("workflow_done", request_id=request_id, graph_name=graph_name)
                )

        scheduler_task = asyncio.create_task(_run())
        return ExecutionHandle(queue, scheduler_task, state)

    async def run(
        self,
        inputs: Dict[str, Any],
        *,
        user_id: Optional[str] = None,
        session_id: Optional[str] = None,
        request_id: Optional[str] = None,
        tracer: Optional[Union["Tracer", List["Tracer"]]] = None,
    ) -> Dict[str, Any]:
        """Execute the workflow with given inputs.

        Each call creates a fresh state, so the same engine can be
        used for multiple independent executions.  Equivalent to::

            handle = engine.start(inputs, tracer=tracer, ...)
            result = await handle.collect(unwrap=True)

        Tracer flush happens automatically inside ``start()`` when the
        scheduler completes.

        Args:
            inputs: Input data for the workflow
            user_id: Optional user identifier (auto-generated if not provided)
            session_id: Optional session identifier (auto-generated if not provided)
            request_id: Optional request identifier (auto-generated if not provided)
            tracer: Optional tracer or list of tracers for observability.
                    Overrides the default tracer set in ``Operon(..., tracer=...)``.

        Returns:
            Dictionary containing workflow outputs plus "$state" key
            with the MemoryState for debugging/tracing access.
        """
        user_id = user_id or str(uuid.uuid4())
        session_id = session_id or str(uuid.uuid4())
        request_id = request_id or str(uuid.uuid4())

        context: Dict[str, Any] = {
            "user_id": user_id,
            "session_id": session_id,
            "request_id": request_id,
        }

        # Apply before_run middleware (in order)
        for mw in self._middleware:
            inputs = await mw.before_run(self.graph, inputs, context)

        handle = self.start(
            inputs,
            user_id=user_id,
            session_id=session_id,
            request_id=request_id,
            tracer=tracer,
        )

        try:
            result = await handle.collect(unwrap=True)
        except Exception as e:
            for mw in reversed(self._middleware):
                await mw.on_error(self.graph, inputs, e, context)
            raise

        result["$state"] = handle.state

        # Apply after_run middleware (in reverse order)
        for mw in reversed(self._middleware):
            result = await mw.after_run(self.graph, inputs, result, context)

        return result

    async def __call__(self, inputs: Dict[str, Any], **kwargs) -> Dict[str, Any]:
        """Callable syntax for running the workflow.

        Equivalent to calling run() with the same arguments.

        Args:
            inputs: Input data for the workflow
            **kwargs: Additional arguments passed to run()

        Returns:
            Dictionary containing workflow outputs plus "$state" key
        """
        return await self.run(inputs, **kwargs)

    def serve(
        self,
        *,
        path: str = "/",
        host: str = "0.0.0.0",
        port: int = 8000,
        stream: Optional[bool] = None,
        websocket: bool = False,
        backend: str = "python",
        **kwargs: Any,
    ) -> None:
        """Serve this workflow as an HTTP API.

        Convenience wrapper around ``operon.serve.OperonApp``. Requires operon-serve
        to be installed.

        Args:
            path: URL path for the endpoint (default: "/").
            host: Bind address.
            port: Bind port.
            stream: Enable SSE streaming endpoint. None = auto-detect.
            websocket: Enable WebSocket endpoint.
            backend: "python" (FastAPI/uvicorn) or "rust" (Axum).
            **kwargs: Extra arguments forwarded to ``OperonApp.serve()``.
        """
        try:
            from operon.serve import OperonApp
        except ImportError:
            raise ImportError(
                "operon-serve is required for engine.serve(). Install it with: pip install operon-serve"
            ) from None

        app = OperonApp(tracer=self._tracer)
        app.endpoint(path, graph=self.graph, stream=stream, websocket=websocket)
        app.serve(host=host, port=port, backend=backend, **kwargs)

    async def batch(
        self,
        inputs_list: List[Dict[str, Any]],
        *,
        concurrency: int = 10,
        **kwargs: Any,
    ) -> List[Dict[str, Any]]:
        """Run the workflow concurrently on multiple inputs.

        Args:
            inputs_list: List of input dicts to process.
            concurrency: Max concurrent executions (default: 10).
            **kwargs: Extra arguments forwarded to ``run()``.

        Returns:
            List of result dicts in the same order as inputs.
        """
        sem = asyncio.Semaphore(concurrency)

        async def _run(inp: Dict[str, Any]) -> Dict[str, Any]:
            async with sem:
                return await self.run(inp, **kwargs)

        return list(await asyncio.gather(*[_run(inp) for inp in inputs_list]))

    def cli(self) -> None:
        """Interactive CLI mode — read JSON from stdin, print result to stdout."""
        inputs = json.load(sys.stdin)
        result = asyncio.run(self.run(inputs))
        # Filter internal keys for clean output
        output = {k: v for k, v in result.items() if not k.startswith("$")}
        json.dump(output, sys.stdout, indent=2)
        sys.stdout.write("\n")

    def input_schema(self) -> Dict[str, Any]:
        """Return JSON Schema describing the workflow's expected inputs."""
        return self._params_to_schema(self.graph.inputs or {}, f"{self.name}_input")

    def output_schema(self) -> Dict[str, Any]:
        """Return JSON Schema describing the workflow's outputs."""
        return self._params_to_schema(self.graph.outputs or {}, f"{self.name}_output")

    @staticmethod
    def _params_to_schema(params: dict, title: str) -> Dict[str, Any]:
        """Convert a dict of Param objects to a JSON Schema dict."""
        properties = {}
        required = []
        for name, param in params.items():
            prop: Dict[str, Any] = {}
            if param.annotation is not None:
                type_map = {
                    int: "integer",
                    float: "number",
                    str: "string",
                    bool: "boolean",
                    list: "array",
                    dict: "object",
                }
                prop["type"] = type_map.get(param.annotation, "string")
            if param.description:
                prop["description"] = param.description
            if param.default is not None:
                prop["default"] = param.default
            else:
                required.append(name)
            properties[name] = prop
        schema: Dict[str, Any] = {"type": "object", "title": title, "properties": properties}
        if required:
            schema["required"] = required
        return schema

    def show(self) -> None:
        """Display workflow structure for debugging."""
        print(f"\n=== Operon Engine: {self.name} ===")
        self.graph.show()
        print()
        self._schema.show()

    def __repr__(self) -> str:
        return f"<Operon engine='{self.name}' ops={len(self.graph._ops)}>"
