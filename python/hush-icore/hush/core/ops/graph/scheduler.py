"""Event-queue scheduler for GraphOp execution.

Entry point: run_scheduler(graph, state, ...) → (outputs, stream_contexts).
All runtime state is local — concurrent calls on the same graph are safe.

Concurrency model
=================

    graph.concurrency controls how many stream context pipelines can execute
    simultaneously. Uses a simple counter + pending queue:

    - When a generator yields, check if running_streams < concurrency
    - If yes: dispatch the stream context immediately, running_streams += 1
    - If no: queue the yield for later
    - When a stream context's terminal op reaches __end__: running_streams -= 1,
      dequeue and dispatch the next pending yield if any

    This ensures each article's full pipeline (classify → actions → save)
    runs end-to-end. No FIFO semaphore interleaving.
"""

import asyncio
import inspect
import traceback
from collections import deque
from datetime import datetime, timezone
from time import perf_counter
from typing import TYPE_CHECKING, Any, Dict, List, Tuple

from hush.core.loggings import LOGGER, format_event
from hush.core.ops import PENDING
from hush.core.ops.base import END
from hush.core.utils.context import _output_queue

if TYPE_CHECKING:
    from hush.core.states import MemoryState


def _is_gen(op_obj):
    """Check if op is a generator (sync or async)."""
    fn = getattr(op_obj, "core", None)
    return fn is not None and (inspect.isgeneratorfunction(fn) or inspect.isasyncgenfunction(fn))


async def run_scheduler(
    graph,
    state: "MemoryState",
    context_id,
    parent_context,
    request_id: str,
) -> Tuple[Dict[str, Any], List]:
    """Execute one scheduler pass over a graph."""

    # ── Local runtime state ────────────────────────────────────────
    event_queue = asyncio.Queue()
    active_count = 0
    ready_counts = {context_id: graph.initial_ready_count.copy()}
    soft_satisfied = {}
    stream_contexts = []
    output_queue = _output_queue.get()

    # Stream concurrency: counter + pending queue (replaces semaphore)
    running_streams = 0
    pending_yields = deque()  # queued (gen_name, stream_ctx, result_data)

    # Track which stream contexts are active (for release on terminal op)
    active_stream_ctxs = set()

    # ── Async tasks (spawned by dispatch_op) ─────────────────────

    async def task_execute(name, op_obj, ctx, p_ctx):
        """Async task for non-inline ops (async, graph, executor="thread")."""
        if op_obj.delay > 0:
            await asyncio.sleep(op_obj.delay)
        try:
            result = await op_obj.run(state, ctx, p_ctx)
        except Exception:
            await event_queue.put(("done", name, ctx))
            return
        if result is PENDING:
            await event_queue.put(("done_pending", name, ctx))
        else:
            await event_queue.put(("done", name, ctx))

    async def task_generator(name, op_obj, ctx, p_ctx):
        """Async task for generator ops (sync or async generators)."""
        gen_start = datetime.now(timezone.utc)
        gen_perf = perf_counter()
        gen_error = None
        gen_inputs = {}

        try:
            gen_inputs = op_obj.get_inputs(state, ctx, p_ctx)
            gen_fn = op_obj.core
            idx = 0

            if inspect.isasyncgenfunction(gen_fn):
                async for result in gen_fn(**gen_inputs):
                    stream_ctx = ctx + (f"[{idx}]",)
                    op_obj.store_result(state, result, stream_ctx)
                    await event_queue.put(("yield", name, stream_ctx, result))
                    idx += 1
            elif inspect.isgeneratorfunction(gen_fn):
                for result in gen_fn(**gen_inputs):
                    stream_ctx = ctx + (f"[{idx}]",)
                    op_obj.store_result(state, result, stream_ctx)
                    await event_queue.put(("yield", name, stream_ctx, result))
                    idx += 1

        except Exception:
            gen_error = traceback.format_exc()
            LOGGER.error(
                format_event(
                    "gen_error",
                    request_id=request_id or "unknown",
                    name=name,
                    error=gen_error.rstrip(),
                )
            )
        finally:
            ms = (perf_counter() - gen_perf) * 1000
            op_obj._log(request_id, ctx, gen_inputs, {}, ms)
            op_obj._store_metrics(
                state,
                ctx,
                start_time=gen_start,
                end_time=datetime.now(timezone.utc),
                duration_ms=ms,
            )
            if gen_error is not None:
                state[op_obj.full_name, "error", ctx] = gen_error

        await event_queue.put(("exhausted", name))

    # ── Scheduling logic ───────────────────────────────────────────

    def propagate(op_name, ctx):
        """Propagate completion: decrement successors' ready_counts, return newly ready ops."""
        op_obj = graph._ops[op_name]
        if op_obj.type == "branch":
            target = op_obj.get_target(state, ctx)
            LOGGER.debug(
                "[SCHED %s] branch %s -> target=%s, adj=%s",
                graph.full_name,
                op_name,
                target,
                graph._compiled_adj[op_name],
            )
            if target == END.name:
                return []
            successors = [(t, s) for t, s in graph._compiled_adj[op_name] if t == target]
            if not successors:
                available = sorted(graph.initial_ready_count.keys())
                raise KeyError(
                    f"Branch '{op_name}' routed to '{target}' which doesn't exist. "
                    f"Available: {available}"
                )
        else:
            successors = graph._compiled_adj[op_name]

        rc = ready_counts[ctx]
        ss = soft_satisfied.setdefault(ctx, set())
        newly_ready = []
        for next_op, is_soft in successors:
            if is_soft:
                if next_op in ss:
                    continue
                ss.add(next_op)
            rc[next_op] -= 1
            if rc[next_op] == 0:
                newly_ready.append(next_op)
        return newly_ready

    terminal_ops = set(graph.exits)

    def is_terminal(op_name):
        """Check if op is a terminal op (connects to END)."""
        return op_name in terminal_ops

    async def dispatch_op(name, ctx):
        """Dispatch one op to the appropriate execution mode."""
        nonlocal active_count
        op_obj = graph._ops[name]
        LOGGER.debug("[SCHED %s] dispatch %s (type=%s)", graph.full_name, name, op_obj.type)

        if _is_gen(op_obj):
            active_count += 1
            asyncio.create_task(task_generator(name, op_obj, ctx, parent_context))
            return []

        # Simple sync op → inline
        if (
            op_obj.type != "graph"
            and not inspect.iscoroutinefunction(getattr(op_obj, "core", None))
            and getattr(op_obj, "executor", None) is None
        ):
            if op_obj.delay > 0:
                await asyncio.sleep(op_obj.delay)
            result = await op_obj.run(state, ctx, parent_context)
            if result is PENDING:
                return []
            return propagate(name, ctx)

        active_count += 1
        LOGGER.debug("[SCHED %s] async task for %s, active=%d", graph.full_name, name, active_count)
        asyncio.create_task(task_execute(name, op_obj, ctx, parent_context))
        return []

    async def drain_ready(queue, ctx):
        """Process queue of ready ops: dispatch each, append newly ready successors, repeat."""
        while queue:
            queue.extend(await dispatch_op(queue.pop(0), ctx))

    def dispatch_yield(gen_name, stream_ctx, result_data):
        """Set up a stream context and dispatch its first ops. Returns True if dispatched."""
        nonlocal running_streams

        rc = graph.initial_ready_count.copy()
        for op_name, dec in graph._stream_predecrements.get(gen_name, {}).items():
            rc[op_name] -= dec
        ready_counts[stream_ctx] = rc
        stream_contexts.append(stream_ctx)
        active_stream_ctxs.add(stream_ctx)
        running_streams += 1

        return gen_name, stream_ctx, result_data

    def try_dequeue_yield():
        """If there's capacity and pending yields, dispatch one."""
        nonlocal running_streams
        if pending_yields and running_streams < graph.concurrency:
            gen_name, stream_ctx, result_data = pending_yields.popleft()
            return dispatch_yield(gen_name, stream_ctx, result_data)
        return None

    def release_stream(ctx):
        """Release a stream context slot, try to dequeue next."""
        nonlocal running_streams
        if ctx in active_stream_ctxs:
            active_stream_ctxs.discard(ctx)
            running_streams -= 1

    # ── The story ──────────────────────────────────────────────────

    # 1. Start entry ops
    LOGGER.debug(
        "[SCHED %s] entries=%s, initial_ready=%s",
        graph.full_name,
        graph.entries,
        dict(ready_counts[context_id]),
    )
    await drain_ready(list(graph.entries), context_id)

    # 2. Event loop
    while active_count > 0:
        LOGGER.debug("[SCHED %s] waiting event... active=%d", graph.full_name, active_count)
        event = await event_queue.get()

        if event[0] == "done":
            _, op_name, ctx = event
            active_count -= 1
            LOGGER.debug(
                "[SCHED %s] done: %s ctx=%s active=%d", graph.full_name, op_name, ctx, active_count
            )

            # Terminal op in stream context → release slot, dequeue next
            if ctx in active_stream_ctxs and is_terminal(op_name):
                release_stream(ctx)
                dispatched = try_dequeue_yield()
                if dispatched:
                    active_count -= 1  # balance the +1 from queueing
                    gen_name, s_ctx, r_data = dispatched
                    await drain_ready(propagate(gen_name, s_ctx), s_ctx)
                    if output_queue is not None:
                        await output_queue.put({"type": "token", "op": gen_name, "data": r_data})

            newly = propagate(op_name, ctx)
            LOGGER.debug(
                "[SCHED %s] propagate %s -> newly_ready=%s, ready_counts=%s",
                graph.full_name,
                op_name,
                newly,
                {k: v for k, v in ready_counts[ctx].items() if v > 0}
                if ctx in ready_counts
                else "?",
            )
            await drain_ready(newly, ctx)

        elif event[0] == "done_pending":
            _, op_name, ctx = event
            active_count -= 1
            if ctx in active_stream_ctxs and is_terminal(op_name):
                release_stream(ctx)
                dispatched = try_dequeue_yield()
                if dispatched:
                    active_count -= 1  # balance the +1 from queueing
                    gen_name, s_ctx, r_data = dispatched
                    await drain_ready(propagate(gen_name, s_ctx), s_ctx)
                    if output_queue is not None:
                        await output_queue.put({"type": "token", "op": gen_name, "data": r_data})

        elif event[0] == "yield":
            _, gen_name, stream_ctx, result_data = event

            if running_streams < graph.concurrency:
                # Slot available — dispatch immediately
                dispatch_yield(gen_name, stream_ctx, result_data)
                await drain_ready(propagate(gen_name, stream_ctx), stream_ctx)

                if output_queue is not None:
                    await output_queue.put({"type": "token", "op": gen_name, "data": result_data})
            else:
                # No slot — queue for later.
                # Increment active_count so event loop doesn't exit while items are pending.
                active_count += 1
                pending_yields.append((gen_name, stream_ctx, result_data))

        elif event[0] == "exhausted":
            active_count -= 1

    # 3. Collect outputs
    if stream_contexts:
        ctx_set = set(stream_contexts)
        prefixes = {ctx[:i] for ctx in ctx_set for i in range(1, len(ctx))}
        leaf_ctxs = [ctx for ctx in stream_contexts if ctx not in prefixes]
        outputs = {
            var: [state[graph.full_name, var, ctx] for ctx in leaf_ctxs] for var in graph.outputs
        }
    elif any(_is_gen(op) for op in graph._ops.values()):
        outputs = {var: [] for var in graph.outputs}
    else:
        outputs = graph.get_outputs(state, context_id=context_id, parent_context=parent_context)

    return outputs, stream_contexts
