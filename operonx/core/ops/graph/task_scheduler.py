"""Task-based workflow scheduler (rewrite).

Single scheduler per workflow execution.
Event-driven: Frame/EOF events drive op dispatch.
"""

import asyncio
from collections import deque
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from time import perf_counter
from typing import Dict, List, Tuple

from operonx.core.loggings import LOGGER
from operonx.core.ops._events import EOF, SELF_CTX, Frame, Interrupt
from operonx.core.states._scratch_var import _reset_state, _set_state
from operonx.core.states.ref import Ref


@dataclass
class LoopConfig:
    """Iteration cap of a synthetic loop (the cycle-rewrite's hidden loop op).

    A loop's stop condition is not configured here: the scheduler stops it
    after an iteration in which no back-edge fired. The cap comes from
    ``if_(..., max_iterations=N)`` on the branch that loops back
    (`cycle_rewrite.loop_cap`); a loop reaching it records
    ``LoopLimitExceeded`` in ``$errors`` and routes nothing.
    """

    max_iterations: int = 1000


# Route policy of an edge nothing was declared on: sequential.
_SEQUENTIAL = (False, 1)


def _is_item_segment(seg) -> bool:
    """``"[3]"`` — the segment a generator's yield adds to its context."""
    return isinstance(seg, str) and len(seg) > 2 and seg[0] == "[" and seg[-1] == "]"


def _last_item_segment(ctx: tuple) -> int:
    """Index of the last ``"[i]"`` segment in ``ctx``, or -1."""
    for i in range(len(ctx) - 1, -1, -1):
        if _is_item_segment(ctx[i]):
            return i
    return -1


def _item_index(seg) -> int:
    try:
        return int(seg[1:-1])
    except (TypeError, ValueError):
        return 0


class InterruptTargetError(ValueError):
    """``Interrupt.SELF`` was emitted where it cannot mean anything."""


def _stamped(
    event: Interrupt,
    op_name: str,
    ctx: tuple,
    emitter_ctx: tuple,
    *,
    is_root_scheduler: bool,
) -> Interrupt:
    """Return a resolved **copy** of ``event``, stamped with its emitter.

    A copy because the op may return a reused object — a module-level
    ``STOP = Interrupt(...)``, a class attribute, the same instance from
    several yields. Resolving in place stamped the first emitter's context
    onto it permanently, so every later emission swept a stale (usually
    already-dead) context and reported the wrong emitter. Measured: two
    different items both recorded ``('main', '[1]')``.

    ``Interrupt.SELF`` becomes ``emitter_ctx``; every other value —
    including ``Interrupt.ALL`` — passes through untouched.

    Raises:
        InterruptTargetError: ``SELF`` from an op at the **outermost**
            scheduler's root context. There "my own context" and
            "everything" are the same tuple, so the sentinel cannot mean
            what it says — and sweeping the run silently is the exact
            failure it replaced. The default was only moved from ``()`` to
            ``("main",)``, which is just as total for a flat graph.

            A *nested* subgraph is exempt even though its root ctx is also
            ``("main",)``: its sweep runs in its own scheduler and cannot
            reach the parent's tasks, so the blast radius is bounded by
            construction rather than by the tuple.
    """
    target = event.ctx_to_cancel
    if target is SELF_CTX:
        if is_root_scheduler and len(emitter_ctx) <= 1:
            raise InterruptTargetError(
                f"Interrupt.SELF from {op_name!r} at context {emitter_ctx}: this op "
                f"runs at the graph's root, where its own context and the whole run "
                f"are the same thing. Name what to cancel — Interrupt(ctx_to_cancel="
                f"Interrupt.ALL) to end the run, or the ctx of the branch you mean. "
                f"SELF is for ops below the root: a generator's yield, a loop "
                f"iteration, a fanned-out item."
            )
        target = emitter_ctx
    return replace(event, op=op_name, ctx=ctx, ctx_to_cancel=target)


def _edge_policy(graph, src: str, dst: str) -> tuple:
    """``(collect, limit, bound)`` for the edge ``src -> dst``, read off dst's Ref.

    The policy lives on the Ref that dst's input holds to src
    (``src["x"].parallel(max=4)``); the first input referencing src wins.
    ``bound`` is ``(max_pending, on_full)`` or None (unbounded, the default).
    """
    dst_op = graph._ops.get(dst)
    for param in getattr(dst_op, "inputs", {}).values():
        ref = getattr(param, "value", None)
        if isinstance(ref, Ref) and getattr(ref.raw_source, "name", None) == src:
            bound = ref._stream_bound
            if ref._stream_collect:
                if bound is not None:
                    raise ValueError(
                        f"{dst} reads {src} with .collect() and max_pending: collect "
                        f"holds the whole stream until it ends, so no bound applies. "
                        f"Drop max_pending, or bound the edge into a per-item op instead."
                    )
                return True, None, None
            if ref._stream_parallel:
                return False, ref._stream_parallel_max or None, bound
            return False, 1, bound
    return False, 1, None


def _collected_vars(graph, src: str, dst: str) -> tuple:
    """The variables of src that dst reads through a ``.collect()`` Ref."""
    dst_op = graph._ops.get(dst)
    return tuple(
        ref.var
        for param in getattr(dst_op, "inputs", {}).values()
        if isinstance(ref := getattr(param, "value", None), Ref)
        and ref._stream_collect
        and getattr(ref.raw_source, "name", None) == src
    )


def _mints_contexts(op) -> bool:
    """Does op emit at contexts of its own: a generator, or a graph with one inside?"""
    if op is None:
        return False
    if getattr(op, "is_gen", False):
        return True
    children = getattr(op, "_ops", None)
    return bool(children) and any(_mints_contexts(c) for c in children.values())


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

    __slots__ = (
        "graph",
        "_route_policy",
        "_wait_bounds",
        "_drop_bounds",
        "_loop_ops",
        "_loop_watch",
        "_collect_gens",
        "_collect_groups",
    )

    def __init__(self, graph):
        self.graph = graph  # static compiled data — never mutated after build()
        # (src, dst) -> (collect, limit) for every edge, resolved once here
        # rather than by scanning dst's inputs for every routed item.
        # ``limit`` caps how many items of the edge run through dst at once:
        # 1 for sequential (the default), N for ``.parallel(max=N)``, None
        # for an unbounded ``.parallel()``.
        self._route_policy: Dict[Tuple[str, str], Tuple[bool, object]] = {}
        # Edges with ``max_pending``. Both empty for a graph that sets none,
        # and every check on the hot path starts with that emptiness, so an
        # unbounded graph pays one falsy test per item and nothing more.
        #   _wait_bounds[src]   = ((src, dst), max_pending) for each of src's
        #                         edges that hold the producer (on_full="wait")
        #   _drop_bounds[(src, dst)] = max_pending for on_full="drop_oldest"
        self._wait_bounds: Dict[str, tuple] = {}
        self._drop_bounds: Dict[Tuple[str, str], int] = {}
        for src, links in graph._adj.items():
            for link in links:
                collect, limit, bound = _edge_policy(graph, src, link.dst)
                self._route_policy[(src, link.dst)] = (collect, limit)
                if bound is None:
                    continue
                cap, on_full = bound
                if on_full == "wait":
                    self._wait_bounds[src] = self._wait_bounds.get(src, ()) + (
                        ((src, link.dst), cap),
                    )
                else:
                    self._drop_bounds[(src, link.dst)] = cap

        # Child ops that are synthetic loops -> the successors their exit
        # routing may reach. A loop op's frames are per iteration, so they
        # are not routed; `_on_eof` routes the op once, when it exits, along
        # the exit edges its body took (plus any outgoing edge no exit
        # accounts for, e.g. a moved lookback edge, which is unconditional).
        self._loop_ops: Dict[str, frozenset] = {}
        for name, child in graph._ops.items():
            if getattr(child, "_loop_mode", None) == "synthetic":
                exits = {dst for _u, dst in child._exit_edges}
                succ = {link.dst for link in graph._adj.get(name, ())}
                self._loop_ops[name] = frozenset(succ - exits)

        # Generators whose stream reaches a `.collect()` edge through at
        # least one per-item op. Such a collect cannot flush on its source's
        # EOF (that is the end of one item), so the scheduler records which
        # generator minted each stream and flushes when that stream is done.
        # Empty for almost every graph, which then pays nothing for it.
        deferred_srcs = {
            src
            for (src, _dst), (collect, _limit) in self._route_policy.items()
            if collect and not getattr(graph._ops.get(src), "is_gen", False)
        }
        self._collect_gens: frozenset = frozenset()
        if deferred_srcs:
            gens = set()
            for name, child in graph._ops.items():
                if not getattr(child, "is_gen", False):
                    continue
                seen, stack = set(), [name]
                while stack:
                    node = stack.pop()
                    for link in graph._adj.get(node, ()):
                        if link.dst not in seen:
                            seen.add(link.dst)
                            stack.append(link.dst)
                if seen & deferred_srcs:
                    gens.add(name)
            self._collect_gens = frozenset(gens)

        # gen -> ((src, dst), ...): the deferred collect edges whose
        # items are this generator's own — reached without passing another
        # op that mints contexts of its own (a nested generator's items are
        # its stream, not this one's). The scheduler opens these groups when
        # the generator mints a stream, so a stream whose every item stopped
        # short of the collect (failed, or took another branch) still flushes,
        # with empty lists, rather than leaving the consumer — and everything
        # after it — waiting for an item that will never come.
        self._collect_groups: Dict[str, tuple] = {}
        for name in self._collect_gens:
            groups = []
            seen, stack = {name}, [name]
            while stack:
                node = stack.pop()
                for link in graph._adj.get(node, ()):
                    dst = link.dst
                    collect, _limit = self._route_policy.get((node, dst), (False, 1))
                    if collect and node in deferred_srcs:
                        groups.append((node, dst))
                    if dst in seen or _mints_contexts(graph._ops.get(dst)):
                        continue
                    seen.add(dst)
                    stack.append(dst)
            if groups:
                self._collect_groups[name] = tuple(groups)

        # When THIS graph is a synthetic loop: the ops whose frames decide
        # how an iteration ended. op -> (back-edge targets, exit targets,
        # is_branch). A frame from a plain op takes all its edges; a
        # branch's frame takes only the edge to the target it chose. An op
        # that raises emits no frame, so it takes none — exactly like a DAG.
        self._loop_watch: Dict[str, tuple] = {}
        if getattr(graph, "_loop_mode", None) == "synthetic":
            watch: Dict[str, tuple] = {}
            for u, v in graph._back_edges:
                watch.setdefault(u, (set(), set()))[0].add(v)
            for u, dst in graph._exit_edges:
                watch.setdefault(u, (set(), set()))[1].add(dst)
            for u, (back, exits) in watch.items():
                if u in graph._ops:
                    is_branch = getattr(graph._ops[u], "type", None) == "branch"
                    self._loop_watch[u] = (frozenset(back), frozenset(exits), is_branch)

    async def run(
        self,
        state,
        context_id: tuple,
        output_queue: asyncio.Queue = None,
    ) -> Tuple[dict, List[tuple], bool]:
        """Drive one execution of the graph.

        Parameters
        ----------
        state:        MemoryState — shared across all ops.
        context_id:   Root context tuple for this execution, e.g. ``("main",)``.
        output_queue: If provided, stream frames to ExecutionHandle and send
                      ``None`` sentinel on completion.

        Returns
        -------
        (outputs, item_ctxs, root_interrupted)
            outputs          - final batch outputs dict (empty when streaming).
            item_ctxs        - per-item context tuples produced by generators.
            root_interrupted - True when a cancellation covered this run's own
                               root context, so ``outputs`` is not a result.
        """
        g = self.graph
        # BUG 7 fix (Phase 3): the top-level scheduler surfaces its
        # output_queue on state so *synthetic hidden loops* can forward
        # frames from their moved SCC ops to the same engine.stream()
        # consumer. Restricted to synthetic loops on the nested-pickup path
        # — regular subgraphs get their frames forwarded by the outer
        # scheduler's own _on_frame (via ``_out_vars``), so having them ALSO
        # forward would double-emit. Cleared after the run so the attribute
        # doesn't leak.
        top_level_stream = False
        if output_queue is not None and getattr(state, "_stream_output_queue", None) is None:
            state._stream_output_queue = output_queue
            top_level_stream = True
        effective_queue = output_queue
        if effective_queue is None and getattr(g, "_loop_mode", None) == "synthetic":
            effective_queue = getattr(state, "_stream_output_queue", None)
        # A loop is not re-run from here: a synthetic loop is one iteration
        # per call, and the scheduler that owns the loop op dispatches the
        # next one from its EOF (see `_on_eof`).
        outputs, item_ctxs, root_interrupted = await self._run_once(
            state, context_id, effective_queue
        )

        # Signal completion to ExecutionHandle (top level only — nested
        # schedulers must not send the None sentinel since the top level's
        # queue keeps receiving frames from other iterations).
        if output_queue is not None and top_level_stream:
            output_queue.put_nowait(None)

        if top_level_stream:
            state._stream_output_queue = None

        return outputs, item_ctxs, root_interrupted

    async def _run_once(
        self,
        state,
        context_id: tuple,
        output_queue: asyncio.Queue = None,
    ) -> Tuple[dict, List[tuple], bool]:
        """Execute the graph exactly once.

        Returns
        -------
        (outputs, item_ctxs, root_interrupted)
            ``root_interrupted`` is True when a sweep covered this run's own
            ``context_id`` — the invocation was cancelled and its outputs are
            whatever happened to be in the cells, not a result.
        """
        g = self.graph
        route_policy = self._route_policy
        loop_ops = self._loop_ops
        loop_watch = self._loop_watch
        # This run's outcome when g is a synthetic loop (one run = one
        # iteration): did a back-edge fire, and which exit targets were taken.
        loop_fired = False
        loop_taken: set = set()
        _start_time = datetime.now(timezone.utc)
        _perf_start = perf_counter()

        # Bind the active MemoryState for this run. Read by the SCRATCH
        # accessor and the post-resolve pass in BaseOp.get_inputs(). Inherited
        # via PEP 567 by every asyncio.create_task / asyncio.to_thread spawned
        # below — concurrent runs see independent values.
        _state_var_token = _set_state(state)

        # All Frame/EOF events flow through here — the main event loop dequeues them.
        queue: asyncio.Queue = asyncio.Queue()

        # Number of live tasks + unconsumed events in queue.
        # dispatch() increments before spawning a task; _pump() decrements in finally.
        # Each Frame/EOF put on queue also increments; _on_frame/_on_eof decrement after processing.
        # When inflight == 0, the graph is fully done.
        # Only the engine passes an output_queue, so this identifies the
        # outermost scheduler — a nested GraphOp runs its own with none.
        _is_root_scheduler = output_queue is not None
        inflight: int = 0
        # Events enqueued for a context but not yet handled by the main
        # loop. `tasks_by_ctx` and `inline_pending` only see work that has
        # already been dispatched, and `seq_queues` only sees work parked
        # behind a sequential edge — none of them sees a Frame sitting on
        # the queue whose consumer has therefore not been dispatched at
        # all. That blind spot is what freed a transient context out from
        # under an async consumer: with a `bound="sync"` middle op the
        # frame is handled inline before the release runs, so two ops and
        # a sync consumer both looked fine while every async chain lost
        # its data.
        pending_events: Dict[tuple, int] = {}

        def _note_event(ctx: tuple, delta: int) -> None:
            n = pending_events.get(ctx, 0) + delta
            if n > 0:
                pending_events[ctx] = n
            else:
                pending_events.pop(ctx, None)

        # Set by _sweep_ctx when a cancellation covers this run's own root.
        root_interrupted: bool = False
        # BaseExceptions (ObserveBudgetExceeded) rescued from _pump so the
        # main loop can re-raise them instead of hanging.
        fatal: List[BaseException] = []

        # ready[ctx][op_name] = number of predecessors still outstanding: one
        # per hard edge plus one for the whole group of soft edges. When it
        # reaches 0, the op is dispatched. ready[ctx][(op_name,)] is set once
        # one of op_name's soft edges has arrived at ctx — later soft arrivals
        # are ignored. It lives in the same dict so a sweep that drops the
        # context drops it too.
        # Root context seeded from _initial_ready; item contexts seeded in _advance().
        ready: Dict[tuple, Dict[str, int]] = {context_id: dict(g._initial_ready)}

        # The stream gate. Keyed by the edge (src, dst) alone, not by the
        # generator context: "sequential" means one item at a time through
        # dst for that edge across the whole run, which is what keeps per-op
        # state (a counter, a buffer) safe when the same consumer serves
        # several streams at once — two nested streams feeding one consumer
        # still take turns. `.parallel(max=N)` is the same gate with room
        # for N; an unbounded `.parallel()` bypasses it.
        #
        # seq_queues[(src, dst)] = deque of item contexts waiting for room.
        seq_queues: Dict[Tuple[str, str], deque] = {}

        # seq_running[(src, dst)] = items of that edge in flight through dst.
        # The next queued item starts only when one of them reaches EOF.
        seq_running: Dict[Tuple[str, str], int] = {}
        # Contexts minted by a transient producer. Everything in them belongs
        # to one item, so the whole context is released when its last op
        # finishes rather than only the cells marked transient.
        transient_ctxs: set = set()

        # seq_origins[(op_name, item_ctx)] = (src, dst) key.
        # When dst_op finishes at item_ctx (EOF arrives), we use this to find
        # which seq_queue to advance. Keyed by (op_name, item_ctx) so two
        # downstream ops from the same generator don't overwrite each other.
        seq_origins: Dict[Tuple[str, tuple], tuple] = {}

        # Backpressure for edges with ``max_pending`` (on_full="wait"). An
        # edge's pending items are the ones parked in its `seq_queues` deque
        # plus the frames its producer has put on the event queue that the
        # main loop has not routed yet. The second half matters: a producer
        # pulling from an already-full inbound queue never suspends, so it
        # can emit a whole backlog before the main loop routes the first
        # frame, and a check on the deque alone would pass every time.
        #   bp_unrouted[src] = src's frames on the event queue, not yet routed
        #   bp_waiters[src]  = futures of src's pumps parked on a full edge
        wait_bounds = self._wait_bounds
        drop_bounds = self._drop_bounds
        bp_unrouted: Dict[str, int] = {}
        bp_waiters: Dict[str, list] = {}

        # collect_bufs[(stream_ctx, src, dst)] = list of (item_ctx, result).
        # Frames on a `.collect()` edge buffer here instead of dispatching,
        # and dst runs once with the lists at stream_ctx + ("__collect__",).
        # Keyed per stream, not per edge: with one buffer per edge, two runs
        # of the same generator in flight at once (a nested stream under
        # `.parallel()`) shared it, and the first to end took both's items.
        #  - src is the generator: stream_ctx is its dispatch ctx; flushed on
        #    its EOF there, which is the end of the stream.
        #  - src runs per item (behind the generator): stream_ctx is the item
        #    ctx minus its last "[i]"; src's EOF is only the end of one item,
        #    so the group is listed in `deferred` and flushed by
        #    `_flush_ended_streams` once the stream has ended.
        #  - otherwise (no stream at all): flushed on src's EOF.
        collect_bufs: Dict[tuple, List] = {}
        deferred: set = set()
        # stream_ctx -> the generator that minted its items, for the streams
        # of `collect_gens` only.
        collect_gens = self._collect_gens
        collect_groups = self._collect_groups
        stream_minter: Dict[tuple, str] = {}

        # Ordered list of item contexts produced by generators.
        # e.g. [("main","[0]"), ("main","[1]"), ...]
        # Returned to GraphOp.run() so it can yield per-item outputs to the caller.
        item_ctxs: List[tuple] = []

        inline_pending: list = []

        # Concurrency gate: limits how many async _pump tasks run simultaneously.
        # Prevents thundering herd when many stream items dispatch at once.
        _sem = asyncio.Semaphore(g.concurrency)

        # tasks_by_ctx[ctx][op_name] = live _pump Task. Used by _sweep_ctx()
        # to cancel the right tasks when an Interrupt event arrives. Keyed
        # by (ctx, op_name) — at most one (op, ctx) pair runs concurrently.
        tasks_by_ctx: Dict[tuple, Dict[str, asyncio.Task]] = {}

        def dispatch(op_name: str, ctx: tuple) -> None:
            """Schedule op based on its bound (sync=inline, io/cpu=task)."""
            nonlocal inflight
            op = g._ops[op_name]
            # A producer on a bounded edge always gets a task, even a plain
            # `def` one: an inline op is iterated by the main loop itself,
            # so it has no way to wait for the main loop to drain its edge.
            if getattr(op, "bound", None) == "sync" and not (
                wait_bounds and op_name in wait_bounds
            ):
                inline_pending.append((op_name, ctx))
            else:
                inflight += 1
                task = asyncio.create_task(_pump(op_name, ctx))
                tasks_by_ctx.setdefault(ctx, {})[op_name] = task

        async def _pump(op_name: str, ctx: tuple) -> None:
            """Drive one op to completion and emit Frame/EOF events.

            Acquires the concurrency semaphore before running the op,
            ensuring at most ``graph.concurrency`` ops run in parallel.

            The ``inflight`` counter is managed here:
            - ``dispatch()`` adds +1 as a reservation before spawning ``_pump``.
            - ``_pump`` adds +1 per Frame and +1 for the EOF it emits.
            - ``finally`` subtracts the original dispatch reservation.
            Net effect: each dispatched op contributes exactly
            ``(N_frames + 1_EOF)`` to ``inflight``, consumed by the main loop.
            """
            nonlocal inflight
            op = g._ops[op_name]
            bp = wait_bounds.get(op_name) if wait_bounds else None

            # Acquired by hand rather than `async with`: a producer parked
            # on a full edge gives its slot back while it waits (below).
            # With `concurrency=1` a producer holding it would starve the
            # very consumer that has to drain the edge. `held` says whether
            # this pump owns a slot to release when it ends.
            await _sem.acquire()
            held = True
            try:
                async for item_ctx, result in op.run(state, ctx):
                    if isinstance(result, Interrupt):
                        # Validated before stamping. Raising from inside
                        # this `async for` would throw into a suspended
                        # generator, whose finally then resets a
                        # ContextVar token from the wrong context — so
                        # the misuse is routed to the main loop instead,
                        # which re-raises it to the caller intact.
                        try:
                            result = _stamped(
                                result,
                                op_name,
                                ctx,
                                item_ctx,
                                is_root_scheduler=_is_root_scheduler,
                            )
                        except InterruptTargetError as e:
                            fatal.append(e)
                            break
                        # Stamp emitter identity so the main loop
                        # knows which task to skip during the
                        # self-cancel guard. That guard looks up
                        # `tasks_by_ctx`, which is keyed by the op's
                        # dispatch ctx — so `.ctx` stays coarse while
                        # SELF resolves to `item_ctx`, the ctx of the
                        # yield that actually emitted the interrupt.
                        queue.put_nowait(result)
                    else:
                        queue.put_nowait(Frame(op_name, item_ctx, result))
                        _note_event(item_ctx, 1)
                        if bp is not None:
                            bp_unrouted[op_name] = bp_unrouted.get(op_name, 0) + 1
                    inflight += 1
                    # The await point of backpressure: the generator is
                    # suspended at its yield, so not resuming it here is
                    # what keeps it from reading its next input.
                    if bp is not None and _bp_full(op_name):
                        _sem.release()
                        held = False
                        await _bp_wait(op_name)
                        await _sem.acquire()
                        held = True
                queue.put_nowait(EOF(op_name, ctx))
                _note_event(ctx, 1)
                inflight += 1
            except asyncio.CancelledError:
                # Cancelled by _sweep_ctx — do not enqueue EOF (the
                # sweep already accounted for queued frames + cleared
                # the consumer bookkeeping).
                raise
            except BaseException as e:
                # An op's own exception never gets here: `BaseOp.run`
                # records it and ends normally. What does is the
                # framework failing around the op, and that is fatal.
                #
                # ObserveBudgetExceeded is a BaseException on purpose —
                # a circuit breaker is not an op result. But letting it
                # escape here enqueued nothing, so the main loop stayed
                # parked in `await queue.get()` with inflight already at
                # zero: the run hung forever and the exception was never
                # retrieved. Hand it to the main loop as an event.
                fatal.append(e)
                queue.put_nowait(EOF(op_name, ctx))
                _note_event(ctx, 1)
                inflight += 1
            finally:
                inflight -= 1
                bucket = tasks_by_ctx.get(ctx)
                if bucket is not None:
                    bucket.pop(op_name, None)
                    if not bucket:
                        tasks_by_ctx.pop(ctx, None)
                        # Last op in this context finished. Nothing else
                        # in the package frees per-context state, so for
                        # a streaming run this is the only thing between
                        # a flat run and unbounded growth.
                        _release_if_done(ctx)
                if held:
                    _sem.release()

        def _bp_full(src: str) -> bool:
            """Has any of src's waiting edges reached its ``max_pending``?"""
            unrouted = bp_unrouted.get(src, 0)
            for key, cap in wait_bounds[src]:
                q = seq_queues.get(key)
                if unrouted + (len(q) if q else 0) >= cap:
                    return True
            return False

        async def _bp_wait(src: str) -> None:
            """Park the calling pump until every bounded edge of src has room."""
            loop = asyncio.get_running_loop()
            while _bp_full(src):
                fut = loop.create_future()
                waiters = bp_waiters.setdefault(src, [])
                waiters.append(fut)
                try:
                    await fut
                finally:
                    # Cancelled while parked: leave no dead future behind.
                    if not fut.done() or fut.cancelled():
                        live = bp_waiters.get(src)
                        if live is not None and fut in live:
                            live.remove(fut)

        def _bp_wake(src: str) -> None:
            """Resume src's parked pumps if its edges have room again.

            All of them, not one: each re-checks in `_bp_wait`'s loop, and
            one woken pump that ends without yielding again would otherwise
            leave the rest parked with nothing left to wake them.
            """
            waiters = bp_waiters.get(src)
            if waiters and not _bp_full(src):
                del bp_waiters[src]
                for fut in waiters:
                    if not fut.done():
                        fut.set_result(None)

        def _drop_oldest(key: tuple) -> None:
            """Drop the stalest item waiting on a full ``drop_oldest`` edge."""
            dropped = seq_queues[key].popleft()
            src, dst = key
            name = f"{g._ops[src].full_name} -> {g._ops[dst].full_name}"
            drops = state._edge_drops
            n = drops.get(name, 0)
            if n == 0:
                LOGGER.warning(
                    f"edge {name} is full (max_pending={drop_bounds[key]}): dropping "
                    f"its oldest waiting items; the count is in handle.drops"
                )
            drops[name] = n + 1
            # Its consumer will never run, so nothing else frees the item.
            _release_if_done(dropped)

        def _release_if_done(ctx: tuple) -> None:
            """Free a context's cells once nothing is left to run in it.

            Both dispatch paths have to call this. Ops with ``bound="sync"``
            never reach ``_pump`` — they are drained inline and never enter
            ``tasks_by_ctx`` — so a hook in ``_pump`` alone silently skipped
            every context whose consumer was sync, which is most of them.
            """
            if tasks_by_ctx.get(ctx):
                return
            if any(pending_ctx == ctx for _, pending_ctx in inline_pending):
                return
            if pending_events.get(ctx):
                return
            # A consumer parked in a sequential queue has not started yet, so
            # it is neither a live task nor an inline pending — and streaming
            # is sequential by default, which makes this the common case
            # rather than the exotic one. Releasing here freed the cells it
            # was still going to read: in a three-op chain the first item
            # dispatched immediately and survived, while every later item
            # arrived at the third op as None. A two-op chain never shows it,
            # which is why the original transient-ports tests missed it.
            for waiting in seq_queues.values():
                if ctx in waiting:
                    return
            for buffered in collect_bufs.values():
                if any(pending_ctx == ctx for pending_ctx, _ in buffered):
                    return
            if ctx in transient_ctxs:
                transient_ctxs.discard(ctx)
                state.release_context(ctx)
            else:
                state.release_transient(ctx)

        async def _drain_inline() -> None:
            """Process all pending inline ops — no task creation, no queue.

            Directly iterates op.run() and feeds Frame/EOF events into
            _on_frame/_on_eof.  If those handlers dispatch more inline ops
            (e.g. downstream sync ops becoming ready), the while-loop picks
            them up immediately.
            """
            while inline_pending:
                op_name, ctx = inline_pending.pop(0)
                op = g._ops[op_name]
                try:
                    async for item_ctx, result in op.run(state, ctx):
                        if isinstance(result, Interrupt):
                            # Validated before stamping. Raising from inside
                            # this `async for` would throw into a suspended
                            # generator, whose finally then resets a
                            # ContextVar token from the wrong context — so
                            # the misuse is routed to the main loop instead,
                            # which re-raises it to the caller intact.
                            try:
                                result = _stamped(
                                    result,
                                    op_name,
                                    ctx,
                                    item_ctx,
                                    is_root_scheduler=_is_root_scheduler,
                                )
                            except InterruptTargetError as e:
                                fatal.append(e)
                                break
                            await _sweep_ctx(result.ctx_to_cancel, exclude=(op_name, ctx))
                            _report_interrupt(result, ctx)
                        else:
                            _on_frame(Frame(op_name, item_ctx, result))
                    _on_eof(EOF(op_name, ctx))
                    _release_if_done(ctx)
                except asyncio.CancelledError:
                    raise
                except BaseException as e:
                    # BaseOp.run records an op's own failure and emits
                    # nothing, so what arrives here is run() itself failing
                    # (or ObserveBudgetExceeded). Same answer as `_pump`:
                    # hand it to the main loop, which raises it once this
                    # drain returns. The old handler wrote
                    # state[op_name, "error"] with the op's local name — not
                    # a schema key — so the caller got a KeyError instead.
                    fatal.append(e)
                    return

        def _report_interrupt(event: Interrupt, ctx: tuple) -> None:
            """Forward the ``__interrupt__`` record to whoever is listening.

            A nested subgraph runs its own scheduler with ``output_queue=None``
            — the outer scheduler forwards its *frames* via ``_out_vars``, so
            passing the queue down would double-emit them. The interrupt record
            is not a frame and is emitted exactly once, at the scheduler that
            performed the sweep, so it can safely fall back to the run-level
            queue. Without this a cancellation inside a subgraph reached
            nobody: ``handle.interrupts`` stayed empty and the only trace of it
            was an all-``None`` result.
            """
            queue_ = output_queue
            if queue_ is None:
                queue_ = getattr(state, "_stream_output_queue", None)
            if queue_ is not None:
                queue_.put_nowait(("__interrupt__", ctx, {"__interrupt__": event}))

        def _on_frame(event: Frame) -> None:
            """Handle one Frame: forward PARENT-bound vars, then advance successors."""
            # Forward PARENT-bound vars to output_queue (root graph only).
            if output_queue is not None:
                out_vars = g._out_vars.get(event.op)
                if out_vars:
                    filtered = {out_vars[k]: v for k, v in event.result.items() if k in out_vars}
                    if filtered:
                        output_queue.put_nowait((event.op, event.ctx, filtered))

            if event.op in loop_ops:
                # One frame per iteration, each at its own context. Routing
                # them ran the loop's successors on every iteration, whichever
                # way the loop went, each in a fresh context of its own.
                # `_on_eof` routes the loop op once, when it exits.
                return
            _advance(event.op, event.ctx, event.result)

        def _advance(src: str, ctx: tuple, result: dict, only=None) -> None:
            """Count src's arrival at ctx on each successor; route those now ready.

            ``only`` restricts the edges followed (a loop's exit routing).
            """
            nonlocal loop_fired
            rc = ready.get(ctx)
            if rc is None:
                # Seed ready counts for a new item context (first frame from a generator).
                item_ctxs.append(ctx)
                rc = ready[ctx] = dict(g._stream_initial_ready.get(src, g._initial_ready))
                if collect_gens and src in collect_gens:
                    stream_ctx = ctx[:-1]
                    if stream_minter.setdefault(stream_ctx, src) == src:
                        # Open this stream's collect groups now, not when an
                        # item first reaches the collect: if none ever does,
                        # the group still flushes when the stream ends.
                        for gsrc, gdst in collect_groups.get(src, ()):
                            key = (stream_ctx, gsrc, gdst)
                            if key not in collect_bufs:
                                collect_bufs[key] = []
                                deferred.add(key)

            # Check for branch target — only route to the selected branch.
            # A branch that matched nothing (no `.else_()`) reports None:
            # that routes nowhere. Only an absent key means "not a branch".
            is_branch = "__branch_target__" in result
            branch_target = result.get("__branch_target__")

            if loop_watch:
                watched = loop_watch.get(src)
                if watched is not None:
                    back, exits, watched_is_branch = watched
                    if not watched_is_branch:
                        loop_fired = loop_fired or bool(back)
                        loop_taken.update(exits)
                    elif branch_target in back:
                        loop_fired = True
                    elif branch_target in exits:
                        loop_taken.add(branch_target)

            # Propagate through adjacency list.
            for edge in g._adj.get(src, ()):
                dst = edge.dst
                if is_branch and dst != branch_target:
                    continue
                if only is not None and dst not in only:
                    continue
                if dst not in rc:
                    continue
                if edge.soft:
                    # The soft edges into an op count as ONE arrival between
                    # them (`_build` gave the whole group a single slot), so
                    # only the first one decrements. Decrementing on each
                    # let two soft arrivals stand in for a hard edge that
                    # had not landed yet, and the op ran with its input
                    # missing.
                    soft_mark = (dst,)
                    if soft_mark in rc:
                        continue
                    rc[soft_mark] = 1
                # All predecessors satisfied — dispatch downstream op.
                rc[dst] -= 1
                if rc[dst] == 0:
                    _route(src, dst, ctx, result)

        def _route(src: str, dst: str, ctx: tuple, result: dict) -> None:
            """Dispatch dst using the correct stream policy (seq/parallel/collect)."""
            if getattr(g._ops.get(src), "transient", False):
                transient_ctxs.add(ctx)

            collect, limit = route_policy.get((src, dst), _SEQUENTIAL)

            if collect:
                # Buffer — see `collect_bufs` for when each group flushes.
                if getattr(g._ops.get(src), "is_gen", False):
                    key = (ctx[:-1], src, dst)
                else:
                    cut = _last_item_segment(ctx)
                    if cut < 0:
                        key = (ctx, src, dst)
                    else:
                        key = (ctx[:cut], src, dst)
                        deferred.add(key)
                collect_bufs.setdefault(key, []).append((ctx, result))

            elif limit is None:
                # Unbounded `.parallel()` — dispatch immediately.
                dispatch(dst, ctx)

            else:
                # Sequential (limit 1) or `.parallel(max=N)`: at most
                # `limit` items of this edge in flight through dst.
                key = (src, dst)
                running = seq_running.get(key, 0)
                if running < limit:
                    seq_running[key] = running + 1
                    seq_origins[(dst, ctx)] = key
                    dispatch(dst, ctx)
                else:
                    q = seq_queues.setdefault(key, deque())
                    if drop_bounds and len(q) >= drop_bounds.get(key, len(q) + 1):
                        _drop_oldest(key)
                    q.append(ctx)

        def _flush_collect(key: tuple) -> None:
            """Hand one collect group to its consumer as lists, in yield order."""
            stream_ctx, src, dst = key
            buf = collect_bufs.pop(key)
            if key in deferred:
                deferred.discard(key)
                # Items finish in any order under `.parallel()`; give the
                # consumer the order the generator yielded them in.
                n = len(stream_ctx)
                buf.sort(key=lambda entry: _item_index(entry[0][n]))
                if not any(k[0] == stream_ctx for k in collect_bufs):
                    stream_minter.pop(stream_ctx, None)
            # Only what dst reads through `.collect()`. No item reaching the
            # collect gives an empty list, not a missing value (which would
            # bind the default).
            merged = {
                var: [r[var] for _, r in buf if var in r] for var in _collected_vars(g, src, dst)
            }
            collect_ctx = stream_ctx + ("__collect__",)
            # Listed once and seeded here, as `_advance` does for a new item.
            # Left unseeded, the consumer's frame at this context looked like
            # the first frame of a new item and listed it a second time, so a
            # subgraph yielded — and stored — its collect result twice. An op
            # after the subgraph still ran once (its ready count was spent);
            # a reducer cell written straight from the subgraph's output got
            # every value twice. Two collects off one stream share the ctx.
            if collect_ctx not in ready:
                ready[collect_ctx] = dict(g._initial_ready)
                item_ctxs.append(collect_ctx)
            # Written straight into src's cells, where dst's pull finds
            # them — not through `store_result`, which also pushes. The
            # lists are dst's input, not a new output of src: storing every
            # output of src through its pushes handed a `PARENT` reducer
            # cell each item a second time, as one list.
            src_name = g._ops[src].full_name
            for var, values in merged.items():
                idx = state.schema.get_index(src_name, var)
                if idx < 0:
                    raise KeyError(f"({src_name}, {var}) not found in schema")
                state._write_cell(idx, collect_ctx, values)
            state.advance_step()
            dispatch(dst, collect_ctx)

        def _busy_below(stream_ctx: tuple) -> bool:
            """Is anything still running, queued or buffered strictly below stream_ctx?"""
            n = len(stream_ctx)

            def below(c):
                return len(c) > n and c[:n] == stream_ctx

            return (
                any(below(c) for c in tasks_by_ctx)
                or any(below(c) for _, c in inline_pending)
                or any(below(c) for c in pending_events)
                or any(below(c) for q in seq_queues.values() for c in q)
                or any(below(k[0]) for k in collect_bufs)
            )

        def _flush_ended_streams() -> bool:
            """Flush deferred collect groups whose stream has ended.

            A stream has ended when the generator that minted it is no
            longer running and nothing is left below its context — every
            item has either reached the collect or stopped short of it (a
            branch not taken, an op that failed). Returns True if anything
            was dispatched.
            """
            flushed = False
            for key in list(deferred):
                stream_ctx = key[0]
                minter = stream_minter.get(stream_ctx)
                if minter is not None and (
                    minter in tasks_by_ctx.get(stream_ctx, ())
                    or (minter, stream_ctx) in inline_pending
                ):
                    continue
                if _busy_below(stream_ctx):
                    continue
                _flush_collect(key)
                flushed = True
            return flushed

        async def _settle() -> None:
            """Drain inline ops, then flush any collect whose stream just ended."""
            await _drain_inline()
            while deferred and not fatal and _flush_ended_streams():
                await _drain_inline()

        def _on_eof(event: EOF) -> None:
            """Handle op completion: flush collect, advance seq queue, check loop."""
            # 1. Flush collect buffers whose stream ended with this EOF.
            if collect_bufs:
                for key in list(collect_bufs):
                    if key[1] == event.op and key[0] == event.ctx and key not in deferred:
                        _flush_collect(key)

            # 2. Advance sequential queue — unblock next waiting item.
            key = seq_origins.pop((event.op, event.ctx), None)
            if key:
                src, dst = key
                q = seq_queues.get(key)
                if q:
                    next_ctx = q.popleft()
                    seq_origins[(dst, next_ctx)] = key
                    dispatch(dst, next_ctx)
                    if wait_bounds and src in bp_waiters:
                        _bp_wake(src)
                else:
                    seq_running[key] -= 1

            # 3. Loop check — a synthetic loop op (the cycle rewrite's hidden
            #    loop) just finished an iteration.
            if event.op in loop_ops:
                _end_iteration(event.op, event.ctx)

        def _end_iteration(name: str, ctx: tuple) -> None:
            """Start the loop's next iteration, or route its successors once."""
            op = g._ops[name]

            # Derive iteration index from the ctx tail so nested loops don't
            # get confused. First iter's ctx has no per-loop suffix → n=0;
            # later ones end in ``{op.full_name}#{n}``. The full name keeps
            # nested loops from colliding on the namespace (HAZARD from
            # Phase 3 review: outer and inner ``__loop_0__`` both bumping to
            # "loop_1" wrote to the same ctx cell, corrupting per-iter
            # checkpoint snapshots). ``#`` is not permitted in op names, so
            # parsing is unambiguous.
            iter_prefix = f"{op.full_name}#"
            tail = ctx[-1] if ctx else None
            n = 0
            if isinstance(tail, str) and tail.startswith(iter_prefix):
                try:
                    n = int(tail[len(iter_prefix) :])
                except ValueError:
                    n = 0
            base = ctx[:-1] if n else ctx

            # How the iteration ended, as seen by the loop's own scheduler
            # (see `_advance`): a back-edge fired iff its source emitted a
            # frame routed along it. That is also what stops the loop when
            # the source raises — a failing op emits nothing. The old test,
            # "the source has an end_time", counted the failed run as a
            # fired edge and spun to the cap. No signal means the iteration
            # never finished its run (it raised before scheduling).
            signal = state._loop_signals.pop((op.full_name, ctx), None)
            fired, taken = signal if signal is not None else (False, frozenset())

            cap = op._loop_config.max_iterations
            if fired and n < cap - 1:
                dispatch(name, base + (f"{iter_prefix}{n + 1}",))
                return

            if signal is None:
                return
            if n:
                _promote_final_values(op, ctx, base)
            if fired:
                # Still looping at the cap: the loop failed, and it says
                # so. It used to stop here silently, with the last
                # iteration's values looking like an answer. Like a
                # failing op, it routes nothing.
                body = ", ".join(repr(child) for child in op._ops)
                error = (
                    f"LoopLimitExceeded: the loop through {body} ran {cap} iterations "
                    f"without exiting and was stopped; the ops after it did not run. "
                    f"Its exit condition never held: check it, or change the cap with "
                    f"if_(..., max_iterations=N) on the branch that loops back."
                )
                state.record_op_error(op.full_name, error)
                err_idx = state.schema.get_index(op.full_name, "error")
                if err_idx >= 0:
                    # Where an enclosing subgraph looks for its ops' failures.
                    state._write_cell(err_idx, base, error)
                return
            # Successors run at the loop's own context — where the ops that
            # joined it before the loop ran, and where a branch finishing
            # after the loop will arrive — not at the last iteration's.
            only = taken | loop_ops[name]
            if only:
                _advance(name, base, {}, only=only)

        def _promote_final_values(op, final_ctx: tuple, base: tuple) -> None:
            """Make the last iteration's outputs the values at the loop's context.

            The loop's successors run at ``base``, which holds iteration 0's
            values; reading there gave an exit-arm op the first iteration's
            result. Copies each body op's outputs, and the PARENT cells they
            push to, from the final iteration's ctx. Shared (declared) cells
            are one value for every ctx already and are left alone; so are
            outputs the body did not write in the final iteration.
            """
            cells = state._cells
            schema = state.schema
            for child in op._ops.values():
                for var in child.outputs or ():
                    idx = schema.get_index(child.full_name, var)
                    if idx < 0:
                        continue
                    for i in (idx, getattr(schema._push_refs[idx], "idx", -1)):
                        if i < 0:
                            continue
                        cell = cells[i]
                        if not cell.is_shared and final_ctx in cell.contexts:
                            state._write_cell(i, base, cell.contexts[final_ctx])

        def _is_descendant_or_equal(child: tuple, parent: tuple) -> bool:
            """True if `child` is `parent` itself or a deeper context."""
            n = len(parent)
            return len(child) >= n and child[:n] == parent

        async def _sweep_ctx(ctx_prefix: tuple, exclude: Tuple[str, tuple] = None) -> None:
            """Drop queued events + cancel in-flight tasks at ctx_prefix
            (and descendants), preserving the inflight invariant.

            `exclude` is the ``(op, ctx)`` of the Interrupt emitter — its
            own pump task is spared so the emitter can finish cleanly
            (its EOF and finally block decrement inflight as normal).
            """
            nonlocal inflight, root_interrupted

            # A sweep covering this run's own root means the invocation was
            # cancelled. A nested GraphOp needs to know: without it, it
            # yields whatever the cells hold — all-``None`` — and the parent
            # forwards that as a perfectly ordinary result.
            if _is_descendant_or_equal(context_id, ctx_prefix):
                root_interrupted = True

            # 1. Drain queue. Frame/EOF/Interrupt items at descendant ctxs
            #    get dropped; inflight is decremented by exactly the drop
            #    count (these were queued events, not in-flight tasks).
            keep: List = []
            drop_count = 0
            while not queue.empty():
                try:
                    item = queue.get_nowait()
                except asyncio.QueueEmpty:
                    break
                item_ctx = getattr(item, "ctx", None)
                item_op = getattr(item, "op", None)
                is_emitter = exclude is not None and (item_op, item_ctx) == exclude
                if (
                    item_ctx is not None
                    and _is_descendant_or_equal(item_ctx, ctx_prefix)
                    and not is_emitter
                ):
                    drop_count += 1
                    if wait_bounds and isinstance(item, Frame) and item_op in wait_bounds:
                        bp_unrouted[item_op] -= 1
                else:
                    # The emitter is spared its own already-queued EOF. A
                    # non-generator op enqueues the Interrupt and its EOF in
                    # the same event-loop slice, so by the time the sweep
                    # runs the EOF is sitting in the queue — and dropping it
                    # meant `_on_eof` never advanced the sequential edge the
                    # emitter was holding. Measured: 6 items in, 1 out, no
                    # error. The seq section below skips the emitter for the
                    # same reason, on the assumption this EOF arrives.
                    keep.append(item)
            for item in keep:
                queue.put_nowait(item)
            inflight -= drop_count

            # 2. Cancel matching in-flight pump tasks (skip the emitter).
            #    For tasks that ran their body, _pump.finally decrements
            #    inflight + cleans tasks_by_ctx. For cancel-before-start,
            #    the body never runs and finally never fires — we detect
            #    that case below and brutally clean up after gather.
            cancelled: List[Tuple[str, tuple, asyncio.Task]] = []
            for ctx in list(tasks_by_ctx):
                if not _is_descendant_or_equal(ctx, ctx_prefix):
                    continue
                for op_name, task in list(tasks_by_ctx[ctx].items()):
                    if exclude == (op_name, ctx):
                        continue
                    if not task.done():
                        task.cancel()
                        cancelled.append((op_name, ctx, task))
            if cancelled:
                await asyncio.gather(*(t for _, _, t in cancelled), return_exceptions=True)
                # Idempotent post-cleanup: if a cancelled task's finally
                # ran, its bucket entry is already gone — skip. Otherwise
                # (cancel-before-start) the bucket still has the entry and
                # inflight wasn't decremented — fix that here.
                for op_name, ctx, _task in cancelled:
                    bucket = tasks_by_ctx.get(ctx)
                    if bucket is not None and op_name in bucket:
                        bucket.pop(op_name, None)
                        if not bucket:
                            tasks_by_ctx.pop(ctx, None)
                        inflight -= 1

            # 3. Clear bookkeeping at descendant ctxs (skip emitter's ctx
            #    so its own pump can complete normally).
            emitter_ctx = exclude[1] if exclude else None
            for ctx in list(ready):
                if _is_descendant_or_equal(ctx, ctx_prefix) and ctx != emitter_ctx:
                    ready.pop(ctx, None)

            # Sequential edges: every cancelled (op_name, ctx) that was
            # holding a `seq_running` slot must release it — otherwise the
            # next item waiting in `seq_queues` for the same edge stays
            # stuck forever (the cancelled pump emitted no EOF, so
            # `_on_eof`'s normal advance path never runs). This mirrors
            # the EOF advance logic at lines ~363-373: drop the
            # seq_origins entry, then either dispatch the next queued
            # item (filtering descendants of ctx_prefix, which are being
            # swept too) or give the slot back.
            for key in list(seq_origins):
                _op_name, _ctx = key
                if not _is_descendant_or_equal(_ctx, ctx_prefix) or _ctx == emitter_ctx:
                    continue

                seq_key = seq_origins.pop(key, None)
                if seq_key is None:
                    continue

                _src, dst = seq_key
                q = seq_queues.get(seq_key)
                if not q:
                    seq_running[seq_key] -= 1
                    continue

                # Filter queued ctxs to keep only siblings (not descendants
                # of the swept prefix — those are being cancelled too).
                kept = deque(c for c in q if not _is_descendant_or_equal(c, ctx_prefix))
                if kept:
                    next_ctx = kept.popleft()
                    seq_queues[seq_key] = kept
                    seq_origins[(dst, next_ctx)] = seq_key
                    dispatch(dst, next_ctx)
                else:
                    seq_queues.pop(seq_key, None)
                    seq_running[seq_key] -= 1

            # A group whose stream is swept goes; a group that only lost some
            # items keeps the rest — dropping it whole meant one cancelled
            # item silently cancelled the collect for all of them.
            for key in list(collect_bufs):
                if _is_descendant_or_equal(key[0], ctx_prefix):
                    collect_bufs.pop(key, None)
                    deferred.discard(key)
                    continue
                buf = collect_bufs[key]
                kept = [e for e in buf if not _is_descendant_or_equal(e[0], ctx_prefix)]
                if len(kept) != len(buf):
                    if kept:
                        collect_bufs[key] = kept
                    else:
                        collect_bufs.pop(key, None)
                        deferred.discard(key)
            for sctx in [c for c in stream_minter if _is_descendant_or_equal(c, ctx_prefix)]:
                stream_minter.pop(sctx, None)

            # Dropped frames and trimmed queues free room on bounded edges.
            for src in list(bp_waiters):
                _bp_wake(src)

            # Inline ops are queued here, not spawned as tasks, so cancelling
            # `tasks_by_ctx` never touched them. `@op` on a plain `def`
            # resolves to bound="sync" — the *default* — so an Interrupt in
            # an all-sync graph swept nothing at all and the run completed
            # normally with an interrupt record attached. Measured:
            # Interrupt.ALL cancelled 0 of 4 downstream ops.
            if inline_pending:
                emitter = exclude if exclude is not None else (None, None)
                inline_pending[:] = [
                    (op_name, ctx)
                    for (op_name, ctx) in inline_pending
                    if not _is_descendant_or_equal(ctx, ctx_prefix) or (op_name, ctx) == emitter
                ]

        try:
            # Seed entry ops.
            for entry in g.entries:
                dispatch(entry, context_id)

            # Drain inline ops seeded above.
            await _settle()
            if fatal:
                raise fatal[0]

            # Main event loop — only runs if task-based ops exist.
            while inflight > 0:
                event = await queue.get()
                inflight -= 1
                _note_event(getattr(event, "ctx", ()), -1)
                if fatal:
                    raise fatal[0]
                if isinstance(event, Frame):
                    _on_frame(event)
                    if wait_bounds and event.op in wait_bounds:
                        # Routed: it now counts in a seq queue, or it ran.
                        bp_unrouted[event.op] -= 1
                        if event.op in bp_waiters:
                            _bp_wake(event.op)
                elif isinstance(event, Interrupt):
                    await _sweep_ctx(event.ctx_to_cancel, exclude=(event.op, event.ctx))
                    _report_interrupt(event, event.ctx)
                else:
                    _on_eof(event)
                # Drain any inline ops triggered by the queue event.
                await _settle()
                # Checked here too, not only after `queue.get()`: when this
                # event brought `inflight` to 0, an error the drain appended
                # (an inline op's misdirected Interrupt.SELF) would otherwise
                # end the loop unseen and the run would return normally.
                if fatal:
                    raise fatal[0]
                # The event is handled and anything it dispatched is now
                # registered, so this is the first moment its context can
                # honestly be called finished.
                _release_if_done(getattr(event, "ctx", ()))
        finally:
            # Cancelling the scheduler must also stop the op tasks it
            # spawned. ``ExecutionHandle.cancel()`` cancels this coroutine
            # and the pump; without the sweep below, every task in
            # ``tasks_by_ctx`` kept running. An op parked on an external
            # await — a generator draining a queue, a socket read — then
            # outlived the run that owned it and never ran its ``finally``.
            #
            # Measured before this block: ``handle.cancel()`` left a parked
            # generator alive, so a streaming graph could not be stopped
            # from outside at all and every caller had to invent an in-band
            # sentinel value instead.
            #
            # On a normal exit this is a no-op: each ``_pump`` clears its own
            # entry in its ``finally``, so ``tasks_by_ctx`` is empty by here.
            _leftover = [
                t for bucket in tasks_by_ctx.values() for t in bucket.values() if not t.done()
            ]
            if _leftover:
                for _t in _leftover:
                    _t.cancel()
                try:
                    await asyncio.gather(*_leftover, return_exceptions=True)
                except asyncio.CancelledError:
                    # Our own cancellation, re-delivered while awaiting the
                    # children. They are already cancelled; swallowing this
                    # one does not suppress the original, which resumes
                    # propagating once this ``finally`` completes.
                    pass

        if getattr(g, "_loop_mode", None) == "synthetic":
            state._loop_signals[(g.full_name, context_id)] = (loop_fired, frozenset(loop_taken))

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

        _reset_state(_state_var_token)

        return outputs, item_ctxs, root_interrupted
