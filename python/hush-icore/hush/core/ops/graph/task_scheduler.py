"""Task-based workflow scheduler.

Replaces event-based scheduler.py. Single scheduler per workflow execution.
Uses asyncio.wait(FIRST_COMPLETED) for concurrent task dispatch.

Phase 1: Batch ops (sync/async, graph, branch)
Phase 2: Generator/streaming ops
Phase 3: Loop support
"""

import asyncio
import contextvars
import inspect
import logging
import traceback
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from time import perf_counter
from typing import Any, Dict, List, Optional, Set, Tuple

from hush.core.utils.context import _output_queue

LOGGER = logging.getLogger("hush.core")

# ── ContextVar: current scheduler for nested GraphOp.run() ──
_current_scheduler: contextvars.ContextVar[Optional["WorkflowScheduler"]] = (
    contextvars.ContextVar("_current_scheduler", default=None)
)


def get_current_scheduler() -> "WorkflowScheduler":
    """Get the active scheduler. Raises if none set."""
    scheduler = _current_scheduler.get()
    if scheduler is None:
        raise RuntimeError(
            "No WorkflowScheduler found. "
            "GraphOp must run inside Hush engine: engine = Hush(graph); await engine.run()"
        )
    return scheduler


def _is_gen(op_obj) -> bool:
    """Check if op is a generator (sync or async)."""
    fn = getattr(op_obj, "core", None)
    return fn is not None and (
        inspect.isgeneratorfunction(fn) or inspect.isasyncgenfunction(fn)
    )


PENDING = object()


@dataclass
class Task:
    """A unit of work for the scheduler."""
    op_name: str        # local name within graph (e.g. "prompt")
    context_id: tuple   # ("main",) or ("main", "[0]", ...)
    graph: Any = None   # GraphOp that owns this op


@dataclass
class YieldEvent:
    """Generator yielded one item."""
    op_name: str
    stream_ctx: tuple
    result: dict
    graph: Any = None


class WorkflowScheduler:
    """Task-based workflow scheduler.

    One instance per workflow execution (per request_id).
    All ops (including nested GraphOp children) dispatch through this scheduler.

    Usage:
        scheduler = WorkflowScheduler()
        outputs, stream_ctxs = await scheduler.execute_graph(graph, state, ctx, parent_ctx, request_id)
    """

    def __init__(self):
        self._cancelled = False
        self._aborted = False

    def cancel(self):
        """Graceful stop: no new tasks dispatched, active finish."""
        self._cancelled = True

    def abort(self):
        """Force stop: cancel all active tasks."""
        self._aborted = True

    async def execute_graph(
        self,
        graph,
        state,
        context_id,
        parent_context,
        request_id: str,
    ) -> Tuple[Dict[str, Any], List]:
        """Execute a graph's ops. Called by GraphOp.run() or top-level.

        Returns: (outputs_dict, stream_contexts_list)
        """
        token = _current_scheduler.set(self)
        try:
            return await self._run_graph(graph, state, context_id, parent_context, request_id)
        finally:
            _current_scheduler.reset(token)

    async def execute_subgraph(
        self,
        graph,
        state,
        context_id,
        parent_context,
        request_id: str,
    ) -> Tuple[Dict[str, Any], List]:
        """Execute a nested subgraph. Same scheduler, no new ContextVar set."""
        return await self._run_graph(graph, state, context_id, parent_context, request_id)

    async def _run_graph(
        self,
        graph,
        state,
        context_id,
        parent_context,
        request_id: str,
    ) -> Tuple[Dict[str, Any], List]:
        """Core graph execution loop."""
        output_queue = _output_queue.get()

        pending: deque[Task] = deque()
        active: Set[asyncio.Task] = set()
        # No concurrency limit — dispatch all ready tasks immediately
        yield_queue: asyncio.Queue[YieldEvent] = asyncio.Queue()
        stream_contexts: List[tuple] = []
        ready_counts: Dict[tuple, Dict[str, int]] = {}

        # Init ready counts for root context
        ready_counts[context_id] = dict(graph.initial_ready_count)

        # Seed entry ops
        for entry_name in graph.entries:
            pending.append(Task(entry_name, context_id, graph))

        LOGGER.debug(
            "[TASK_SCHED %s] start entries=%s ready=%s",
            graph.full_name, graph.entries, ready_counts[context_id],
        )

        # ── Helper: propagate completion ──
        def propagate(op_name: str, ctx: tuple) -> List[Task]:
            """Decrement downstream ready counts, return newly ready tasks."""
            newly_ready = []
            op_obj = graph._ops[op_name]

            if op_obj.type == "branch":
                target = op_obj.get_target(state, ctx)
                LOGGER.debug("[TASK_SCHED %s] branch %s → %s", graph.full_name, op_name, target)
                adj_list = [(target, False)]
            else:
                adj_list = graph._compiled_adj.get(op_name, [])

            rc = ready_counts.get(ctx)
            if rc is None:
                return newly_ready

            for next_op, is_soft in adj_list:
                if next_op not in rc:
                    continue

                if is_soft:
                    # Soft edge: first soft predecessor decrements by 1
                    # (all soft edges to same node count as 1 group in ready_count)
                    if rc[next_op] <= 0:
                        continue
                    rc[next_op] -= 1
                else:
                    rc[next_op] -= 1

                if rc[next_op] == 0:
                    newly_ready.append(Task(next_op, ctx, graph))

            return newly_ready

        # ── Helper: run one op as async task ──
        async def run_op(task: Task):
            """Execute one op, return (task, result_or_none)."""
            op_obj = graph._ops[task.op_name]

            if _is_gen(op_obj) or getattr(op_obj, '_has_streaming', False):
                # Generator op or streaming GraphOp: yield into yield_queue
                await _run_generator(task, op_obj, yield_queue, state, context_id, parent_context, request_id)
                return task, "exhausted"
            else:
                # Regular op (sync, async, non-streaming graph)
                try:
                    result = await op_obj.run(state, task.context_id, parent_context)
                except Exception:
                    LOGGER.error(
                        "[TASK_SCHED %s] error in %s ctx=%s: %s",
                        graph.full_name, task.op_name, task.context_id,
                        traceback.format_exc().rstrip(),
                    )
                    result = None
                return task, result

        # ── Main loop ──
        while (pending or active) and not self._aborted:

            # 1. Promote pending → active
            while pending and not self._cancelled and not self._aborted:
                task = pending.popleft()
                LOGGER.debug(
                    "[TASK_SCHED %s] dispatch %s ctx=%s active=%d",
                    graph.full_name, task.op_name, task.context_id, len(active) + 1,
                )
                active.add(asyncio.create_task(run_op(task)))
                # After each dispatch, check if yields arrived
                while not yield_queue.empty():
                    self._handle_yield(
                        yield_queue.get_nowait(), graph, state,
                        ready_counts, stream_contexts, pending, output_queue,
                    )

            if not active:
                break

            # 2. Wait for ANY task to complete
            # Also listen for generator yields via a waiter task
            yield_waiter = asyncio.create_task(yield_queue.get()) if any(True for _ in active) else None

            wait_set = active.copy()
            if yield_waiter:
                wait_set.add(yield_waiter)

            done, _ = await asyncio.wait(wait_set, return_when=asyncio.FIRST_COMPLETED)

            # 3. Process completed tasks
            for t in done:
                if t is yield_waiter:
                    # Generator yield event
                    try:
                        event = t.result()
                        self._handle_yield(
                            event, graph, state,
                            ready_counts, stream_contexts, pending, output_queue,
                        )
                    except (asyncio.CancelledError, Exception):
                        pass
                elif t in active:
                    # Op task completed
                    active.discard(t)
                    try:
                        task, result = t.result()
                        LOGGER.debug(
                            "[TASK_SCHED %s] done %s ctx=%s active=%d",
                            graph.full_name, task.op_name, task.context_id, len(active),
                        )
                        # Propagate: find ready downstream
                        # Generator ops propagate via yield events, not on exhaustion
                        if result is not PENDING and result != "exhausted":
                            newly_ready = propagate(task.op_name, task.context_id)
                            pending.extend(newly_ready)
                    except Exception:
                        LOGGER.error(
                            "[TASK_SCHED %s] task error: %s",
                            graph.full_name, traceback.format_exc().rstrip(),
                        )

            # Cancel yield_waiter if not done
            if yield_waiter and yield_waiter not in done:
                yield_waiter.cancel()
                try:
                    await yield_waiter
                except (asyncio.CancelledError, Exception):
                    pass

            # Drain any remaining yield events
            while not yield_queue.empty():
                self._handle_yield(
                    yield_queue.get_nowait(), graph, state,
                    ready_counts, stream_contexts, pending, output_queue,
                )

        # ── Collect outputs ──
        if stream_contexts:
            ctx_set = set(stream_contexts)
            prefixes = {ctx[:i] for ctx in ctx_set for i in range(1, len(ctx))}
            leaf_ctxs = [ctx for ctx in stream_contexts if ctx not in prefixes]

            # Filter: only include contexts where terminal op actually produced output.
            # N-to-M generators (e.g. VAD yields 1 segment from 50 chunks) create many
            # stream contexts but only a few reach the terminal op with real output.
            # Only check non-shared vars — shared vars are always set (at DEFAULT_CONTEXT)
            # so they'd make every context appear to have output.
            non_shared_outputs = [
                var for var in graph.outputs
                if state.schema.get_index(graph.full_name, var) not in state.schema._shared_indices
            ]

            def _has_output(ctx):
                check_vars = non_shared_outputs if non_shared_outputs else graph.outputs
                for var in check_vars:
                    try:
                        val = state[graph.full_name, var, ctx]
                        if val is not None:
                            return True
                    except (KeyError, IndexError):
                        pass
                return False

            terminal_ctxs = [ctx for ctx in leaf_ctxs if _has_output(ctx)]

            # Shared vars: read once from DEFAULT_CONTEXT (scalar)
            # Non-shared vars: collect per terminal context (list)
            from hush.core.states.cell import DEFAULT_CONTEXT
            outputs = {}
            for var in graph.outputs:
                idx = state.schema.get_index(graph.full_name, var)
                if idx >= 0 and idx in state.schema._shared_indices:
                    # Shared: scalar value from DEFAULT_CONTEXT
                    outputs[var] = state[graph.full_name, var, DEFAULT_CONTEXT]
                else:
                    # Non-shared: list per terminal context
                    outputs[var] = [state[graph.full_name, var, ctx] for ctx in terminal_ctxs]
        elif any(_is_gen(op) for op in graph._ops.values()):
            outputs = {var: [] for var in graph.outputs}
        else:
            outputs = graph.get_outputs(state, context_id=context_id, parent_context=parent_context)

        return outputs, stream_contexts

    def _handle_yield(
        self, event: YieldEvent, graph, state,
        ready_counts, stream_contexts, pending, output_queue,
    ):
        """Process a generator yield: create stream context, enqueue downstream."""
        stream_ctx = event.stream_ctx
        stream_contexts.append(stream_ctx)

        # Init ready counts for this stream context
        rc = dict(graph.initial_ready_count)
        for op_name, dec in graph._stream_predecrements.get(event.op_name, {}).items():
            rc[op_name] -= dec
        ready_counts[stream_ctx] = rc

        LOGGER.debug(
            "[TASK_SCHED %s] yield %s ctx=%s",
            graph.full_name, event.op_name, stream_ctx,
        )

        # Propagate: decrement ready counts and enqueue newly ready ops
        adj_list = graph._compiled_adj.get(event.op_name, [])
        for next_op, is_soft in adj_list:
            if next_op not in rc:
                continue
            if is_soft:
                if rc[next_op] <= 0:
                    continue
                rc[next_op] -= 1
            else:
                rc[next_op] -= 1
            if rc[next_op] == 0:
                pending.append(Task(next_op, stream_ctx, graph))

        # Streaming output event
        if output_queue is not None:
            try:
                output_queue.put_nowait({"type": "token", "op": event.op_name, "data": event.result})
            except Exception:
                pass


async def _run_generator(
    task: Task,
    op_obj,
    yield_queue: asyncio.Queue,
    state,
    root_context_id,
    parent_context,
    request_id: str,
):
    """Execute a generator op, putting yield events into yield_queue."""
    gen_start = datetime.now(timezone.utc)
    gen_perf = perf_counter()
    gen_error = None
    gen_inputs = {}

    try:
        idx = 0

        # GraphOp with streaming inner ops → use _run_streaming
        if getattr(op_obj, '_has_streaming', False) and hasattr(op_obj, '_run_streaming'):
            async for result in op_obj._run_streaming(state, task.context_id, parent_context, request_id):
                stream_ctx = task.context_id + (f"[{idx}]",)
                op_obj.store_result(state, result, stream_ctx)
                await yield_queue.put(YieldEvent(task.op_name, stream_ctx, result, task.graph))
                idx += 1
        else:
            # Normal @op generator
            gen_inputs = op_obj.get_inputs(state, task.context_id, parent_context)
            gen_fn = op_obj.core

            if inspect.isasyncgenfunction(gen_fn):
                async for result in gen_fn(**gen_inputs):
                    stream_ctx = task.context_id + (f"[{idx}]",)
                    op_obj.store_result(state, result, stream_ctx)
                    await yield_queue.put(YieldEvent(task.op_name, stream_ctx, result, task.graph))
                    idx += 1
            elif inspect.isgeneratorfunction(gen_fn):
                for result in gen_fn(**gen_inputs):
                    stream_ctx = task.context_id + (f"[{idx}]",)
                    op_obj.store_result(state, result, stream_ctx)
                    await yield_queue.put(YieldEvent(task.op_name, stream_ctx, result, task.graph))
                    idx += 1

    except Exception:
        gen_error = traceback.format_exc()
        LOGGER.error(
            "[TASK_SCHED] gen_error %s: %s",
            task.op_name, gen_error.rstrip(),
        )
    finally:
        try:
            ms = (perf_counter() - gen_perf) * 1000
            op_obj._log(request_id, task.context_id, gen_inputs, {}, ms)
            op_obj._store_metrics(
                state, task.context_id,
                start_time=gen_start,
                end_time=datetime.now(timezone.utc),
                duration_ms=ms,
            )
            if gen_error is not None:
                state[op_obj.full_name, "error", task.context_id] = gen_error
        except Exception:
            LOGGER.error(
                "[TASK_SCHED] gen_finally_error %s: %s",
                task.op_name, traceback.format_exc().rstrip(),
            )


# ── Backward compat wrapper ──

async def run_task_scheduler(graph, state, context_id, parent_context, request_id):
    """Drop-in replacement for old run_scheduler(). Same signature."""
    scheduler = _current_scheduler.get()
    if scheduler is None:
        scheduler = WorkflowScheduler()
    return await scheduler.execute_subgraph(graph, state, context_id, parent_context, request_id)