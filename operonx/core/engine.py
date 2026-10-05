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
    print(result.get("$errors"))  # {"my-workflow.processor": "..."} if it raised
    ```
"""

import asyncio
import json
import sys
import time
import uuid
from collections import deque
from time import perf_counter
from typing import TYPE_CHECKING, Any, Callable, Dict, Iterator, List, Optional, Sequence, Union

from operonx.core.loggings import LOGGER, format_event
from operonx.core.ops.graph.graph_op import GraphOp
from operonx.core.policy import ERRORS_MODES, RunPolicy, _FailFast
from operonx.core.runtime import _RunInfo
from operonx.core.states import StateSchema

if TYPE_CHECKING:
    from operonx.core.ops.base import BaseOp
    from operonx.telemetry.consumer import Consumer


_MISSING = object()

#: The op tag of the scheduler's synthetic interrupt record. It travels on
#: the frame queue so `handle.interrupts` and raw iteration see it, but it
#: is not an output: merged into a result it became a key no graph
#: declares, holding an object `json.dumps` rejects.
_INTERRUPT_TAG = "__interrupt__"

#: What ``Operon.stream(mode=...)`` takes, alone or as a list.
STREAM_MODES = ("updates", "values", "frames", "custom", "interrupts", "tasks")


def _go_live(consumer: "Consumer", trace: Any) -> None:
    """Hand a live consumer the run as it goes: ``on_start`` now, then
    ``on_execution`` from every ``trace.record``. Either raising is logged
    once per run and never reaches it."""
    failed = []

    def report(hook: str) -> None:
        if not failed:
            failed.append(hook)
            LOGGER.exception(
                "live trace consumer %r failed in %s on trace %s; its other calls in this "
                "run are not logged",
                type(consumer).__name__,
                hook,
                trace.trace_id,
            )

    try:
        consumer.on_start(trace)
    except Exception:
        report("on_start")

    def on_execution(execution: Any) -> None:
        try:
            consumer.on_execution(trace, execution)
        except Exception:
            report("on_execution")

    trace._execution_listeners.append(on_execution)


def _ingress_of(graph: Any) -> Optional[str]:
    """The full name of an ingress door anywhere in *graph*, or None."""
    for node in (getattr(graph, "_ops", None) or {}).values():
        if getattr(node, "door", None) == "ingress":
            return node.full_name
        found = _ingress_of(node)
        if found is not None:
            return found
    return None


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

        # Ops that raised: {op_full_name: error_text}, {} when none
        failed = handle.errors
    """

    def __init__(
        self,
        queue: asyncio.Queue,
        task: asyncio.Task,
        state: Any = None,
        trace: Any = None,
        graph_name: str = "",
    ) -> None:
        self._queue = queue  # fed by root Scheduler
        self._scheduler_task = task  # task running the workflow
        self.state = state  # MemoryState for this execution (tracing access)
        # The graph's name, which is the first half of every declared cell
        # key. Reading `handle.state[name, "agent"]` from outside the run
        # is the point of declaring a cell, and without this the caller has
        # to already hold the engine to know the name — which a serve-layer
        # `on_close(session, handle)` does not.
        self.graph_name = graph_name
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
        except BaseException as exc:
            # Cancellation included: `cancel()` stops this pump, and the
            # None the scheduler enqueues on its way out then reaches
            # nobody. Whoever is parked on `_cond` — `result()`,
            # `collect()`, `async for` — has to be woken here or never.
            async with self._cond:
                if not self._done:
                    self._error = exc
                    self._done = True
                self._resolve_all_waiters(self._error)
                self._cond.notify_all()
            if not isinstance(exc, Exception):
                raise

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
        """Yield the next frame (op, ctx, data) as it arrives. Waits if no frames are available yet.

        Frames are the graph's **outputs**: an op appears here only if it
        writes a PARENT- or END-bound var. A generator wired into a
        downstream consumer yields nothing to this iterator no matter how
        much it produces — ``result()``/``collect()`` are built from these
        frames, so widening it would put every intermediate var into the
        result. ``Operon.stream(mode="updates")`` watches those ops.
        """
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

    @property
    def errors(self) -> Dict[str, str]:
        """The ops that raised in this run: ``{op_full_name: error_text}``.

        An op that raises does not raise out of the run; it is reported
        here instead. The key is the op's name in state
        (``"<graph>.<op>"``, a nested op by its full path) and the value
        the same text as its ``error`` cell — the first failure of each op.
        Empty when nothing failed. Readable while the run is going, which
        is how a long-lived run (a call) sees a failure before it ends.

        ``collect()`` and ``result()`` carry the same dict under
        ``"$errors"``, and ``Operon.run()`` does too — only when it is
        not empty.
        """
        if self.state is None:
            return {}
        return dict(self.state._op_errors)

    @property
    def drops(self) -> Dict[str, int]:
        """Items dropped by full ``on_full="drop_oldest"`` edges so far.

        ``{"<src full name> -> <dst full name>": count}``, empty when
        nothing was dropped. Readable while the run is going. An edge
        bounded with ``max_pending`` and the default ``on_full="wait"``
        never drops; it holds its producer instead.
        """
        if self.state is None:
            return {}
        return dict(self.state._edge_drops)

    def _with_errors(self, out: Dict[str, Any]) -> Dict[str, Any]:
        """Add ``"$errors"`` to a result payload when an op failed.

        Absent otherwise, so a caller comparing a clean run's keys against
        its declared outputs sees no change.
        """
        errors = self.errors
        if errors:
            out["$errors"] = errors
        stopped = getattr(self.state, "_durable", None)
        if stopped is not None and stopped.stopped == "interrupted":
            out["$interrupted"] = list(stopped.parked)
        elif stopped is not None and stopped.stopped == "drained":
            out["$drained"] = True
        return out

    async def collect(
        self, mode: str = "group", unwrap: bool = False
    ) -> dict[str, Any] | list[dict[str, Any]]:
        """Consume all frames and return collected output.

        Args:
            mode: ``"group"`` merges values by key into lists (default).
                  ``"flat"`` returns an ordered list of frame dicts.
            unwrap: When *True*, single-item lists become scalars.

        In ``"group"`` mode the dict also has ``"$errors"`` when an op
        raised (see :attr:`errors`). A ``"flat"`` list has nowhere to put
        it; read ``handle.errors``.
        """
        if mode == "flat":
            frames: list[dict[str, Any]] = []
            async for op, _, data in self:
                if op != _INTERRUPT_TAG:
                    frames.append(data)
            await self._await_scheduler_completion()
            return frames

        # mode == "group"
        out: dict[str, list[Any]] = {}
        async for op, _, data in self:
            if op == _INTERRUPT_TAG:
                continue
            for k, v in data.items():
                out.setdefault(k, []).append(v)

        await self._await_scheduler_completion()
        if unwrap:
            return self._with_errors({k: v[0] if len(v) == 1 else v for k, v in out.items()})
        return self._with_errors(out)

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
        internal buffer rather than re-iterating. Has ``"$errors"`` when an
        op raised (see :attr:`errors`).
        """
        # Wait for execution to complete if still running
        if not self._done:
            async with self._cond:
                while not self._done:
                    await self._cond.wait()
        if self._error:
            raise self._error
        out: dict[str, list[Any]] = {}
        for op, _, data in self._frames:
            if op == _INTERRUPT_TAG:
                continue
            for k, v in data.items():
                out.setdefault(k, []).append(v)
        if unwrap:
            return self._with_errors({k: v[0] if len(v) == 1 else v for k, v in out.items()})
        return self._with_errors(out)

    async def drain(self) -> None:
        """Stop the run for a deploy, without losing it: nothing new starts,
        the ops in flight finish and are journalled, and the run stops with
        status ``drained``. Returns once it has; ``result()`` then has
        ``"$drained": True``. ``await engine.resume(run_id)`` — on any worker
        — continues it. Needs ``Operon(journal=…)``.
        """
        recorder = getattr(self.state, "_durable", None)
        if recorder is None:
            raise RuntimeError(
                "drain(): this run has no journal= to continue it from; cancel() it instead"
            )
        recorder.drain()
        if not self._scheduler_task.done():
            try:
                await asyncio.shield(self._scheduler_task)
            except (Exception, asyncio.CancelledError):
                pass

    def cancel(self) -> None:
        """Cancel the workflow execution.

        Ends the run for everyone waiting on it: ``result()``,
        ``collect()``, ``await handle[op, var]`` and ``async for`` raise
        ``asyncio.CancelledError`` once the frames that landed before the
        cancel are consumed.

        A run that already finished keeps its result, and its teardown —
        trace consumers, checkpointer unsubscribe — is left to complete:
        ``stream()`` cancels in its ``finally`` on every exit, the clean
        one included.
        """
        if self._done:
            return
        # Marked here, not only in `_pump`'s handler: a pump cancelled
        # before its first step never runs that handler, and a caller doing
        # `cancel()` then `await result()` would wait on a mark nothing
        # sets. A waiter already parked on `_cond` parked after the pump
        # started, so the handler wakes it.
        self._error = asyncio.CancelledError("the run was cancelled")
        self._done = True
        self._resolve_all_waiters(self._error)
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

    __slots__ = [
        "graph",
        "name",
        "_schema",
        "_collector",
        "_trace_consumers",
        "inputs_expected",
        "_errors",
        "_max_concurrency",
        "_journal",
        "_durability",
        "_on_resume",
        "_fingerprint",
        "_carry",
    ]

    def __init__(
        self,
        graph: Union[GraphOp, Callable[..., GraphOp]],
        *,
        params: Optional[Dict[str, Any]] = None,
        trace: Optional[Union[str, "Consumer", List[Union[str, "Consumer"]]]] = None,
        errors: str = "record",
        max_concurrency: Optional[int] = None,
        journal: Any = None,
        durability: str = "async",
        on_resume: str = "restart",
        carry: Sequence[str] = (),
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
                   report, …); a live one (the ClickHouse and SQL stores)
                   also gets the run as it starts and each execution as it
                   lands, and lists the run as ``running`` meanwhile.
                   Failures are caught + logged per-consumer so one bad
                   backend never affects the call. Requires
                   :func:`operonx.bootstrap` when using string keys.
                   ``"local"`` is the built-in local consumer: inside a
                   project (an ``operonx.toml`` at or above the working
                   directory) it writes to ``<project>/.operonx/runs``.
                   ``"project"`` is the project's own ``[tracing]`` sinks,
                   chosen as its services' and jobs' are. Unset, nothing
                   is traced. A run started inside an op of a running
                   engine is part of that run and calls no consumer.
                   Examples::

                       trace="trace_local:default"
                       trace=CallbotLocalConsumer(config={"root": "/tmp/x"})
                       trace=["trace_langfuse:edupia", MyDebugConsumer()]

            errors: What a run does when an op fails. ``"record"`` (the
                   default) records it — ``"$errors"``, ``handle.errors`` —
                   and carries on, so one failing op does not end a live
                   session. ``"raise"`` ends the run at the first failure no
                   error edge handles: the ops still running are cancelled
                   and ``run()``, ``result()``, ``collect()``, iteration and
                   ``stream()`` raise :class:`OpFailed`. For jobs, tests and
                   batch scripts, which want the failure rather than a
                   result with a key missing.
            max_concurrency: The most ops of one run that may be running at
                   once, counted across every nested graph. A graph's own
                   ``concurrency=`` caps that graph only, so nested graphs
                   multiply (2 subgraphs at ``concurrency=2`` each run 4
                   ops); this is the one cap for the run. Counts the ops
                   that run as tasks (async, ``bound="cpu"``); a plain
                   ``def`` op runs inline and a subgraph holds no slot of
                   its own. ``None`` (the default): no shared cap.
            journal: Makes runs durable: a :class:`~operonx.durable.Journal`
                   (``MemoryJournal``, ``SqliteJournal``) records each
                   execution's yields and end with the cell writes they made,
                   and :meth:`resume` continues a run that stopped — after a
                   crash, on any worker opening the same journal — without
                   running again what had ended. ``None`` (the default): runs
                   are not recorded, at the cost of one ``is None`` test per
                   execution. See docs/RUNTIME_R3_PLAN.md.
            durability: With a journal, when steps are written: ``"async"``
                   (the default; a background writer, in order — a crash
                   loses at most the unwritten tail, which runs again),
                   ``"sync"`` (each step committed before the scheduler sees
                   its event) or ``"exit"`` (all at the end of the run).
            carry: Declared cells (``PARENT.declare``) a thread keeps between
                   its runs: a run started with ``thread_id=T`` begins with
                   them as T's last run left them, and saves them when it
                   ends. Needs a ``journal=``, where threads are kept.
            on_resume: An execution that had yielded and not ended when the
                   run stopped: ``"restart"`` runs it again and checks its
                   first yields against the journal; ``"fail"`` refuses.

        Raises:
            RuntimeError: If a provider op needs the hub but none has been
                installed. The message points at ``operonx.bootstrap()``.
            TypeError: If a ``trace=`` item is neither a str nor a
                Consumer instance.
        """
        if errors not in ERRORS_MODES:
            raise ValueError(f"errors= must be 'record' or 'raise', got {errors!r}")
        if max_concurrency is not None and (
            isinstance(max_concurrency, bool)
            or not isinstance(max_concurrency, int)
            or max_concurrency < 1
        ):
            raise ValueError(
                f"max_concurrency= takes an int >= 1 (ops running at once), got {max_concurrency!r}"
            )
        if callable(graph) and not isinstance(graph, GraphOp):
            graph = graph(**(params or {}))

        self._errors = errors
        self._max_concurrency = max_concurrency
        if journal is not None:
            from operonx.durable import DURABILITY, ON_RESUME, Journal

            if not isinstance(journal, Journal):
                raise TypeError(
                    f"journal= takes a Journal (MemoryJournal, SqliteJournal), not {journal!r}"
                )
            if durability not in DURABILITY:
                raise ValueError(
                    f"durability= is one of {', '.join(DURABILITY)}, got {durability!r}"
                )
            if on_resume not in ON_RESUME:
                raise ValueError(f"on_resume= is one of {', '.join(ON_RESUME)}, got {on_resume!r}")
        self._journal = journal
        self._durability = durability
        self._on_resume = on_resume
        self._fingerprint: Optional[str] = None
        self.graph = graph
        self.name = graph.name
        self._carry = tuple(carry)
        if self._carry:
            if journal is None:
                raise ValueError(
                    "carry= keeps cells between a thread's runs in the journal: pass journal="
                )
            declared = set(getattr(graph, "_shared_vars", None) or {})
            unknown = [name for name in self._carry if name not in declared]
            if unknown:
                raise ValueError(
                    f"carry={list(self._carry)}: {unknown} not declared by graph {graph.name!r} "
                    f"(PARENT.declare(...)); it declares {sorted(declared) or 'none'}"
                )
        self._trace_consumers = self._resolve_trace_consumers(trace)
        #: The runtime inputs a caller must pass, when whoever compiled the
        #: engine knows them (a served graph: its unbound parameters). The
        #: serve layer checks a door's RunRequest against it; None = unchecked.
        self.inputs_expected = None

        # Build graph and create schema immediately
        self.graph.build()
        self._schema = StateSchema(self.graph)

        # Eagerly init backends if a hub is already configured
        self._warmup_ops()

        LOGGER.debug(
            "Operon engine initialized for workflow [highlight]%s[/highlight]",
            self.name,
        )

    @property
    def trace_consumers(self) -> List[Any]:
        """The consumers every run of this engine feeds, resolved from
        ``trace=`` — for a caller that runs another graph beside this one
        and wants it traced to the same places (an eval's judges)."""
        return list(self._trace_consumers)

    @staticmethod
    def _resolve_trace_consumers(trace: Any) -> List[Any]:
        """Resolve `trace=` argument → list of Consumer instances.

        Accepts three call styles for flexibility:

        * ``None`` → returns ``[]`` (tracing off).
        * ``"project"`` → the project's ``[tracing]`` sinks
          (:func:`operonx.app.tracing.project_sinks`), each resolved as
          below; a ``ValueError`` outside a project.
        * ``str`` → ResourceHub key, resolved to a shared Consumer
          instance (typical production wiring via ``resources.yaml``).
          ``"local"`` is the built-in local consumer, no key needed.
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
        # The built-in consumer kinds (`trace_local:`, `trace_langfuse:`,
        # `run_store:`) register when `operonx.telemetry` is imported. A
        # resources.yaml naming one must not depend on some other module
        # having imported it first — that failed as "not found" while the
        # key was listed as available.
        import operonx.telemetry  # noqa: F401

        items = trace if isinstance(trace, list) else [trace]
        resolved: List[Any] = []
        for item in items:
            if item == "project":
                from operonx.app.tracing import project_sinks

                resolved.extend(Operon._resolve_trace_consumers(project_sinks()))
            elif item == "local":
                # The built-in local consumer, by the name `[tracing]` gives
                # it: what a job records to when nothing is configured. Not a
                # hub key, so it works without a resources.yaml.
                from operonx.telemetry.consumers.local import LocalConsumer

                resolved.append(LocalConsumer())
            elif isinstance(item, str):
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
        context: Any = None,
        run_id: Optional[str] = None,
        thread_id: Optional[str] = None,
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
            context: Any object the run's ops may read as
                ``run_context().context`` — a tenant, a feature flag set, a
                typed dataclass of what the caller knows. Information only:
                stored as given, never read by operonx.
            run_id: The run's id — its trace id, and with a ``journal=`` the
                key :meth:`resume` continues it by. Same as ``trace_id``.
            thread_id: The thread the run belongs to (``session_id`` names
                the same thing). With ``carry=``, the run starts with the
                carried cells as the thread's last run left them.

        The run's ``run_context().run_id`` is its trace id (``trace_id``,
        else ``request_id``), and ``thread_id`` is ``session_id`` as given
        (``None`` when not given).

        Returns:
            ExecutionHandle — async-iterable, supports ``await handle["op","var"]``
            and ``await handle.collect()``
        """
        if run_id is not None and trace_id is not None and run_id != trace_id:
            raise ValueError(f"run_id={run_id!r} and trace_id={trace_id!r} name one id; give one")
        trace_id = trace_id or run_id
        if thread_id is not None and session_id is not None and thread_id != session_id:
            raise ValueError(
                f"thread_id={thread_id!r} and session_id={session_id!r} name one thread; give one"
            )
        thread_id = thread_id or session_id
        session_id = session_id or thread_id
        user_id = user_id or str(uuid.uuid4())
        session_id = session_id or str(uuid.uuid4())
        request_id = request_id or str(uuid.uuid4())

        state = self._schema.create_state(
            inputs=inputs,
            user_id=user_id,
            session_id=session_id,
            request_id=request_id,
        )
        carried: Dict[str, Any] = {}
        if self._carry and thread_id is not None:
            saved = self._journal.load_thread(thread_id)
            carried = {name: saved[name] for name in self._carry if name in saved}
            self._seed(state, carried)
        if self._journal is not None:
            from operonx.durable import RunHeader

            recorder = self._recorder(trace_id or request_id, state)
            self._journal.open_run(
                RunHeader(
                    run_id=recorder.run_id,
                    fingerprint=self.fingerprint,
                    inputs=dict(inputs),
                    thread_id=thread_id,
                    carried=carried,
                )
            )
        return self._launch(
            state,
            request_id=request_id,
            user_id=user_id,
            session_id=session_id,
            trace_id=trace_id,
            thread_id=thread_id,
            scratch=scratch,
            checkpointer=checkpointer,
            context=context,
        )

    @property
    def fingerprint(self) -> str:
        """The compiled graph's structural fingerprint (what :meth:`resume`
        checks): its ops, their code and their wiring."""
        if self._fingerprint is None:
            from operonx.durable import graph_fingerprint

            self._fingerprint = graph_fingerprint(self.graph)
        return self._fingerprint

    def _seed(self, state: Any, values: Dict[str, Any]) -> None:
        """Set the root's declared cells to *values* — as they start, not
        as a write: no reducer, nothing journalled (the run's header keeps
        them)."""
        from operonx.core.states.cell import DEFAULT_CONTEXT

        for name, value in values.items():
            idx = state.schema.get_index(state.schema.name, name)
            if idx >= 0:
                state._cells[idx][DEFAULT_CONTEXT] = value

    def _recorder(self, run_id: str, state: Any) -> Any:
        from operonx.durable import RunRecorder

        names = {idx: key for key, idx in state.schema._var_to_idx.items()}
        recorder = RunRecorder(
            self._journal,
            run_id,
            names,
            root=state.schema.name,
            durability=self._durability,
            on_resume=self._on_resume,
        )
        state._durable = recorder
        state.subscribe_writes(recorder.on_write)
        return recorder

    async def resume(
        self,
        run_id: str,
        *,
        answers: Optional[Dict[str, Any]] = None,
        allow_graph_change: bool = False,
        checkpointer=None,
        context: Any = None,
    ) -> "ExecutionHandle":
        """Continue the journalled run *run_id* and return its handle::

            handle = await engine.resume("order-42")
            out = await handle.result()

        Every cell is put back as the journal last had it; an execution that
        ended is not run again — its yields are replayed, so what came after
        it is dispatched as before — and the rest runs. The result is the one
        the run would have had. Any process with the same graph and a
        journal holding the run can resume it.

        A run parked on interrupts (its result's ``"$interrupted"``) takes
        their responses as ``answers={interrupt_id: value}``; one left
        unanswered parks the run again. A drained run resumes as it is.

        Raises:
            RuntimeError: The engine has no ``journal=``.
            JournalError: The journal has no such run, the graph changed
                since the run started (``allow_graph_change=True`` resumes
                anyway, at the caller's risk), an answer names no question
                the run asked, or the graph reads through a door.
        """
        from operonx.durable import JournalError

        if self._journal is None:
            raise RuntimeError(
                f"resume({run_id!r}): this engine has no journal=; build it with the journal "
                "the run was recorded in"
            )
        door = _ingress_of(self.graph)
        if door is not None:
            raise JournalError(
                f"resume({run_id!r}): {door} is a door (ingress) — its items come from a live "
                "connection, which the journal does not hold. A served graph's runs are "
                "journalled for audit, not resumed"
            )
        header, steps = await asyncio.to_thread(self._journal.read, run_id)
        if header.fingerprint != self.fingerprint and not allow_graph_change:
            raise JournalError(
                f"run {run_id!r} ran graph {header.fingerprint} and this engine's graph is "
                f"{self.fingerprint}: an op, its code or its wiring changed, so the journal's "
                "steps may name other work. Resume with the graph it ran, or pass "
                "allow_graph_change=True"
            )
        request_id = str(uuid.uuid4())
        session_id = header.thread_id or str(uuid.uuid4())
        user_id = str(uuid.uuid4())
        state = self._schema.create_state(
            inputs=header.inputs,
            user_id=user_id,
            session_id=session_id,
            request_id=request_id,
        )
        self._seed(state, header.carried)  # what the thread gave it, then its own writes
        recorder = self._recorder(run_id, state)
        recorder.restore(state, steps, lenient=allow_graph_change)
        asked = set(recorder.questions.values())
        for interrupt_id in answers or {}:
            if interrupt_id not in asked:
                raise JournalError(
                    f"resume({run_id!r}): the run has no question {interrupt_id!r}; it asked "
                    f"{sorted(asked) or 'none'}"
                )
        recorder.answers = dict(answers or {})
        await asyncio.to_thread(self._journal.set_status, run_id, "running")
        return self._launch(
            state,
            request_id=request_id,
            user_id=user_id,
            session_id=session_id,
            trace_id=run_id,
            thread_id=header.thread_id,
            scratch=None,
            checkpointer=checkpointer,
            context=context,
        )

    def runs(self, status: Optional[str] = None) -> List[Any]:
        """The journalled runs (their headers), optionally only *status*
        (``"running"``: stopped or still going, the ones to resume)."""
        if self._journal is None:
            raise RuntimeError("runs(): this engine has no journal=")
        return self._journal.runs(status)

    def _launch(
        self,
        state: Any,
        *,
        request_id: str,
        user_id: str,
        session_id: str,
        trace_id: Optional[str],
        thread_id: Optional[str],
        scratch: Optional[Dict[str, Any]],
        checkpointer: Any,
        context: Any,
    ) -> "ExecutionHandle":
        """Run *state* in the background: the part of :meth:`start` that a
        resume shares."""
        # `state.tracing` gates the per-op metric writes in `BaseOp.run`.
        # It is not dead and it is not back-compat: `MemoryState` defaults
        # it on, so an op driven directly — as several tests do — records
        # metrics, while an engine run does not. Trace dispatch has not
        # depended on it since V3.
        state.tracing = False

        # Seed scratch synchronously before the scheduler task is created.
        if scratch:
            state._scratch.update(scratch)

        # Left None at the defaults, so the scheduler and the failure path
        # pay one `is None` test for a feature the run does not use.
        if self._errors != "record" or self._max_concurrency is not None:
            state._run_policy = RunPolicy(
                errors=self._errors,
                limiter=(
                    asyncio.Semaphore(self._max_concurrency)
                    if self._max_concurrency is not None
                    else None
                ),
            )

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

        # ``@op(observe_max=N)`` is enforced on every run, checkpointer or
        # not. It used to be counted inside bind_checkpointer's closure,
        # which made the circuit breaker a no-op under plain ``run()``.
        # Binds nothing (and returns None) when no op declares a budget.
        from operonx.checkpoint.bridge import bind_observe_budget

        _budget_unsubscribe = bind_observe_budget(state, self._all_ops_registry())

        LOGGER.info(format_event("workflow_start", request_id=request_id, graph_name=self.name))

        graph_name = self.name
        queue: asyncio.Queue = asyncio.Queue()

        # V3 tracing: per-run WorkflowTrace buffer, ContextVar-scoped.
        # Always created — consumers read `handle.trace` after the run.
        # Ops append `OpExecution` records automatically via the
        # `BaseOp.run()` recording hook — no author code required.
        from operonx.core.workflow_trace import WorkflowTrace, run_metadata
        from operonx.core.workflow_trace import _current_trace as _v3_trace_var

        # A run started inside an op of a running engine (a helper graph a
        # service op runs per call) is part of that op's run: it records
        # into its own handle's trace, and calls no consumer — a second
        # root trace per call is not something its caller asked for.
        nested_in = _v3_trace_var.get()
        consumers = [] if nested_in is not None else self._trace_consumers
        if nested_in is not None and self._trace_consumers:
            LOGGER.debug(
                "run of %s inside run %s: its trace consumers are not called",
                self.name,
                nested_in.trace_id,
            )

        _wf_trace = WorkflowTrace(
            trace_id=trace_id or request_id,
            workflow_name=self.name,
            started_at=perf_counter(),
            wall_started_at=time.time(),
            ended_at=0.0,
            metadata={
                # process-wide facts first (the code's version…); the
                # run's own ids and whatever its caller merges win
                **run_metadata(),
                "request_id": request_id,
                "user_id": user_id,
                "session_id": session_id,
                **({"tags": list(state.tags)} if getattr(state, "tags", None) else {}),
            },
            # The run's own failure records — the dict `handle.errors`
            # copies — so a consumer sees what no node shows.
            errors=state._op_errors,
        )

        state._run_info = _RunInfo(_wf_trace.run_id, thread_id, context)
        for _consumer in consumers:
            if _consumer.live:
                _go_live(_consumer, _wf_trace)

        recorder = state._durable

        async def _run() -> None:
            nonlocal recorder
            v3_token = _v3_trace_var.set(_wf_trace)
            outcome = "ok"
            try:
                await self.graph._scheduler.run(state, ("main",), output_queue=queue)
                if recorder is not None:
                    recorder.on_quiet = None  # ended: a stop asked now is too late
                if self._carry and thread_id is not None:
                    root = state.schema.name
                    kept = {name: state[root, name] for name in self._carry}
                    await asyncio.to_thread(self._journal.save_thread, thread_id, kept)
            except asyncio.CancelledError:
                if recorder is not None and recorder.stopping is not None and recorder._quiet:
                    # Parked on an interrupt or drained: stopped on purpose,
                    # by the recorder, once nothing could move — not a
                    # cancel. The handle ends with what the run made.
                    outcome = recorder.stopped = recorder.stopping
                    try:  # closed before the handle ends: its result and
                        # the journal's status agree
                        await asyncio.to_thread(recorder.close, outcome)
                    finally:
                        recorder = None
                        queue.put_nowait(None)
                    return
                outcome = "running"  # stopped, not finished: resumable
                if recorder is not None:
                    recorder.close(outcome)
                    recorder = None
                # Phase 2b3 T5: notify the checkpointer of run-level cancel so
                # audit trails and speculative-chain teardown observers hear it.
                if checkpointer is not None:
                    try:
                        checkpointer.on_cancel(("main",))
                    except Exception:
                        LOGGER.exception("checkpointer.on_cancel failed")
                queue.put_nowait(None)
                raise
            except _FailFast as e:
                # errors="raise": an op failed and the run was stopped. The
                # carrier is a BaseException only to get past every
                # `except Exception` on its way up; the caller gets OpFailed.
                outcome = "error"
                queue.put_nowait(e.failure)
            except BaseException as e:  # includes ObserveBudgetExceeded (Phase 2)
                outcome = "error"
                queue.put_nowait(e)
                # Do NOT re-raise a BaseException — the ExecutionHandle re-raises
                # it to the caller via _pump when the value is dequeued. Bubbling
                # here would surface as an unhandled task exception.
            finally:
                _v3_trace_var.reset(v3_token)
                _wf_trace.ended_at = perf_counter()
                if recorder is not None:
                    try:
                        await asyncio.to_thread(recorder.close, outcome)
                    except Exception:
                        LOGGER.exception("journal: run %s could not be closed", _wf_trace.trace_id)
                # Detach any Phase 2 observers bound at start().
                if _cp_unsubscribe is not None:
                    try:
                        _cp_unsubscribe()
                    except Exception:
                        LOGGER.exception("checkpointer unsubscribe failed")
                if _budget_unsubscribe is not None:
                    try:
                        _budget_unsubscribe()
                    except Exception:
                        LOGGER.exception("observe budget unsubscribe failed")
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
                for _consumer in consumers:
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
        if recorder is not None:
            recorder.on_quiet = scheduler_task.cancel
        return ExecutionHandle(queue, scheduler_task, state, trace=_wf_trace, graph_name=self.name)

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
        context: Any = None,
        run_id: Optional[str] = None,
        thread_id: Optional[str] = None,
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
            context: What the run's ops read as ``run_context().context``
                (see :meth:`start`).
            run_id: The run's id (see :meth:`start`).
            thread_id: The run's thread (see :meth:`start`).

        Returns:
            Dictionary containing workflow outputs plus "$state" key
            with the MemoryState for debugging/tracing access, plus
            "$errors" — ``{op_full_name: error_text}`` — when at least one
            op raised. An op that raises does not raise out of the run;
            its outputs, and those of every op after it, are missing —
            unless the engine was built with ``errors="raise"``, which
            raises :class:`OpFailed` instead. A ``BaseException``
            (``ObserveBudgetExceeded``, a misdirected ``Interrupt``) does
            raise.
        """
        handle = self.start(
            inputs,
            user_id=user_id,
            session_id=session_id,
            request_id=request_id,
            trace_id=trace_id,
            scratch=scratch,
            checkpointer=checkpointer,
            context=context,
            run_id=run_id,
            thread_id=thread_id,
        )

        try:
            result = await handle.collect(unwrap=True)
        except BaseException:
            # `asyncio.wait_for(engine.run(...), t)` cancels this await,
            # not the run behind it: the graph kept going after the caller
            # had been told it timed out, and the op after the timeout
            # still ran. The run belongs to this call, so it ends with it.
            handle.cancel()
            raise
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

    async def stream(
        self,
        inputs: Dict[str, Any],
        *,
        mode: Union[str, List[str], tuple] = "updates",
        channels: Optional[List[str]] = None,
        checkpointer: Optional[Any] = None,
        **kwargs: Any,
    ) -> "asyncio.AsyncGenerator[Any, None]":
        """LangGraph-familiar streaming iterator.

        Args:
            inputs: workflow inputs (same as ``run``/``invoke``)
            mode: one mode, or a list of them. A string yields that mode's
                chunks; a list yields ``(mode, chunk)`` pairs from one run,
                in the order they arrive. The modes:

                - ``"updates"`` — yields ``{op_name: {var: value, ...}}`` per op
                  completion (matches LangGraph's ``stream_mode="updates"``).
                  Covers **every** op, including generators in the middle of
                  the graph, and delivers each write as it lands. When an
                  :class:`~operonx.InterruptOp` suspends, also yields its
                  :class:`~operonx.checkpoint.InterruptEvent`, in order with
                  the updates, unless ``"interrupts"`` is also streamed;
                  answer with ``event.resume(value)``.
                - ``"interrupts"`` — yields only those ``InterruptEvent`` objects.
                - ``"values"`` — yields the full state snapshot per step;
                  requires a checkpointer (auto-created in-memory if omitted)
                - ``"frames"`` — yields ``(op, ctx, data)`` for ops that
                  write a graph **output** (PARENT- or END-bound). An op
                  feeding only a downstream consumer emits nothing here,
                  whatever it yields — use ``"updates"`` to watch those.
                - ``"custom"`` — yields :class:`~operonx.checkpoint.CustomEvent`
                  emitted by any :class:`~operonx.EmitOp`, optionally filtered
                  by ``channels=[...]``
                - ``"tasks"`` — yields :class:`~operonx.TaskStarted`,
                  :class:`~operonx.TaskFinished` and :class:`~operonx.TaskFailed`:
                  one start and one end per op invocation (a generator's end
                  comes after its last item) and per ``child()`` execution,
                  each with its attempt.
            channels: for ``mode="custom"`` only — restrict to these channel names
            checkpointer: for ``mode="values"``; auto-created InMemory if None
            **kwargs: forwarded to ``start()`` (user_id, session_id, etc.)

        Yields:
            mode-specific chunks, or ``(mode, chunk)`` for a list of modes.

        Raises:
            ValueError: an unknown mode, one given twice, or an empty list —
                before anything runs.
            BaseException: whatever ``run()`` raises — a fatal error such as
                ``ObserveBudgetExceeded`` — in every mode, after the chunks
                that landed before it. An op that raises is not fatal: the
                stream ends normally, as ``run()`` returns normally.
        """
        paired = not isinstance(mode, str)
        modes = tuple(mode) if paired else (mode,)
        unknown = [m for m in modes if m not in STREAM_MODES]
        if unknown or not modes or len(set(modes)) != len(modes):
            if not modes:
                problem = "takes at least one mode"
            elif unknown:
                problem = f"has no mode {unknown[0]!r}"
            else:
                problem = "names a mode more than once"
            raise ValueError(
                f"engine.stream(mode={mode!r}) {problem} — valid modes: "
                + ", ".join(repr(m) for m in STREAM_MODES)
                + "; pass a list for several at once"
            )
        async for name, chunk in self._stream_modes(inputs, modes, channels, checkpointer, kwargs):
            yield (name, chunk) if paired else chunk

    async def _stream_modes(  # noqa: C901 — one pacing loop for every mode
        self,
        inputs: Dict[str, Any],
        modes: tuple,
        channels: Optional[List[str]],
        checkpointer: Optional[Any],
        start_kwargs: Dict[str, Any],
    ) -> "asyncio.AsyncGenerator[tuple, None]":
        """One run, every requested mode, ``(mode, chunk)`` in arrival order.

        Each mode feeds the same pacing loop:

        - ``updates`` / ``values`` ride the state's write bus and are
          released by completed step (a step is complete once the counter
          has moved past it), so every op invocation yields exactly once;
        - an ``InterruptEvent`` follows the updates that landed before its
          op suspended, under ``interrupts`` if that mode is streamed, else
          under ``updates`` — the consumer has to see the question, or the
          run waits on an answer nobody can give;
        - ``custom`` and ``tasks`` events and ``frames`` are delivered as they
          arrive.

        The frames are drained in the background either way: the scheduler
        needs its output queue consumed to make progress.
        """
        from operonx.checkpoint import InMemoryCheckpointer
        from operonx.checkpoint.base import CustomEvent
        from operonx.checkpoint.bridge import bind_custom_bus, bind_interrupt_bus

        if "values" in modes and checkpointer is None:
            checkpointer = InMemoryCheckpointer()
        handle = self.start(inputs, checkpointer=checkpointer, **start_kwargs)
        state = handle.state
        # A wakeup per event; `ready` holds what is delivered as it arrives.
        signal: asyncio.Queue = asyncio.Queue()
        ready: deque = deque()
        undo: List[Callable[[], None]] = []

        def _arrived(name: str, chunk: Any) -> None:
            ready.append((name, chunk))
            signal.put_nowait(None)

        # Buffered per step: {step: {op_name: {var: value}}}. A write
        # signals the pacer. This loop used to be driven by `async for _ in
        # handle`, which only ticks on *output* frames — so a graph whose
        # single output lands at the end buffered every intermediate update
        # and released them all at once (four generator yields 150 ms apart,
        # all delivered together after the run).
        step_updates: Dict[int, Dict[str, Dict[str, Any]]] = {}
        if "updates" in modes:
            idx_to_key = {
                idx: (op_name, var) for (op_name, var), idx in state.schema._var_to_idx.items()
            }

            def _record(idx: int, ctx_key: tuple, value):
                op_name, var = idx_to_key.get(idx, ("?", "?"))
                step = state._current_step
                step_updates.setdefault(step, {}).setdefault(op_name, {})[var] = value
                signal.put_nowait(step)

            state.subscribe_writes(_record)
            undo.append(lambda: state.unsubscribe_writes(_record))

        interrupt_mode = (
            "interrupts" if "interrupts" in modes else "updates" if "updates" in modes else None
        )
        interrupts: List[Any] = []
        if interrupt_mode is not None:

            def _interrupted(event) -> None:
                interrupts.append(event)
                signal.put_nowait(None)

            undo.append(
                bind_interrupt_bus(state, _interrupted, op_registry=self._all_ops_registry())
            )
        if "custom" in modes:

            def _custom(evt: CustomEvent) -> None:
                if channels is None or evt.channel in channels:
                    _arrived("custom", evt)

            undo.append(bind_custom_bus(state, _custom, op_registry=self._all_ops_registry()))
        if "tasks" in modes and handle.trace is not None:

            def _task(evt: Any) -> None:
                _arrived("tasks", evt)

            handle.trace._task_listeners.append(_task)
            undo.append(lambda: handle.trace._task_listeners.remove(_task))

        stepped = "updates" in modes or "values" in modes

        def _flush(upto: int, last: int):
            """The completed steps in ``(last, upto]``, and the new last."""
            out = []
            while last < upto:
                last += 1
                if "updates" in modes:
                    batch = step_updates.pop(last, {})
                    if batch:
                        out.append(("updates", batch))
                if "values" in modes:
                    try:
                        out.append(("values", checkpointer.get_state(last)))
                    except Exception:
                        pass
            return out, last

        frames = "frames" in modes

        async def _drain_frames():
            async for frame in handle:
                if frames:
                    _arrived("frames", frame)

        drainer = None
        getter = None
        try:
            drainer = asyncio.create_task(_drain_frames())
            last = -1
            while not (drainer.done() and signal.empty()):
                getter = asyncio.create_task(signal.get())
                done, _pending = await asyncio.wait(
                    {getter, drainer},
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if getter not in done:
                    getter.cancel()
                if stepped:
                    batches, last = _flush(state._current_step - 1, last)
                    for item in batches:
                        yield item
                # After the updates that landed before the op suspended:
                # every op that committed before it has bumped the step.
                while interrupts:
                    yield interrupt_mode, interrupts.pop(0)
                while ready:
                    yield ready.popleft()
                while not signal.empty():
                    signal.get_nowait()
            # The run is over, so the last step is complete too.
            if stepped:
                batches, last = _flush(state._current_step, last)
                for item in batches:
                    yield item
            while interrupts:
                yield interrupt_mode, interrupts.pop(0)
            while ready:
                yield ready.popleft()
            # Over, or dead: what landed is delivered first, then a fatal
            # error is raised as `run()` raises it. Reading only `done()`
            # ended the stream cleanly instead.
            drainer.result()
        finally:
            for fn in undo:
                fn()
            # Phase 2b3 B3: cancel the scheduler on caller break/error so
            # long-running ops (LLM calls, DB writes) don't keep burning
            # resources with no consumer.
            handle.cancel()
            if drainer is not None and not drainer.done():
                drainer.cancel()
            if getter is not None and not getter.done():
                getter.cancel()

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
        websocket: bool = False,
        **kwargs: Any,
    ) -> None:
        """Serve this workflow over HTTP or a WebSocket.

        The one-endpoint convenience form. It builds the same ``ServeSpec``
        an ``operonx.toml`` would and serves that, so a project outgrowing
        it moves to the manifest without changing how anything runs.

        Args:
            path: URL path for the endpoint (default: "/").
            host: Bind address.
            port: Bind port.
            websocket: Serve a WebSocket endpoint instead of HTTP.
            **kwargs: Extra arguments forwarded to ``uvicorn.run()``.
        """
        # This used to delegate to `operonx.serve.OperonApp` — a package
        # that is not installed, not a dependency and not in this
        # repository, so every call raised ImportError. Meanwhile
        # `operonx.toml` had been declaring the same endpoint by hand for
        # the studio's benefit. The two halves are joined now: this is the
        # one-endpoint convenience form, and it builds the same ServeSpec
        # the manifest would have produced.
        from operonx.app.manifest import ServeSpec
        from operonx.app.serve.app import build_app

        try:
            import uvicorn
        except ImportError:
            raise ImportError(
                'engine.serve() needs the serve extra: pip install "operonx[serve]"'
            ) from None

        spec = ServeSpec(
            name=self.name or "serve",
            kind="websocket" if websocket else "http",
            graph="",  # the engine is passed directly
            path=path,
            host=host,
            port=port,
            session="per_connection" if websocket else "per_request",
            max_inflight=kwargs.pop("max_inflight", 4096) if websocket else None,
        )
        app = build_app((spec,), engines={spec.name: self})
        uvicorn.run(app, host=host, port=port, **kwargs)

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
