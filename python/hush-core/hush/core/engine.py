"""Hush - Workflow execution engine.

This module provides the Hush class, an execution engine that runs
GraphOp workflows with state management and observability.

Example:
    ```python
    from hush.core import Hush, GraphOp, START, END, PARENT
    from hush.core.ops import FuncOp

    # Define graph
    with GraphOp(name="my-workflow") as graph:
        node = FuncOp(name="processor", ...)
        START >> node >> END

    # Create engine and run
    engine = Hush(graph)
    result = await engine.run(inputs={"query": "hello"})
    print(result["answer"])  # workflow output
    print(result["$state"])  # access state for debugging/tracing
    ```
"""

import asyncio
import json
import sys
import uuid
from typing import TYPE_CHECKING, Any, AsyncGenerator, Dict, List, Optional, Union

from hush.core.loggings import LOGGER
from hush.core.middleware import Middleware
from hush.core.ops.graph.graph_op import GraphOp
from hush.core.states import StateSchema
from hush.core.utils.context import _output_queue

if TYPE_CHECKING:
    from hush.core.registry import ResourceHub
    from hush.core.tracing import Tracer


class Hush:
    """Workflow execution engine.

    Hush takes a GraphOp and provides execution capabilities:
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
        engine = Hush(graph)

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
        graph: GraphOp,
        *,
        env: Union[str, bool] = True,
        resources: Optional[str] = None,
        tracer: Optional[Union["Tracer", List["Tracer"]]] = None,
    ):
        """Initialize Hush engine with a GraphOp.

        Args:
            graph: The GraphOp workflow to execute.
                   Must be defined (context manager exited).
            env: Path to .env file, True to auto-find, or False to skip.
            resources: Path to resources.yaml. None = no resources loaded.
            tracer: Default tracer(s) for all run() calls. Can be overridden per-run.
                    Useful for serve() where tracer applies to every request.
        """
        # 1. Load .env
        self._load_env(env)

        # 2. Load resources (if provided)
        self.resources = self._load_resources(resources)

        self.graph = graph
        self.name = graph.name
        self._tracer = tracer

        # Build graph and create schema immediately
        self.graph.build()
        self._schema = StateSchema(self.graph)

        # Precompute trace collector (graph metadata, topo order)
        from hush.core.tracing import TraceCollector

        self._collector = TraceCollector(self.graph)
        self._middleware: List[Middleware] = []

        LOGGER.debug(
            "Hush engine initialized for workflow [highlight]%s[/highlight]",
            self.name,
        )

    @staticmethod
    def _load_env(env: Union[str, bool]) -> None:
        """Load .env file if requested."""
        if env is False:
            return
        try:
            from dotenv import load_dotenv
        except ImportError:
            # python-dotenv not installed — skip silently
            return
        if isinstance(env, str):
            load_dotenv(env)
        else:
            # Auto-find: walk up from CWD
            from dotenv import find_dotenv

            dotenv_path = find_dotenv(usecwd=True)
            if dotenv_path:
                load_dotenv(dotenv_path)

    @staticmethod
    def _load_resources(resources: Optional[str]) -> Optional["ResourceHub"]:
        """Load ResourceHub from a YAML file if provided."""
        if resources is None:
            return None
        from pathlib import Path

        from hush.core.registry import ResourceHub, set_global_hub

        path = Path(resources)
        if not path.exists():
            raise FileNotFoundError(
                f"Resources file not found: {resources}\n"
                f"  Create it or copy resources.starter.yaml:\n"
                f"    cp resources.starter.yaml {resources}"
            )
        hub = ResourceHub.from_yaml(path)
        set_global_hub(hub)
        ResourceHub.set_instance(hub)
        return hub

    @property
    def schema(self) -> StateSchema:
        """Access the workflow state schema."""
        return self._schema

    def use(self, middleware: Middleware) -> "Hush":
        """Add middleware to the engine. Returns self for chaining.

        Args:
            middleware: A Middleware instance to add.

        Returns:
            self, for fluent chaining: ``engine.use(m1).use(m2)``
        """
        self._middleware.append(middleware)
        return self

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
        used for multiple independent executions.

        Args:
            inputs: Input data for the workflow
            user_id: Optional user identifier (auto-generated if not provided)
            session_id: Optional session identifier (auto-generated if not provided)
            request_id: Optional request identifier (auto-generated if not provided)
            tracer: Optional tracer or list of tracers for observability.
                    Overrides the default tracer set in ``Hush(..., tracer=...)``.

        Returns:
            Dictionary containing workflow outputs plus "$state" key
            with the MemoryState for debugging/tracing access.
        """
        # Generate IDs if not provided
        user_id = user_id or str(uuid.uuid4())
        session_id = session_id or str(uuid.uuid4())
        request_id = request_id or str(uuid.uuid4())

        # Resolve tracers: per-run overrides engine default
        effective = tracer if tracer is not None else self._tracer
        if effective is not None:
            tracers = effective if isinstance(effective, list) else [effective]
        else:
            tracers = []

        context: Dict[str, Any] = {
            "user_id": user_id,
            "session_id": session_id,
            "request_id": request_id,
        }

        # Apply before_run middleware (in order)
        for mw in self._middleware:
            inputs = await mw.before_run(self.graph, inputs, context)

        LOGGER.info(
            "[title]\\[%s][/title] Running workflow [highlight]%s[/highlight]",
            request_id,
            self.name,
        )

        # Create fresh state for this run
        state = self._schema.create_state(
            inputs=inputs,
            user_id=user_id,
            session_id=session_id,
            request_id=request_id,
        )

        # Execute the graph
        try:
            result = await self.graph.run(state)
        except Exception as e:
            # on_error middleware (in reverse order)
            for mw in reversed(self._middleware):
                await mw.on_error(self.graph, inputs, e, context)
            raise

        # Collect + flush in background thread (non-blocking) — legacy tracer= path
        if tracers:
            from hush.core.tracing import get_flush_worker

            get_flush_worker().submit(tracers, self._collector, state)

        LOGGER.info(
            "[title]\\[%s][/title] Workflow [highlight]%s[/highlight] completed",
            request_id,
            self.name,
        )

        # Include state in result for debugging/tracing access
        result["$state"] = state

        # Apply after_run middleware (in reverse order)
        for mw in reversed(self._middleware):
            result = await mw.after_run(self.graph, inputs, result, context)

        return result

    async def stream(
        self,
        inputs: Dict[str, Any],
        *,
        user_id: Optional[str] = None,
        session_id: Optional[str] = None,
        request_id: Optional[str] = None,
        tracer: Optional[Union["Tracer", List["Tracer"]]] = None,
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """Execute the workflow and yield streaming events as they arrive.

        For graphs with generator ops, yields per-token events in real-time.
        Always yields a final "done" event with the complete result.

        Args:
            inputs: Input data for the workflow
            user_id: Optional user identifier
            session_id: Optional session identifier
            request_id: Optional request identifier
            tracer: Optional tracer or list of tracers for observability.
                    Overrides the default tracer set in ``Hush(..., tracer=...)``.

        Yields:
            Dicts with "type" key:
            - {"type": "token", "op": name, "data": {...}} — per-yield from generator ops
            - {"type": "done", "data": {full outputs}} — final result
        """
        user_id = user_id or str(uuid.uuid4())
        session_id = session_id or str(uuid.uuid4())
        request_id = request_id or str(uuid.uuid4())

        # Resolve tracers: per-run overrides engine default
        effective = tracer if tracer is not None else self._tracer
        if effective is not None:
            tracers = effective if isinstance(effective, list) else [effective]
        else:
            tracers = []

        context: Dict[str, Any] = {
            "user_id": user_id,
            "session_id": session_id,
            "request_id": request_id,
        }

        # Apply before_run middleware (in order)
        for mw in self._middleware:
            inputs = await mw.before_run(self.graph, inputs, context)

        LOGGER.info(
            "[title]\\[%s][/title] Streaming workflow [highlight]%s[/highlight]",
            request_id,
            self.name,
        )

        state = self._schema.create_state(
            inputs=inputs,
            user_id=user_id,
            session_id=session_id,
            request_id=request_id,
        )

        queue = asyncio.Queue()
        token = _output_queue.set(queue)

        try:
            task = asyncio.create_task(self.graph.run(state))

            while True:
                if task.done() and queue.empty():
                    break
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=0.05)
                    yield event
                except asyncio.TimeoutError:
                    continue

            result = task.result()
        except Exception as e:
            for mw in reversed(self._middleware):
                await mw.on_error(self.graph, inputs, e, context)
            raise
        finally:
            _output_queue.reset(token)

        # Legacy tracer= path
        if tracers:
            from hush.core.tracing import get_flush_worker

            get_flush_worker().submit(tracers, self._collector, state)

        LOGGER.info(
            "[title]\\[%s][/title] Workflow [highlight]%s[/highlight] stream completed",
            request_id,
            self.name,
        )

        result["$state"] = state

        # Apply after_run middleware (in reverse order)
        for mw in reversed(self._middleware):
            result = await mw.after_run(self.graph, inputs, result, context)

        yield {"type": "done", "data": result}

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
        **kwargs,
    ) -> None:
        """Serve this workflow as an HTTP API.

        Convenience wrapper around ``hush.serve.HushApp``. Requires hush-serve
        to be installed.

        Args:
            path: URL path for the endpoint (default: "/").
            host: Bind address.
            port: Bind port.
            stream: Enable SSE streaming endpoint. None = auto-detect.
            websocket: Enable WebSocket endpoint.
            backend: "python" (FastAPI/uvicorn) or "rust" (Axum).
            **kwargs: Extra arguments forwarded to ``HushApp.serve()``.
        """
        try:
            from hush.serve import HushApp
        except ImportError:
            raise ImportError(
                "hush-serve is required for engine.serve(). "
                "Install it with: pip install hush-serve"
            ) from None

        app = HushApp(tracer=self._tracer)
        app.endpoint(path, graph=self.graph, stream=stream, websocket=websocket)
        app.serve(host=host, port=port, backend=backend, **kwargs)

    async def batch(
        self,
        inputs_list: List[Dict[str, Any]],
        *,
        concurrency: int = 10,
        **kwargs,
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
                type_map = {int: "integer", float: "number", str: "string", bool: "boolean", list: "array", dict: "object"}
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
        print(f"\n=== Hush Engine: {self.name} ===")
        self.graph.show()
        print()
        self._schema.show()

    def __repr__(self) -> str:
        return f"<Hush engine='{self.name}' ops={len(self.graph._ops)}>"
