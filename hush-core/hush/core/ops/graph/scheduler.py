"""Event-queue scheduler for GraphOp execution."""

import asyncio
import inspect
import traceback
from datetime import datetime
from time import perf_counter
from typing import TYPE_CHECKING, Any, Dict, List, Tuple

from hush.core.loggings import LOGGER
from hush.core.ops import PENDING
from hush.core.ops.base import END, BaseOp
from hush.core.utils.context import _output_queue

if TYPE_CHECKING:
    from hush.core.states import MemoryState


def _is_gen(op_obj):
    """Check if op is a generator (sync or async). O(1) bitwise check."""
    fn = getattr(op_obj, "core", None)
    return fn is not None and (inspect.isgeneratorfunction(fn) or inspect.isasyncgenfunction(fn))


class Scheduler:
    """Async event-driven scheduler for graph execution.

    Handles both batch ops (run once) and generators (yield multiple
    outputs, each creating a new context [n]).

    Ready-count tracking: each op starts with a count equal to its
    number of predecessors. When a predecessor completes, the count
    decrements. When it reaches 0, the op is ready to run.

    Ops returning PENDING absorb input without triggering downstream.

    Init with graph topology only. Call run() with runtime params.
    """

    __slots__ = [
        "graph",
        "semaphore",
        # Runtime state (reset per run)
        "state",
        "context_id",
        "parent_context",
        "request_id",
        "event_queue",
        "active_count",
        "ready_counts",
        "soft_satisfied",
        "stream_contexts",
        "output_queue",
    ]

    def __init__(self, graph):
        self.graph = graph
        self.semaphore = asyncio.Semaphore(graph._max_stream_concurrent)

    def _reset(self, state: "MemoryState", context_id, parent_context, request_id: str):
        """Reset runtime state for a new run."""
        self.state = state
        self.context_id = context_id
        self.parent_context = parent_context
        self.request_id = request_id
        self.event_queue = asyncio.Queue()
        self.active_count = 0
        self.ready_counts = {context_id: self.graph.initial_ready_count.copy()}
        self.soft_satisfied = {}
        self.stream_contexts = []
        self.output_queue = _output_queue.get()

    def _can_inline(self, op_obj: BaseOp) -> bool:
        """Check if op can be inlined (run synchronously without a task)."""
        from hush.core.ops.graph.graph_op import GraphOp

        return (
            not isinstance(op_obj, GraphOp)
            and not _is_gen(op_obj)
            and not inspect.iscoroutinefunction(getattr(op_obj, "core", None))
            and getattr(op_obj, "executor", None) is None
        )

    def _get_successors(self, op_name: str, ctx) -> list:
        """Get successor ops, resolving branch targets at runtime."""
        g = self.graph
        current_op = g._ops[op_name]
        if current_op.type == "branch":
            branch_target = current_op.get_target(self.state, ctx)
            if branch_target == END.name:
                return []
            for tgt, soft in g._compiled_adj[op_name]:
                if tgt == branch_target:
                    return [(tgt, soft)]
            available_ops = sorted(g.initial_ready_count.keys())
            raise KeyError(
                f"\n"
                f"Op '{branch_target}' not found in graph '{g.name}'.\n"
                f"\n"
                f"  Source: Branch op '{op_name}' routed to '{branch_target}'\n"
                f"  Available ops: {available_ops}\n"
                f"\n"
                f'  This usually means the target string in if_(..., "{branch_target}") '
                f"doesn't match any op's actual name.\n"
                f"  Check that your branch targets match the 'name' parameter "
                f"of the target ops."
            )
        return g._compiled_adj[op_name]

    def _activate_successors(self, op_name: str, ctx) -> list:
        """Decrement ready counts for successors, return newly ready ops."""
        rc = self.ready_counts[ctx]
        ss = self.soft_satisfied.setdefault(ctx, set())
        newly_ready = []
        for next_op, is_soft in self._get_successors(op_name, ctx):
            if is_soft:
                if next_op in ss:
                    continue
                ss.add(next_op)
            rc[next_op] -= 1
            if rc[next_op] == 0:
                newly_ready.append(next_op)
        return newly_ready

    def _create_stream_context(self, stream_ctx, gen_name):
        """Create a new streaming context with pre-decremented ready counts."""
        rc = self.graph.initial_ready_count.copy()
        predecrements = self.graph._stream_predecrements.get(gen_name, {})
        for op_name, decrement in predecrements.items():
            rc[op_name] -= decrement
        self.ready_counts[stream_ctx] = rc
        self.stream_contexts.append(stream_ctx)

    async def _run_op(self, name, op_obj, ctx, p_ctx):
        """Run a batch or streaming downstream op, emit done event.

        If op returns PENDING, emits done_pending so the scheduler
        skips successor activation.
        """
        is_stream = ctx != self.context_id
        if is_stream:
            await self.semaphore.acquire()
        try:
            result = await op_obj.run(self.state, ctx, p_ctx)
        finally:
            if is_stream:
                self.semaphore.release()
        if result is PENDING:
            await self.event_queue.put(("done_pending", name, ctx))
        else:
            await self.event_queue.put(("done", name, ctx))

    async def _drive_generator(self, name, op_obj, ctx, p_ctx):
        """Drive a generator op, emitting yield/exhausted events."""
        gen_start_time = datetime.now()
        gen_perf_start = perf_counter()
        gen_error_msg = None
        gen_inputs = {}

        try:
            gen_inputs = op_obj.get_inputs(self.state, ctx, p_ctx)
            gen_fn = op_obj.core
            yield_idx = 0

            if inspect.isasyncgenfunction(gen_fn):
                async for result in gen_fn(**gen_inputs):
                    stream_ctx = ctx + (f"[{yield_idx}]",)
                    op_obj.store_result(self.state, result, stream_ctx)
                    await self.event_queue.put(("yield", name, stream_ctx, result))
                    yield_idx += 1
            elif inspect.isgeneratorfunction(gen_fn):
                for result in gen_fn(**gen_inputs):
                    stream_ctx = ctx + (f"[{yield_idx}]",)
                    op_obj.store_result(self.state, result, stream_ctx)
                    await self.event_queue.put(("yield", name, stream_ctx, result))
                    yield_idx += 1

        except Exception:
            gen_error_msg = traceback.format_exc()
            LOGGER.error(
                "[%s] Error in generator op %s:\n%s",
                self.request_id,
                name,
                gen_error_msg.rstrip(),
            )
        finally:
            gen_duration_ms = (perf_counter() - gen_perf_start) * 1000
            op_obj._log(self.request_id, ctx, gen_inputs, {}, gen_duration_ms)
            op_obj._store_metrics(
                self.state,
                ctx,
                error=gen_error_msg,
                start_time=gen_start_time,
                end_time=datetime.now(),
                duration_ms=gen_duration_ms,
            )

        await self.event_queue.put(("exhausted", name))

    async def _schedule_op(self, name, ctx, p_ctx):
        """Schedule an op for execution. Returns newly ready successors (for inline ops)."""
        op_obj = self.graph._ops[name]
        if _is_gen(op_obj):
            self.active_count += 1
            asyncio.create_task(self._drive_generator(name, op_obj, ctx, p_ctx))
        elif self._can_inline(op_obj):
            result = await op_obj.run(self.state, ctx, p_ctx)
            if result is PENDING:
                return []  # Absorbed input, no downstream
            return self._activate_successors(name, ctx)
        else:
            self.active_count += 1
            asyncio.create_task(self._run_op(name, op_obj, ctx, p_ctx))
        return []

    def _collect_outputs(self) -> Dict[str, Any]:
        """Collect final outputs from state after all ops complete.

        For batch graphs (no generators): return outputs from batch context.
        For streaming graphs: collect all context results into lists.
        """
        g = self.graph
        has_generators = any(_is_gen(op) for op in g._ops.values())
        if self.stream_contexts:
            # Only collect from leaf contexts (not prefixes of deeper contexts)
            ctx_set = set(self.stream_contexts)
            leaf_ctxs = [
                ctx
                for ctx in self.stream_contexts
                if not any(
                    other != ctx and len(other) > len(ctx) and other[: len(ctx)] == ctx
                    for other in ctx_set
                )
            ]
            outputs = {}
            for var_name in g.outputs:
                outputs[var_name] = [self.state[g.full_name, var_name, ctx] for ctx in leaf_ctxs]
        elif has_generators:
            # Generator(s) yielded 0 items → return empty lists
            outputs = {var_name: [] for var_name in g.outputs}
        else:
            outputs = g.get_outputs(
                self.state, context_id=self.context_id, parent_context=self.parent_context
            )
        return outputs

    async def _drain_ready(self, queue: list, ctx, p_ctx):
        """Drain the ready queue, scheduling ops and extending with newly ready ones."""
        while queue:
            name = queue.pop(0)
            newly_ready = await self._schedule_op(name, ctx, p_ctx)
            queue.extend(newly_ready)

    async def run(
        self,
        state: "MemoryState",
        context_id,
        parent_context,
        request_id: str,
    ) -> Tuple[Dict[str, Any], List]:
        """Execute one scheduler pass.

        Args:
            state: Workflow state.
            context_id: Context of this graph execution.
            parent_context: Context of PARENT, passed to child ops.
            request_id: Request ID for logging.

        Returns:
            (outputs_dict, stream_contexts_list)
        """
        self._reset(state, context_id, parent_context, request_id)

        # Start entry ops
        await self._drain_ready(list(self.graph.entries), self.context_id, self.parent_context)

        # Event loop
        while self.active_count > 0:
            event = await self.event_queue.get()

            if event[0] == "done":
                _, op_name, ctx = event
                self.active_count -= 1
                ready = list(self._activate_successors(op_name, ctx))
                await self._drain_ready(ready, ctx, self.parent_context)

            elif event[0] == "done_pending":
                # Op returned PENDING — absorbed input, no downstream
                self.active_count -= 1

            elif event[0] == "yield":
                _, gen_name, stream_ctx, result_data = event
                self._create_stream_context(stream_ctx, gen_name)
                ready = list(self._activate_successors(gen_name, stream_ctx))
                await self._drain_ready(ready, stream_ctx, self.parent_context)

                # Forward yield event to output queue for real-time delivery
                if self.output_queue is not None:
                    await self.output_queue.put(
                        {"type": "token", "op": gen_name, "data": result_data}
                    )

            elif event[0] == "exhausted":
                self.active_count -= 1

        return self._collect_outputs(), self.stream_contexts
