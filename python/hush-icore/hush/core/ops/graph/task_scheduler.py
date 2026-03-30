"""Task-based workflow scheduler.

Single scheduler per workflow execution.
Uses asyncio.wait(FIRST_COMPLETED) for concurrent task dispatch.
Includes loop execution (LoopConfig + run_loop).
"""

import asyncio
import contextvars
import inspect
import logging
import traceback
from collections import deque
from dataclasses import dataclass, field
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


# ── Loop support (moved from _loop.py) ──────────────────────────────────────

@dataclass
class LoopConfig:
    """Configuration for GraphOp.loop() feedback loops."""
    until: Any  # str expression or callable
    max_iterations: int
    initial_state: dict
    _compiled_until: Any = field(default=None, repr=False)


def _evaluate_until(cfg: LoopConfig, outputs: dict) -> bool:
    """Evaluate the loop's until condition against current outputs."""
    if cfg is None or cfg.until is None:
        return False
    if callable(cfg.until):
        return bool(cfg.until(outputs))
    return bool(eval(cfg._compiled_until, {"__builtins__": {}}, outputs))  # noqa: S307


async def run_loop(graph, state, context_id, parent_context, request_id, outputs):
    """Run feedback loop iterations until condition met or max reached.

    Returns:
        Final outputs dict with _loop_metrics added.
    """
    cfg = graph._loop_config
    iteration = 0
    while True:
        if _evaluate_until(cfg, outputs):
            outputs["_loop_metrics"] = {
                "total_iterations": iteration + 1,
                "stopped_by_condition": True,
            }
            return outputs
        iteration += 1
        if iteration >= cfg.max_iterations:
            outputs["_loop_metrics"] = {
                "total_iterations": iteration,
                "stopped_by_condition": False,
                "max_iterations_reached": True,
            }
            return outputs
        next_ctx = context_id + (f"loop_{iteration}",)
        for var_name, value in outputs.items():
            state[graph.full_name, var_name, next_ctx] = value
        outputs, _ = await run_task_scheduler(graph, state, next_ctx, parent_context, request_id)


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
        yield_queue: asyncio.Queue[YieldEvent] = asyncio.Queue()
        stream_contexts: List[tuple] = []
        ready_counts: Dict[tuple, Dict[str, int]] = {}

        # Sequential streaming: queue per (source, downstream) pair
        sequential_queues: Dict[tuple, deque] = {}
        sequential_active: Dict[tuple, bool] = {}

        # Collect streaming: buffer per (source, downstream) pair
        collect_buffers: Dict[tuple, list] = {}

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
            """Execute one op. Returns (task, is_generator)."""
            op_obj = graph._ops[task.op_name]
            is_gen_op = _is_gen(op_obj) or getattr(op_obj, '_has_streaming', False)

            # Set yield callback for generators
            if is_gen_op:
                async def on_yield(name, stream_ctx, result):
                    await yield_queue.put(YieldEvent(name, stream_ctx, result, task.graph))
                op_obj._on_yield = on_yield

            await op_obj.run(state, task.context_id, parent_context)
            op_obj._on_yield = None  # cleanup

            return task, is_gen_op

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
                        sequential_queues, sequential_active, collect_buffers,
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

            # 3. Process completed tasks — yield events first, then op completions.
            # Ordering matters: if gen_task and yield_waiter complete in the same
            # batch, yield_waiter holds one item already removed from the queue.
            # Processing yield events first ensures collect_buffers are fully
            # populated before we check them on gen_task completion.
            if yield_waiter and yield_waiter in done:
                try:
                    event = yield_waiter.result()
                    self._handle_yield(
                        event, graph, state,
                        ready_counts, stream_contexts, pending, output_queue,
                        sequential_queues, sequential_active, collect_buffers,
                    )
                except (asyncio.CancelledError, Exception):
                    pass

            for t in done:
                if t is yield_waiter:
                    pass  # already handled above
                elif t in active:
                    # Op task completed
                    active.discard(t)
                    try:
                        task, is_gen_op = t.result()
                        LOGGER.debug(
                            "[TASK_SCHED %s] done %s ctx=%s gen=%s active=%d",
                            graph.full_name, task.op_name, task.context_id, is_gen_op, len(active),
                        )

                        if is_gen_op:
                            # Drain any yields queued before this completion was processed
                            while not yield_queue.empty():
                                self._handle_yield(
                                    yield_queue.get_nowait(), graph, state,
                                    ready_counts, stream_contexts, pending, output_queue,
                                    sequential_queues, sequential_active, collect_buffers,
                                )
                            # Generator exhausted — trigger .collect() dispatches
                            for (src, dst), buffer in list(collect_buffers.items()):
                                if src == task.op_name and buffer:
                                    # Collect all buffered results into list
                                    collected_values = {}
                                    for _, item_result in buffer:
                                        for k, v in item_result.items():
                                            collected_values.setdefault(k, []).append(v)
                                    # Dispatch downstream with collected list
                                    collect_ctx = task.context_id + ("__collect__",)
                                    stream_contexts.append(collect_ctx)
                                    rc = dict(graph.initial_ready_count)
                                    for op_n, dec in graph._stream_predecrements.get(task.op_name, {}).items():
                                        rc[op_n] -= dec
                                    ready_counts[collect_ctx] = rc
                                    # Store collected values in source op's namespace
                                    # (downstream reads from source["key"], not its own)
                                    src_op = graph._ops.get(src)
                                    if src_op:
                                        src_op.store_result(state, collected_values, collect_ctx)
                                    pending.append(Task(dst, collect_ctx, graph))
                                    del collect_buffers[(src, dst)]
                                    LOGGER.debug(
                                        "[TASK_SCHED %s] collect dispatch %s→%s items=%d",
                                        graph.full_name, src, dst, len(buffer),
                                    )
                        elif not is_gen_op:
                            # Normal op done — propagate downstream
                            newly_ready = propagate(task.op_name, task.context_id)
                            pending.extend(newly_ready)

                        # Sequential: dispatch next queued item for this op
                        for key in list(sequential_queues.keys()):
                            src, dst = key
                            if dst == task.op_name and sequential_queues[key]:
                                next_task = sequential_queues[key].popleft()
                                pending.append(next_task)
                                LOGGER.debug(
                                    "[TASK_SCHED %s] sequential next %s ctx=%s",
                                    graph.full_name, next_task.op_name, next_task.context_id,
                                )
                                break
                            elif dst == task.op_name:
                                sequential_active[key] = False

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
                    sequential_queues, sequential_active, collect_buffers,
                )

        # ── Collect outputs ──
        if stream_contexts:
            # Shared vars: read once from DEFAULT_CONTEXT (scalar).
            # Non-shared vars: collect all non-None values from stream contexts (list).
            from hush.core.states.cell import DEFAULT_CONTEXT
            outputs = {}
            for var in graph.outputs:
                idx = state.schema.get_index(graph.full_name, var)
                if idx >= 0 and idx in state.schema._shared_indices:
                    outputs[var] = state[graph.full_name, var, DEFAULT_CONTEXT]
                else:
                    # Separate __collect__ contexts (single-shot) from per-item contexts.
                    # __collect__ → downstream ran once with full list → scalar output.
                    # Regular stream contexts → downstream ran per-item → list output.
                    collect_val = None
                    per_item_vals = []
                    for ctx in stream_contexts:
                        try:
                            val = state[graph.full_name, var, ctx]
                            if val is not None:
                                if ctx and ctx[-1] == "__collect__":
                                    collect_val = val
                                else:
                                    per_item_vals.append(val)
                        except (KeyError, IndexError):
                            pass
                    if collect_val is not None:
                        outputs[var] = collect_val
                    else:
                        outputs[var] = per_item_vals
        elif any(_is_gen(op) for op in graph._ops.values()):
            outputs = {var: [] for var in graph.outputs}
        else:
            outputs = graph.get_outputs(state, context_id=context_id, parent_context=parent_context)

        return outputs, stream_contexts

    @staticmethod
    def _get_stream_mode(graph, source_op_name, downstream_op_name):
        """Get streaming mode for a connection: check Ref modifiers on downstream input.

        Returns: (is_parallel, parallel_max, is_collect)
        Default: (False, None, False) = sequential, per-item
        """
        from hush.core.states.ref import Ref
        downstream_op = graph._ops.get(downstream_op_name)
        if not downstream_op:
            return False, None, False

        source_op = graph._ops.get(source_op_name)
        source_full_name = source_op.full_name if source_op else source_op_name

        for _, param in downstream_op.inputs.items():
            if isinstance(param.value, Ref):
                ref = param.value
                src = ref.source if isinstance(ref.source, str) else getattr(ref.source, "full_name", getattr(ref.source, "name", None))
                if src == source_op_name or src == source_full_name:
                    return ref._stream_parallel, ref._stream_parallel_max, ref._stream_collect

        return False, None, False

    def _handle_yield(
        self, event: YieldEvent, graph, state,
        ready_counts, stream_contexts, pending, output_queue,
        sequential_queues, sequential_active, collect_buffers,
    ):
        """Process a generator yield: create stream context, enqueue downstream.

        Respects Ref streaming modes:
        - Default (sequential): queue, dispatch one at a time
        - .parallel(): dispatch immediately
        - .collect(): buffer, dispatch all when source exhausted
        """
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

        # Propagate: decrement ready counts and find newly ready ops
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
                is_parallel, parallel_max, is_collect = self._get_stream_mode(
                    graph, event.op_name, next_op
                )

                if is_collect:
                    # Buffer for collection — dispatch when source exhausted
                    key = (event.op_name, next_op)
                    if key not in collect_buffers:
                        collect_buffers[key] = []
                    collect_buffers[key].append((stream_ctx, event.result))
                    LOGGER.debug(
                        "[TASK_SCHED %s] collect buffer %s→%s count=%d",
                        graph.full_name, event.op_name, next_op, len(collect_buffers[key]),
                    )

                elif is_parallel:
                    # Parallel: dispatch immediately
                    pending.append(Task(next_op, stream_ctx, graph))

                else:
                    # Sequential (default): queue, dispatch one at a time
                    key = (event.op_name, next_op)
                    if key not in sequential_queues:
                        sequential_queues[key] = deque()
                    sequential_queues[key].append(Task(next_op, stream_ctx, graph))

                    if key not in sequential_active or not sequential_active[key]:
                        # No active task for this sequential chain — dispatch first
                        sequential_active[key] = True
                        pending.append(sequential_queues[key].popleft())

        # Streaming output event
        if output_queue is not None:
            try:
                output_queue.put_nowait({"type": "token", "op": event.op_name, "data": event.result})
            except Exception:
                pass


# ── Backward compat wrapper ──

async def run_task_scheduler(graph, state, context_id, parent_context, request_id):
    """Drop-in replacement for old run_scheduler(). Same signature."""
    scheduler = _current_scheduler.get()
    if scheduler is None:
        scheduler = WorkflowScheduler()
    return await scheduler.execute_subgraph(graph, state, context_id, parent_context, request_id)