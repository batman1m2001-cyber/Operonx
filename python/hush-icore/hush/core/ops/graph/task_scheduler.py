"""Task-based workflow scheduler (rewrite).

Single scheduler per workflow execution.
Event-driven: Frame/EOF events drive op dispatch.
"""

import asyncio
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from time import perf_counter
from typing import Any, Dict, List, Tuple

from hush.core.loggings import LOGGER
from hush.core.states.ref import Ref


@dataclass
class Frame:
    """Generator yielded one result."""

    op: str
    ctx: tuple
    result: dict


@dataclass
class EOF:
    """Marker for generator completion."""

    op: str
    ctx: tuple


@dataclass
class LoopConfig:
    """Configuration for GraphOp.loop() feedback loops."""

    until: Any  # str expression or Callable
    max_iterations: int = 1000

    def __post_init__(self):
        self._compiled_until = None
        if isinstance(self.until, str):
            self._compiled_until = compile(self.until, "<until>", "eval")


def _evaluate_until(cfg: LoopConfig, outputs: dict) -> bool:
    """Evaluate loop stop condition against current outputs."""
    if cfg is None or cfg.until is None:
        return False
    if callable(cfg.until):
        return bool(cfg.until(outputs))
    return bool(eval(cfg._compiled_until, {"__builtins__": {}}, outputs))


class Scheduler:
    """Created once at graph.build(). Shared across all executions.
    All per-execution mutable state lives as locals inside run().

    Event flow::

        dispatch(op, ctx)
            └─ _pump(op, ctx)       async task
                ├─ op.run() yields  → Frame(op, ctx, result) → queue
                └─ op.run() done    → EOF(op, ctx)           → queue

        run() loop:
            dequeue event
            ├─ Frame → _on_frame()  update ready counts, route downstream
            └─ EOF   → _on_eof()    flush collect, advance seq queue, check loop
    """

    __slots__ = ("graph",)

    def __init__(self, graph):
        self.graph = graph  # static compiled data — never mutated after build()

    async def run(
        self,
        state,
        context_id: tuple,
        output_queue: asyncio.Queue = None,
    ) -> Tuple[dict, List[tuple]]:
        """Drive graph execution, including loop re-dispatch if configured.

        Parameters
        ----------
        state:        MemoryState — shared across all ops.
        context_id:   Root context tuple for this execution, e.g. ``("main",)``.
        output_queue: If provided, stream frames to ExecutionHandle and send
                      ``None`` sentinel on completion.

        Returns
        -------
        (outputs, item_ctxs)
            outputs     - final batch outputs dict (empty when streaming).
            item_ctxs   - list of per-item context tuples produced by generators.
        """
        g = self.graph
        outputs, item_ctxs = await self._run_once(state, context_id, output_queue)

        # Top-level loop re-dispatch (GraphOp.loop() sets _loop_config on g).
        if g._loop_config:
            n_iters = 0
            current_ctx = context_id
            # The initial _run_once above counts as iteration 1,
            # so we only re-dispatch up to max_iterations - 1 more times.
            while (
                not _evaluate_until(g._loop_config, outputs)
                and n_iters < g._loop_config.max_iterations - 1
            ):
                next_ctx = (
                    current_ctx + ("loop_1",)
                    if n_iters == 0
                    else current_ctx[:-1] + (f"loop_{n_iters + 1}",)
                )
                n_iters += 1
                for var, val in outputs.items():
                    state[g.full_name, var, next_ctx] = val
                current_ctx = next_ctx
                outputs, _ = await self._run_once(state, current_ctx)
            # Push final outputs so handle.collect() gets the latest values.
            if output_queue is not None and n_iters > 0:
                output_queue.put_nowait((g.name, current_ctx, outputs))

        # Signal completion to ExecutionHandle.
        if output_queue is not None:
            output_queue.put_nowait(None)

        return outputs, item_ctxs

    async def _run_once(
        self,
        state,
        context_id: tuple,
        output_queue: asyncio.Queue = None,
    ) -> Tuple[dict, List[tuple]]:
        """Execute the graph exactly once (no loop re-dispatch).

        Returns
        -------
        (outputs, item_ctxs)
        """
        g = self.graph
        _start_time = datetime.now(timezone.utc)
        _perf_start = perf_counter()

        # All Frame/EOF events flow through here — the main event loop dequeues them.
        queue: asyncio.Queue = asyncio.Queue()

        # Number of live tasks + unconsumed events in queue.
        # dispatch() increments before spawning a task; _pump() decrements in finally.
        # Each Frame/EOF put on queue also increments; _on_frame/_on_eof decrement after processing.
        # When inflight == 0, the graph is fully done.
        inflight: int = 0

        # ready[ctx][op_name] = number of hard-edge predecessors still outstanding.
        # When it reaches 0, the op is dispatched.
        # Root context seeded from _initial_ready; item contexts seeded in _route().
        ready: Dict[tuple, Dict[str, int]] = {context_id: dict(g._initial_ready)}

        # seq_queues[gen_ctx][dst_op] = deque of item_ctx values waiting to run.
        # Sequential mode (default): only one item runs through dst_op at a time.
        # New items queue here and dispatch one-by-one as each finishes.
        seq_queues: Dict[tuple, Dict[str, deque]] = {}

        # seq_active[gen_ctx][dst_op] = True while an item is in flight for dst_op.
        # Guards against double-dispatch: next item only starts after current EOF.
        seq_active: Dict[tuple, Dict[str, bool]] = {}

        # seq_origins[(op_name, item_ctx)] = (src, dst) key.
        # When dst_op finishes at item_ctx (EOF arrives), we use this to find
        # which seq_queue to advance. Keyed by (op_name, item_ctx) so two
        # downstream ops from the same generator don't overwrite each other.
        seq_origins: Dict[Tuple[str, tuple], tuple] = {}

        # collect_bufs[gen_ctx][dst_op] = list of (item_ctx, result) pairs.
        # When downstream op has collect=True, frames buffer here instead of
        # dispatching immediately. Flushed all at once when generator sends EOF.
        collect_bufs: Dict[tuple, Dict[str, List]] = {}

        # Ordered list of item contexts produced by generators.
        # e.g. [("main","[0]"), ("main","[1]"), ...]
        # Returned to GraphOp.run() so it can yield per-item outputs to the caller.
        item_ctxs: List[tuple] = []

        # loop_iters[ctx] = iteration number for that context.
        # iter 0 runs at context_id, iter 1 at context_id+("loop_1",), etc.
        # Defaults to 0 via .get() — no pre-seeding needed.
        loop_iters: Dict[tuple, int] = {}

        def dispatch(op_name: str, ctx: tuple) -> None:
            """Schedule op to run as soon as possible, with given context."""
            nonlocal inflight
            inflight += 1
            asyncio.create_task(_pump(op_name, ctx))

        async def _pump(op_name: str, ctx: tuple) -> None:
            """Run op and put Frame/EOF events on queue."""
            nonlocal inflight
            op = g._ops[op_name]
            try:
                async for item_ctx, result in op.run(state, ctx):
                    queue.put_nowait(Frame(op_name, item_ctx, result))
                    inflight += 1
                queue.put_nowait(EOF(op_name, ctx))
                inflight += 1
            except Exception as e:
                state[op_name, "error", ctx] = str(e)
                queue.put_nowait(EOF(op_name, ctx))
                inflight += 1  # account for the EOF we just put on queue
            finally:
                inflight -= 1

        def _on_frame(event: Frame) -> None:
            """Handle one Frame: seed item ctx, decrement ready, route when ready."""
            # Seed ready counts for a new item context (first frame from a generator).
            if event.ctx not in ready:
                item_ctxs.append(event.ctx)
                ri = g._stream_initial_ready.get(event.op, g._initial_ready)
                ready[event.ctx] = dict(ri)

            # Forward PARENT-bound vars to output_queue (root graph only).
            if output_queue is not None:
                out_vars = g._out_vars.get(event.op)
                if out_vars:
                    filtered = {out_vars[k]: v for k, v in event.result.items() if k in out_vars}
                    if filtered:
                        output_queue.put_nowait((event.op, event.ctx, filtered))

            # Check for branch target — only route to the selected branch.
            branch_target = event.result.get("__branch_target__")

            # Propagate through adjacency list.
            for edge in g._adj.get(event.op, []):
                if branch_target and edge.dst != branch_target:
                    continue
                rc = ready[event.ctx]
                if edge.dst not in rc:
                    continue
                if edge.soft and rc[edge.dst] <= 0:
                    continue
                # All predecessors satisfied — dispatch downstream op.
                rc[edge.dst] -= 1
                if rc[edge.dst] == 0:
                    _route(event.op, edge.dst, event.ctx, event.result)

        def _route(src: str, dst: str, ctx: tuple, result: dict) -> None:
            """Dispatch dst using the correct stream policy (seq/parallel/collect)."""
            dst_op = g._ops[dst]

            # Resolve per-var stream policy from schema (O(1)).
            # Find which input var of dst references src, then look up its policy.
            policy = None
            for var_name, param in dst_op.inputs.items():
                ref = getattr(param, "value", None)
                if isinstance(ref, Ref) and ref.raw_source.name == src:
                    policy = state.schema._stream_policies.get((dst_op.full_name, var_name))
                    break

            if policy and policy.collect:
                # Buffer — flush all at once when generator EOF arrives.
                collect_bufs.setdefault((src, dst), []).append((ctx, result))

            elif policy and policy.parallel:
                # All items run concurrently — dispatch immediately.
                dispatch(dst, ctx)

            else:
                # Sequential (default) — one item at a time through dst.
                key = (src, dst)
                if not seq_active.get(key):
                    seq_active[key] = True
                    seq_origins[(dst, ctx)] = key
                    dispatch(dst, ctx)
                else:
                    seq_queues.setdefault(key, deque()).append(ctx)

        def _on_eof(event: EOF) -> None:
            """Handle op completion: flush collect, advance seq queue, check loop."""
            # 1. Flush collect buffers sourced from this op.
            for key in list(collect_bufs):
                src, dst = key
                if src != event.op:
                    continue
                buf = collect_bufs.pop(key)
                merged = {}
                for _, r in buf:
                    for k, v in r.items():
                        merged.setdefault(k, []).append(v)
                collect_ctx = event.ctx + ("__collect__",)
                item_ctxs.append(collect_ctx)
                g._ops[src].store_result(state, merged, collect_ctx)
                dispatch(dst, collect_ctx)

            # 2. Advance sequential queue — unblock next waiting item.
            key = seq_origins.pop((event.op, event.ctx), None)
            if key:
                src, dst = key
                q = seq_queues.get(key)
                if q:
                    next_ctx = q.popleft()
                    seq_origins[(dst, next_ctx)] = key
                    dispatch(dst, next_ctx)
                else:
                    seq_active[key] = False

            # 3. Loop check — only for GraphOp with _loop_config.
            op = g._ops.get(event.op)
            if op and hasattr(op, "_loop_config") and op._loop_config:
                outputs = op.get_outputs(state, event.ctx)
                cfg = op._loop_config
                n = loop_iters.get(event.ctx, 0)
                if not _evaluate_until(cfg, outputs) and n < cfg.max_iterations - 1:
                    loop_iters[event.ctx] = n + 1
                    next_ctx = (
                        event.ctx + ("loop_1",) if n == 0 else event.ctx[:-1] + (f"loop_{n + 1}",)
                    )
                    for var, val in outputs.items():
                        state[op.full_name, var, next_ctx] = val
                    dispatch(event.op, next_ctx)

        # Seed entry ops.
        for entry in g.entries:
            dispatch(entry, context_id)

        # Main event loop - runs until all tasks and queue events are consumed.
        while inflight > 0:
            event = await queue.get()
            inflight -= 1
            if isinstance(event, Frame):
                _on_frame(event)
            else:
                _on_eof(event)

        # Store graph-level metrics so TraceCollector can find this graph node.
        _end_time = datetime.now(timezone.utc)
        g._store_metrics(
            state,
            context_id,
            start_time=_start_time,
            end_time=_end_time,
            duration_ms=(perf_counter() - _perf_start) * 1000,
        )

        # Collect final outputs at root context.
        outputs = g.get_outputs(state, context_id)

        return outputs, item_ctxs
