"""Event-queue scheduler for GraphOp execution.

Entry point: run_scheduler(graph, state, ...) → (outputs, stream_contexts).
All runtime state is local — concurrent calls on the same graph are safe.

Execution flow
==============

All functions are closures inside run_scheduler, sharing local state
(event_queue, active_count, ready_counts, soft_satisfied, stream_contexts).

    GraphOp.run()
        |
        v
    run_scheduler(graph, state, context_id, parent_context, request_id)
        |
        |  1. Start entry ops
        |
        |     drain_ready(entries, ctx)
        |         |
        |         └─ dispatch_op(name, ctx)
        |              ├─ Generator    → create_task(task_generator), active_count += 1
        |              ├─ Sync+simple  → run inline, PENDING? stop : propagate()
        |              └─ Async/graph  → create_task(task_execute), active_count += 1
        |
        |  2. Event loop (while active_count > 0)
        |     wait for event from event_queue, then:
        |
        |     "done"         → active_count -= 1
        |                      propagate(op_name, ctx) → drain_ready(newly_ready)
        |
        |     "done_pending" → active_count -= 1
        |
        |     "yield"        → create stream_ctx with pre-decremented ready_counts
        |                      propagate(gen_name, stream_ctx) → drain_ready(newly_ready)
        |                      push to output_queue (if present)
        |
        |     "exhausted"    → active_count -= 1
        |
        |  3. Collect outputs
        |     stream_contexts? → gather from leaf contexts (deepest [n])
        |     generators?     → return empty lists
        |     batch only?     → graph.get_outputs()
        |
        v
    return (outputs, stream_contexts)


Functions
=========

    task_execute(name, op_obj, ctx, p_ctx)
        Async task for non-inline ops (async, graph, executor="thread").
        Acquires semaphore for stream contexts (backpressure).
        Calls op_obj.run(), emits "done" or "done_pending".

    task_generator(name, op_obj, ctx, p_ctx)
        Async task for sync/async generator ops.
        Iterates the generator, emitting "yield" per item with stream_ctx.
        Each yield stores result in state. On error: partial yields kept.
        Logs metrics. Emits "exhausted" when done.

    propagate(op_name, ctx) → list[str]
        Decrement ready_counts for each successor of op_name.
        Branch ops: resolve target at runtime, only activate that branch.
        Soft edges: all soft edges to same target count as 1 (first wins).
        Returns op names whose ready_count just hit 0.

    dispatch_op(name, ctx) → list[str]
        Dispatch one op based on its type (see flow diagram above).
        Only the inline sync path returns newly ready ops.
        Async paths post events to event_queue instead.

    drain_ready(queue, ctx)
        Process queue of ready ops: dispatch each, append newly ready
        successors, repeat until empty.


Key concepts
============

    ready_count    Each op starts with count = number of predecessors.
                   When a predecessor completes, count -= 1. At 0 → ready.

    soft edges     Branch outputs: only one branch fires. All soft edges
                   to the same target count as 1 in ready_count.

    stream context Generator yields create context tuples like ("main", "[0]").
                   Each yield gets its own ready_counts copy, so downstream
                   ops run independently per item.

    predecrements  When a generator yields, batch predecessors of downstream
                   ops have already completed. Their contributions are
                   pre-subtracted from the fresh ready_counts copy.

    PENDING        An op can return PENDING to signal "I absorbed this input
                   but produced no output" — downstream ops are NOT triggered.

    semaphore      Limits concurrent stream tasks (default 64). Only stream
                   contexts acquire it — batch ops run without limit.
"""

import asyncio
import inspect
import traceback
from datetime import datetime
from time import perf_counter
from typing import TYPE_CHECKING, Any, Dict, List, Tuple

from hush.core.loggings import LOGGER
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
    """Execute one scheduler pass over a graph.

    All runtime state is local — safe for concurrent calls on the same graph.

    Returns:
        (outputs_dict, stream_contexts_list)
    """

    # ── Local runtime state ────────────────────────────────────────
    event_queue = asyncio.Queue()
    active_count = 0
    ready_counts = {context_id: graph.initial_ready_count.copy()}
    soft_satisfied = {}
    stream_contexts = []
    output_queue = _output_queue.get()
    semaphore = asyncio.Semaphore(graph._max_stream_concurrent)

    # ── Async tasks (spawned by dispatch_op) ─────────────────────

    async def task_execute(name, op_obj, ctx, p_ctx):
        """Async task for non-inline ops (async, graph, executor="thread").

        Acquires semaphore for stream contexts (backpressure control).
        Emits "done" or "done_pending" to event_queue when finished.
        """
        is_stream = ctx != context_id
        if is_stream:
            await semaphore.acquire()
        try:
            result = await op_obj.run(state, ctx, p_ctx)
        finally:
            if is_stream:
                semaphore.release()
        if result is PENDING:
            await event_queue.put(("done_pending", name, ctx))
        else:
            await event_queue.put(("done", name, ctx))

    async def task_generator(name, op_obj, ctx, p_ctx):
        """Async task for generator ops (sync or async generators).

        Iterates the generator, emitting ("yield", name, stream_ctx, result)
        per item. Each yield stores the result in state and creates a new
        stream context ctx + ("[n]",). Emits ("exhausted", name) when done.
        Handles errors gracefully — partial yields are kept, metrics logged.
        """
        gen_start = datetime.now()
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
            LOGGER.error("[%s] Error in generator op %s:\n%s", request_id, name, gen_error.rstrip())
        finally:
            ms = (perf_counter() - gen_perf) * 1000
            op_obj._log(request_id, ctx, gen_inputs, {}, ms)
            op_obj._store_metrics(
                state, ctx, error=gen_error,
                start_time=gen_start, end_time=datetime.now(), duration_ms=ms,
            )

        await event_queue.put(("exhausted", name))

    # ── Scheduling logic ───────────────────────────────────────────

    def propagate(op_name, ctx):
        """Propagate completion: decrement successors' ready_counts, return newly ready ops.

        Branch ops: resolve target at runtime via get_target(), only
        activate that one branch (or return [] if target is END).
        Soft edges: first soft edge to a target sets it as satisfied,
        subsequent soft edges to the same target are skipped.
        Returns list of op names whose ready_count just hit 0.
        """
        op_obj = graph._ops[op_name]
        if op_obj.type == "branch":
            target = op_obj.get_target(state, ctx)
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

    async def dispatch_op(name, ctx):
        """Dispatch one op to the appropriate execution mode.

        Generator  → create_task(task_generator), active_count += 1, return []
        Sync+simple (not graph, not async, no executor)
                   → run inline, return propagate() or [] if PENDING
        Async/graph → create_task(task_execute), active_count += 1, return []

        Returns list of newly ready op names (only non-empty for inline path).
        """
        nonlocal active_count
        op_obj = graph._ops[name]

        if _is_gen(op_obj):
            active_count += 1
            asyncio.create_task(task_generator(name, op_obj, ctx, parent_context))
            return []

        # Simple sync op (not graph, not async, no executor) → inline
        if (
            op_obj.type != "graph"
            and not inspect.iscoroutinefunction(getattr(op_obj, "core", None))
            and getattr(op_obj, "executor", None) is None
        ):
            result = await op_obj.run(state, ctx, parent_context)
            if result is PENDING:
                return []
            return propagate(name, ctx)

        active_count += 1
        asyncio.create_task(task_execute(name, op_obj, ctx, parent_context))
        return []

    async def drain_ready(queue, ctx):
        """Process queue of ready ops: dispatch each, append newly ready successors, repeat.

        Used after initial entries and after propagate().
        Only the inline sync path can return successors — async tasks
        post events to event_queue instead, handled by the main loop.
        """
        while queue:
            queue.extend(await dispatch_op(queue.pop(0), ctx))

    # ── The story ──────────────────────────────────────────────────

    # 1. Start entry ops
    await drain_ready(list(graph.entries), context_id)

    # 2. Event loop
    while active_count > 0:
        event = await event_queue.get()

        if event[0] == "done":
            _, op_name, ctx = event
            active_count -= 1
            await drain_ready(propagate(op_name, ctx), ctx)

        elif event[0] == "done_pending":
            active_count -= 1

        elif event[0] == "yield":
            _, gen_name, stream_ctx, result_data = event

            # Create stream context with pre-decremented ready counts
            rc = graph.initial_ready_count.copy()
            for op_name, dec in graph._stream_predecrements.get(gen_name, {}).items():
                rc[op_name] -= dec
            ready_counts[stream_ctx] = rc
            stream_contexts.append(stream_ctx)

            await drain_ready(propagate(gen_name, stream_ctx), stream_ctx)

            if output_queue is not None:
                await output_queue.put({"type": "token", "op": gen_name, "data": result_data})

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
