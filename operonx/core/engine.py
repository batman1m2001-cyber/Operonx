"""Operon - Workflow execution engine.

This module provides the Operon class, an execution engine that runs
GraphOp workflows with state management and observability.

Example:
    ```python
    from operonx.core import Operon, GraphOp, START, END, PARENT
    from operonx.core.ops import FuncOp

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
from time import perf_counter
from typing import TYPE_CHECKING, Any, Callable, Dict, Iterator, List, Optional, Union

from operonx.core.loggings import LOGGER, format_event
from operonx.core.ops.graph.graph_op import GraphOp
from operonx.core.states import StateSchema

if TYPE_CHECKING:
    from operonx.core.ops.base import BaseOp
    from operonx.telemetry.consumer import Consumer


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

    def __init__(
        self,
        queue: asyncio.Queue,
        task: asyncio.Task,
        state: Any = None,
        trace: Any = None,
    ) -> None:
        self._queue = queue  # fed by root Scheduler
        self._scheduler_task = task  # task running the workflow
        self.state = state  # MemoryState for this execution (tracing access)
        # V3 tracing: WorkflowTrace populated live by BaseOp.run wrappers.
        # `None` when V3 tracing is disabled at import time (rare — kept
        # as an escape hatch for tests that don't want the buffer).
        self.trace = trace
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
                    if (
                        isinstance(item, tuple)
                        and len(item) == 3
                        and isinstance(item[2], BaseException)
                    ):
                        # Some scheduler paths wrap errors in a frame tuple.
                        self._error = item[2]
                        self._done = True
                        self._resolve_all_waiters(item[2])
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

    @property
    def scratch(self) -> Dict[str, Any]:
        """Per-call scratch dict on the underlying MemoryState.

        Read-anywhere; writes are race-free only when performed
        synchronously between ``engine.start()`` and the next ``await``.
        Prefer ``engine.start(scratch=...)`` to seed values entry ops will
        read.
        """
        return self.state._scratch

    @property
    def interrupts(self) -> list:
        """List of Interrupt events forwarded by the scheduler so far.

        Filters ``self._frames`` for the synthetic ``__interrupt__`` op tag
        and unwraps the payload, returning the original ``Interrupt``
        objects in arrival order. Useful for tests and consumer code that
        wants typed access without iterating frames manually.
        """
        return [
            data["__interrupt__"]
            for op, _ctx, data in self._frames
            if op == "__interrupt__" and isinstance(data, dict) and "__interrupt__" in data
        ]

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
            await self._await_scheduler_completion()
            return frames

        # mode == "group"
        out: dict[str, list[Any]] = {}
        async for _, _, data in self:
            for k, v in data.items():
                out.setdefault(k, []).append(v)

        await self._await_scheduler_completion()
        if unwrap:
            return {k: v[0] if len(v) == 1 else v for k, v in out.items()}
        return out

    async def _await_scheduler_completion(self) -> None:
        """Wait for the scheduler task's finally to complete before returning.

        ``_pump`` sets ``_done`` as soon as the scheduler puts ``None`` on
        the output queue at EOF — but that happens BEFORE the scheduler
        task's ``finally`` block runs (legacy flush_worker submit, new
        TracePipeline awaited flush, ContextVar resets). Awaiting the task
        ensures all teardown is observable by the caller of ``collect()``.
        """
        if not self._scheduler_task.done():
            try:
                await self._scheduler_task
            except (Exception, asyncio.CancelledError):
                # Errors from the scheduler task are already surfaced via
                # ``self._error`` / ``raise`` in __anext__. Don't double-raise.
                pass

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
            llm = LLMOp(name="llm", resource="gpt-4o", inputs={"prompt": ...})
            START >> llm >> END

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

    __slots__ = ["graph", "name", "_schema", "_collector", "_trace_consumers"]

    def __init__(
        self,
        graph: Union[GraphOp, Callable[..., GraphOp]],
        *,
        params: Optional[Dict[str, Any]] = None,
        trace: Optional[Union[str, "Consumer", List[Union[str, "Consumer"]]]] = None,
    ):
        """Initialize Operon engine with a GraphOp or a graph factory.

        Pure orchestrator — does **not** load ``.env`` or ``resources.yaml``.
        Call :func:`operonx.bootstrap` (or :meth:`ResourceHub.from_yaml` directly)
        before constructing the engine if your graph uses provider ops.
        Pure-compute graphs need no setup.

        Args:
            graph: A GraphOp workflow, or a callable that returns one.
                   When a callable is passed, it is invoked with ``**params``
                   immediately — call :func:`operonx.bootstrap` first if the
                   factory needs the hub.
            params: Keyword arguments passed to the graph factory. Ignored
                    when *graph* is already a GraphOp. Defaults to ``{}``.
            trace: V3 tracing. Accepts a ResourceHub key (str),
                   a :class:`Consumer` instance, or a list of either.
                   Each consumer gets ``handle.trace`` at the end of
                   every run and writes its own view (disk, Langfuse,
                   report, …). Failures are caught + logged per-consumer
                   so one bad backend never affects the call. Requires
                   :func:`operonx.bootstrap` when using string keys.
                   Examples::

                       trace="trace_local:default"
                       trace=CallbotLocalConsumer(config={"root": "/tmp/x"})
                       trace=["trace_langfuse:edupia", MyDebugConsumer()]

        Raises:
            RuntimeError: If a provider op needs the hub but none has been
                installed. The message points at ``operonx.bootstrap()``.
            TypeError: If a ``trace=`` item is neither a str nor a
                Consumer instance.
        """
        if callable(graph) and not isinstance(graph, GraphOp):
            graph = graph(**(params or {}))

        self.graph = graph
        self.name = graph.name
        self._trace_consumers = self._resolve_trace_consumers(trace)

        # Build graph and create schema immediately
        self.graph.build()
        self._schema = StateSchema(self.graph)

        # Eagerly init backends if a hub is already configured
        self._warmup_ops()

        LOGGER.debug(
            "Operon engine initialized for workflow [highlight]%s[/highlight]",
            self.name,
        )

    @staticmethod
    def _resolve_trace_consumers(trace: Any) -> List[Any]:
        """Resolve `trace=` argument → list of Consumer instances.

        Accepts three call styles for flexibility:

        * ``None`` → returns ``[]`` (tracing off).
        * ``str`` → ResourceHub key, resolved to a shared Consumer
          instance (typical production wiring via ``resources.yaml``).
        * :class:`Consumer` instance → used as-is (ad-hoc, testing,
          per-graph one-offs — no YAML round-trip needed).
        * ``list`` of any of the above — mixed is fine.

        Resolution happens once in ``__init__``; every ``start()`` reuses
        the same list, no per-call hub lookup.
        """
        # Late import: `Consumer` lives in a subpackage that imports
        # engine machinery — avoid the circular by resolving here.
        from operonx.core.registry import ResourceHub
        from operonx.telemetry.consumer import Consumer

        if trace is None:
            return []
        items = trace if isinstance(trace, list) else [trace]
        resolved: List[Any] = []
        for item in items:
            if isinstance(item, str):
                resolved.append(ResourceHub.instance().get(item))
            elif isinstance(item, Consumer):
                resolved.append(item)
            else:
                raise TypeError(
                    f"`trace=` item must be a str (ResourceHub key) or a "
                    f"Consumer instance; got {type(item).__name__}."
                )
        return resolved

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

    @property
    def schema(self) -> StateSchema:
        """Access the workflow state schema."""
        return self._schema

    def _all_ops_registry(self) -> Dict[str, "BaseOp"]:
        """Build ``{full_name: op}`` map for the whole graph tree.

        Used by ``bind_checkpointer`` so per-op observability filters
        (``@op(exclude=/include=/observe_max=)``) can be honoured.
        """
        registry: Dict[str, "BaseOp"] = {}

        def _walk(op):
            if getattr(op, "_full_name", None):
                registry[op._full_name] = op
            elif getattr(op, "name", None):
                registry[op.name] = op
            children = getattr(op, "_ops", None)
            if children:
                for child in children.values():
                    _walk(child)

        _walk(self.graph)
        return registry

    def start(
        self,
        inputs: Dict[str, Any],
        *,
        user_id: Optional[str] = None,
        session_id: Optional[str] = None,
        request_id: Optional[str] = None,
        trace_id: Optional[str] = None,
        scratch: Optional[Dict[str, Any]] = None,
        checkpointer=None,
    ) -> "ExecutionHandle":
        """Start workflow execution and return a streaming handle immediately.

        Does not block — the graph runs in the background. Use the handle to
        stream frames, await specific outputs, or collect the final result.

        V3 trace consumers (declared via ``Operon(..., trace=...)``) fire
        automatically once the scheduler completes — no explicit finalize
        needed.

        Args:
            inputs: Input data for the workflow
            user_id: Optional user identifier (auto-generated if not provided)
            session_id: Optional session identifier (auto-generated if not provided)
            request_id: Optional request identifier (auto-generated if not provided)
            scratch: Optional initial values for per-call scratch space. Applied
                synchronously before the scheduler task is created — race-free.
                Equivalent to writing ``handle.scratch[k] = v`` before the first
                ``await`` after ``start()``, but guaranteed to be visible to
                entry ops.

        Returns:
            ExecutionHandle — async-iterable, supports ``await handle["op","var"]``
            and ``await handle.collect()``
        """
        user_id = user_id or str(uuid.uuid4())
        session_id = session_id or str(uuid.uuid4())
        request_id = request_id or str(uuid.uuid4())

        state = self._schema.create_state(
            inputs=inputs,
            user_id=user_id,
            session_id=session_id,
            request_id=request_id,
        )
        # Legacy ``state.tracing`` flag retained for back-compat with code
        # that reads it (e.g. inside op.run() for per-op metric writes).
        # No longer load-bearing for trace dispatch.
        state.tracing = False

        # Seed scratch synchronously before the scheduler task is created.
        if scratch:
            state._scratch.update(scratch)

        # Phase 2: wire the checkpointer to the state's write funnel BEFORE
        # the scheduler runs, so no writes are missed. Unsubscribe hook is
        # invoked in the run's finally-block to detach cleanly.
        _cp_unsubscribe = None
        if checkpointer is not None:
            from operonx.checkpoint.bridge import bind_checkpointer

            _cp_unsubscribe = bind_checkpointer(
                state,
                checkpointer,
                op_registry=self._all_ops_registry(),
            )

        LOGGER.info(format_event("workflow_start", request_id=request_id, graph_name=self.name))

        graph_name = self.name
        queue: asyncio.Queue = asyncio.Queue()

        # V3 tracing: per-run WorkflowTrace buffer, ContextVar-scoped.
        # Always created — consumers read `handle.trace` after the run.
        # Ops append `OpExecution` records automatically via the
        # `BaseOp.run()` recording hook — no author code required.
        from operonx.core.workflow_trace import WorkflowTrace
        from operonx.core.workflow_trace import _current_trace as _v3_trace_var

        _wf_trace = WorkflowTrace(
            trace_id=trace_id or request_id,
            workflow_name=self.name,
            started_at=perf_counter(),
            ended_at=0.0,
            metadata={
                "request_id": request_id,
                "user_id": user_id,
                "session_id": session_id,
                **({"tags": list(state.tags)} if getattr(state, "tags", None) else {}),
            },
        )

        async def _run() -> None:
            v3_token = _v3_trace_var.set(_wf_trace)
            try:
                await self.graph._scheduler.run(state, ("main",), output_queue=queue)
            except asyncio.CancelledError:
                # Phase 2b3 T5: notify the checkpointer of run-level cancel so
                # audit trails and speculative-chain teardown observers hear it.
                if checkpointer is not None:
                    try:
                        checkpointer.on_cancel(("main",))
                    except Exception:
                        LOGGER.exception("checkpointer.on_cancel failed")
                queue.put_nowait(None)
                raise
            except BaseException as e:  # includes ObserveBudgetExceeded (Phase 2)
                queue.put_nowait(e)
                # Do NOT re-raise a BaseException — the ExecutionHandle re-raises
                # it to the caller via _pump when the value is dequeued. Bubbling
                # here would surface as an unhandled task exception.
            finally:
                _v3_trace_var.reset(v3_token)
                _wf_trace.ended_at = perf_counter()
                # Detach any Phase 2 observers bound at start().
                if _cp_unsubscribe is not None:
                    try:
                        _cp_unsubscribe()
                    except Exception:
                        LOGGER.exception("checkpointer unsubscribe failed")
                # Phase 2b3 H3: drain any pending InterruptOp futures so the
                # state's response bus doesn't leak entries after the run.
                if state._interrupt_responses:
                    for _iid, _fut in list(state._interrupt_responses.items()):
                        if not _fut.done():
                            _fut.cancel()
                    state._interrupt_responses.clear()
                # V3 consumers — auto-invoke on the completed trace.
                # `asyncio.to_thread` so a slow HTTP consumer (Langfuse)
                # doesn't block the event loop; per-consumer try/except
                # so one broken backend never affects the call.
                for _consumer in self._trace_consumers:
                    try:
                        await asyncio.to_thread(_consumer.consume, _wf_trace)
                    except Exception:
                        LOGGER.exception(
                            "trace consumer %r failed on trace %s",
                            type(_consumer).__name__,
                            _wf_trace.trace_id,
                        )
                LOGGER.info(
                    format_event("workflow_done", request_id=request_id, graph_name=graph_name)
                )

        scheduler_task = asyncio.create_task(_run())
        return ExecutionHandle(queue, scheduler_task, state, trace=_wf_trace)

    async def run(
        self,
        inputs: Dict[str, Any],
        *,
        user_id: Optional[str] = None,
        session_id: Optional[str] = None,
        request_id: Optional[str] = None,
        trace_id: Optional[str] = None,
        scratch: Optional[Dict[str, Any]] = None,
        checkpointer=None,
    ) -> Dict[str, Any]:
        """Execute the workflow with given inputs.

        Each call creates a fresh state, so the same engine can be
        used for multiple independent executions.  Equivalent to::

            handle = engine.start(inputs, ...)
            result = await handle.collect(unwrap=True)

        V3 trace consumers (declared via ``Operon(..., trace=...)``)
        fire automatically inside ``start()`` when the scheduler
        completes.

        Args:
            inputs: Input data for the workflow
            user_id: Optional user identifier (auto-generated if not provided)
            session_id: Optional session identifier (auto-generated if not provided)
            request_id: Optional request identifier (auto-generated if not provided)
            scratch: Optional initial values for per-call scratch space.

        Returns:
            Dictionary containing workflow outputs plus "$state" key
            with the MemoryState for debugging/tracing access.
        """
        user_id = user_id or str(uuid.uuid4())
        session_id = session_id or str(uuid.uuid4())
        request_id = request_id or str(uuid.uuid4())

        handle = self.start(
            inputs,
            user_id=user_id,
            session_id=session_id,
            request_id=request_id,
            trace_id=trace_id,
            scratch=scratch,
            checkpointer=checkpointer,
        )

        result = await handle.collect(unwrap=True)
        result["$state"] = handle.state

        return result

    async def invoke(self, inputs: Dict[str, Any], **kwargs) -> Dict[str, Any]:
        """LangGraph-familiar alias for :meth:`run`. Same signature."""
        return await self.run(inputs, **kwargs)

    # ------------------------------------------------------------------
    # Streaming helpers (module-local; see stream() below)
    # ------------------------------------------------------------------

    @staticmethod
    def _idx_to_op_var_dispatch(state, idx: int):
        for (op_name, var), i in state.schema._var_to_idx.items():
            if i == idx:
                return op_name, var
        return "?", "?"

    async def stream(  # noqa: C901 — routing on mode; keeps engine surface compact
        self,
        inputs: Dict[str, Any],
        *,
        mode: str = "updates",
        channels: Optional[List[str]] = None,
        checkpointer: Optional[Any] = None,
        **kwargs: Any,
    ) -> "asyncio.AsyncGenerator[Any, None]":
        """LangGraph-familiar streaming iterator.

        Args:
            inputs: workflow inputs (same as ``run``/``invoke``)
            mode: one of
                - ``"updates"`` — yields ``{op_name: {var: value, ...}}`` per op
                  completion (matches LangGraph's ``stream_mode="updates"``)
                - ``"values"`` — yields the full state snapshot per step;
                  requires a checkpointer (auto-created in-memory if omitted)
                - ``"frames"`` — yields ``(op, ctx, data)`` operonx frames
                - ``"custom"`` — yields :class:`~operonx.checkpoint.CustomEvent`
                  emitted by any :class:`~operonx.EmitOp`, optionally filtered
                  by ``channels=[...]``
            channels: for ``mode="custom"`` only — restrict to these channel names
            checkpointer: for ``mode="values"``; auto-created InMemory if None
            **kwargs: forwarded to ``start()`` (user_id, session_id, etc.)

        Yields:
            mode-specific chunks (see above).
        """
        import asyncio

        from operonx.checkpoint import InMemoryCheckpointer
        from operonx.checkpoint.base import CustomEvent
        from operonx.checkpoint.bridge import bind_custom_bus

        # Only "values" mode strictly needs a checkpointer. Create an in-memory
        # one on demand so callers don't have to plumb it manually.
        if mode == "values" and checkpointer is None:
            checkpointer = InMemoryCheckpointer()

        # For mode="custom", subscribe to the state's custom bus and buffer
        # events into a local queue. Handle is used for its scheduler task.
        if mode == "custom":
            queue: asyncio.Queue = asyncio.Queue()

            def _sink(evt: CustomEvent):
                if channels is None or evt.channel in channels:
                    queue.put_nowait(evt)

            handle = self.start(inputs, checkpointer=checkpointer, **kwargs)
            op_registry = self._all_ops_registry()
            _unbind = bind_custom_bus(handle.state, _sink, op_registry=op_registry)
            drainer = None
            getter = None
            try:
                # Drain the frame queue in the background so scheduler can
                # progress; we ignore frames here — only custom events.
                async def _drain_frames():
                    async for _ in handle:
                        pass

                drainer = asyncio.create_task(_drain_frames())
                while not (drainer.done() and queue.empty()):
                    getter = asyncio.create_task(queue.get())
                    done, _pending = await asyncio.wait(
                        {getter, drainer},
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                    if getter in done:
                        yield getter.result()
                    else:
                        getter.cancel()
                        # Drain any remaining events after scheduler done.
                        while not queue.empty():
                            yield queue.get_nowait()
                        break
            finally:
                _unbind()
                # Phase 2b3 B3: cancel the scheduler on caller break/error so
                # long-running ops (LLM calls, DB writes) don't keep burning
                # resources with no consumer.
                handle.cancel()
                if drainer and not drainer.done():
                    drainer.cancel()
                if getter and not getter.done():
                    getter.cancel()
            return

        # For "updates" and "values" we need per-op-completion granularity,
        # not just the scheduler's output-frame stream (which only fires for
        # ops that push to PARENT / END). We piggy-back on the state's write
        # bus so every op invocation yields exactly once.
        if mode in ("updates", "values"):
            if mode == "values" and checkpointer is None:
                checkpointer = InMemoryCheckpointer()
            handle = self.start(inputs, checkpointer=checkpointer, **kwargs)
            state = handle.state

            # Reverse index built once.
            idx_to_key = {
                idx: (op_name, var) for (op_name, var), idx in state.schema._var_to_idx.items()
            }

            # Buffer per-step writes into a list of updates. Each element
            # in step_updates is {op_name: {var: value, ...}} for one step.
            step_updates: Dict[int, Dict[str, Dict[str, Any]]] = {}

            def _record(idx: int, ctx_key: tuple, value):
                op_name, var = idx_to_key.get(idx, ("?", "?"))
                step = state._current_step
                step_updates.setdefault(step, {}).setdefault(op_name, {})[var] = value

            state.subscribe_writes(_record)
            try:
                # Consume the frame stream to drive the scheduler forward;
                # we don't yield from it, but we need to advance so writes
                # keep happening. Track "already yielded" step to yield newly-
                # completed steps as they land.
                last_yielded = -1
                async for _ in handle:
                    current = state._current_step
                    while last_yielded < current - 1:
                        last_yielded += 1
                        if mode == "updates":
                            batch = step_updates.pop(last_yielded, {})
                            if batch:
                                yield batch
                        else:  # values
                            if checkpointer is not None:
                                try:
                                    yield checkpointer.get_state(last_yielded)
                                except Exception:
                                    pass
                # Drain any final steps after the handle completes.
                current = state._current_step
                while last_yielded < current:
                    last_yielded += 1
                    if mode == "updates":
                        batch = step_updates.pop(last_yielded, {})
                        if batch:
                            yield batch
                    else:
                        if checkpointer is not None:
                            try:
                                yield checkpointer.get_state(last_yielded)
                            except Exception:
                                pass
            finally:
                state.unsubscribe_writes(_record)
                # Phase 2b3 B3: cancel scheduler on caller break so a partial
                # consume doesn't leave the graph running with no listener.
                handle.cancel()
            return

        handle = self.start(inputs, checkpointer=checkpointer, **kwargs)

        if mode == "frames":
            try:
                async for frame in handle:
                    yield frame
            finally:
                # Phase 2b3 B3: cancel on caller break/error.
                handle.cancel()
            return

        raise ValueError(
            f"engine.stream(mode={mode!r}) — valid modes: 'updates', 'values', 'frames', 'custom'"
        )

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

        Convenience wrapper around ``operonx.serve.OperonApp``. Requires operonx-serve
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
            from operonx.serve import OperonApp
        except ImportError:
            raise ImportError(
                "operonx-serve is required for engine.serve(). Install it with: pip install operonx-serve"
            ) from None

        app = OperonApp()
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
