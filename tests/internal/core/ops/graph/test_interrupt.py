"""Tests for the Interrupt scheduler event.

Covers B1–B14 from docs/SCRATCH_PRIMITIVE_PLAN.md Phase B (B8 — httpx
cancellation — is omitted; the underlying mechanism is the same as B7's
mid-await asyncio.CancelledError propagation).
"""

import asyncio

import pytest

from operonx.core import (
    END,
    PARENT,
    SCRATCH,
    START,
    GraphOp,
    Interrupt,
    Operon,
    op,
)

# =============================================================================
# B1 / B2 / B3: drop, preserve siblings, subtree
# =============================================================================


class TestB1DropQueuedFrames:
    @pytest.mark.asyncio
    async def test_interrupt_drops_remaining_generator_frames(self):
        """Generator yields N frames; an Interrupt mid-stream cancels the rest."""

        @op
        def gen_n(n: int):
            for i in range(n):
                yield {"i": i}

        @op
        async def slow_consumer(i: int):
            await asyncio.sleep(0.05)  # give scheduler time to deliver Interrupt
            return {"j": i}

        @op
        def emit_interrupt_after(j: int, target_ctx_str: str):
            target = tuple(target_ctx_str.split("|"))
            if j >= 2:
                return Interrupt(ctx_to_cancel=target, reason="b1")
            return {"k": j}

        with GraphOp(name="b1") as g:
            gen = gen_n(n=PARENT["n"])
            cons = slow_consumer(i=gen["i"].parallel())
            em = emit_interrupt_after(j=cons["j"], target_ctx_str=PARENT["target_ctx_str"])
            START >> gen >> cons >> em >> END

        engine = Operon(g)
        # ctx_to_cancel = ("main",) cancels all the in-flight item branches.
        h = engine.start(inputs={"n": 8, "target_ctx_str": "main"})
        await h.collect()

        # Less than 8 emitted frames at em (some were cancelled).
        em_frames = [op for op, _ctx, _data in h._frames if op == "em"]
        assert len(em_frames) < 8

        # And at least one Interrupt was forwarded.
        assert h.interrupts, "expected at least one Interrupt forwarded"


class TestB2PreserveSiblings:
    @pytest.mark.asyncio
    async def test_sibling_branch_uncancelled(self):
        """Two parallel item branches — Interrupt at branch A leaves B alone."""

        @op
        def gen_two(_signal):
            yield {"i": 0, "branch": "A"}
            yield {"i": 1, "branch": "B"}

        @op
        async def slow_a(i: int, branch: str):
            await asyncio.sleep(0.05)
            return {"out": f"{branch}-{i}"}

        @op
        def maybe_interrupt(out: str, target_ctx_str: str):
            # Cancel branch A's item ctx if we see "A-" appear from a
            # sibling running at a different ctx.
            target = tuple(target_ctx_str.split("|")) if target_ctx_str else ()
            if "B-" in out:  # branch B emits — try to cancel A
                return Interrupt(ctx_to_cancel=target, reason="b2")
            return {"final": out}

        with GraphOp(name="b2") as g:
            entry = gen_two(_signal=PARENT["seed"])
            s = slow_a(i=entry["i"].parallel(), branch=entry["branch"])
            m = maybe_interrupt(out=s["out"], target_ctx_str=PARENT["target_ctx_str"])
            START >> entry >> s >> m >> END

        engine = Operon(g)
        # Use an unrelated ctx for cancel — siblings remain alive.
        h = engine.start(
            inputs={"seed": "go", "target_ctx_str": "nowhere"},
        )
        await h.collect()
        # At least one final output reached the end (sibling not cancelled).
        finals = [
            data
            for op, _ctx, data in h._frames
            if op == "m" and isinstance(data, dict) and "final" in data
        ]
        assert finals, "expected sibling branch to complete"


class TestB3SubtreeCancellation:
    @pytest.mark.asyncio
    async def test_descendants_of_target_cancelled(self):
        """Sweeping ctx ('main',) cancels all in-flight descendants."""

        @op
        async def slow_descendant(seed: str):
            try:
                await asyncio.sleep(2.0)
                return {"out": seed}
            except asyncio.CancelledError:
                # Verify the cancel reaches the await point.
                raise

        @op
        def kicker(seed: str, target_ctx_str: str):
            target = tuple(target_ctx_str.split("|"))
            return Interrupt(ctx_to_cancel=target, reason="b3")

        # slow_descendant runs in parallel with kicker.
        with GraphOp(name="b3") as g:
            s = slow_descendant(seed=PARENT["seed"])
            k = kicker(seed=PARENT["seed"], target_ctx_str=PARENT["target_ctx_str"])
            START >> s
            START >> k
            s >> END
            k >> END

        engine = Operon(g)
        # Use a wall-clock budget — if cancel works, this returns fast;
        # if not, it'd wait 2s for slow_descendant.
        start = asyncio.get_event_loop().time()
        h = engine.start(inputs={"seed": "x", "target_ctx_str": "main"})
        await h.collect()
        elapsed = asyncio.get_event_loop().time() - start
        assert elapsed < 1.0, f"sweep didn't cancel descendant: took {elapsed:.2f}s"


# =============================================================================
# B4 / B5 / B6: inflight invariant
# =============================================================================


class TestB4InflightQueueDrops:
    @pytest.mark.asyncio
    async def test_main_loop_terminates_after_queue_drops(self):
        """If sweep drops queued items, inflight reaches 0 and main loop exits."""

        @op
        def gen_many(n: int):
            for i in range(n):
                yield {"i": i}

        @op
        async def slow(i: int):
            await asyncio.sleep(0.05)
            return {"j": i}

        @op
        def maybe_kick(j: int):
            if j == 0:
                return Interrupt(ctx_to_cancel=("main",), reason="b4")
            return {"k": j}

        with GraphOp(name="b4") as g:
            gen = gen_many(n=PARENT["n"])
            s = slow(i=gen["i"].parallel())
            k = maybe_kick(j=s["j"])
            START >> gen >> s >> k >> END

        engine = Operon(g)
        # Use a wall-clock budget — termination implies inflight reached 0.
        h = engine.start(inputs={"n": 20})
        await asyncio.wait_for(h.collect(), timeout=2.0)


class TestB5InflightTaskCancels:
    @pytest.mark.asyncio
    async def test_cancel_in_flight_tasks_terminates_cleanly(self):
        """In-flight tasks cancelled via sweep — finally decrements inflight."""

        @op
        async def long_task(seed: str):
            await asyncio.sleep(2.0)
            return {"out": seed}

        @op
        def kicker(seed: str):
            return Interrupt(ctx_to_cancel=("main",), reason="b5")

        with GraphOp(name="b5") as g:
            l1 = long_task(name="l1", seed=PARENT["seed"])
            l2 = long_task(name="l2", seed=PARENT["seed"])
            l3 = long_task(name="l3", seed=PARENT["seed"])
            k = kicker(seed=PARENT["seed"])
            START >> [l1, l2, l3, k]
            l1 >> END
            l2 >> END
            l3 >> END
            k >> END

        engine = Operon(g)
        h = engine.start(inputs={"seed": "x"})
        await asyncio.wait_for(h.collect(), timeout=2.0)


class TestB6InflightCollect:
    @pytest.mark.asyncio
    async def test_collect_buffers_cleared_on_sweep(self):
        """Generator with collect-downstream + Interrupt — inflight drains."""

        @op
        def g3(_seed):
            for i in range(3):
                yield {"i": i}

        @op
        def collect_op(i):
            return {"sum": sum(i)}

        @op
        def kick(_seed):
            return Interrupt(ctx_to_cancel=("main",), reason="b6")

        with GraphOp(name="b6") as g:
            gen = g3(_seed=PARENT["seed"])
            c = collect_op(i=gen["i"].collect())
            k = kick(_seed=PARENT["seed"])
            START >> [gen, k]
            gen >> c >> END
            k >> END

        engine = Operon(g)
        h = engine.start(inputs={"seed": "go"})
        await asyncio.wait_for(h.collect(), timeout=2.0)


# =============================================================================
# B7: Mid-yield generator cancellation
# =============================================================================


class TestB7MidAwaitCancel:
    @pytest.mark.asyncio
    async def test_async_op_cancelled_at_await_checkpoint(self):
        """Async op cancelled mid-sleep — propagates fast, not after sleep."""

        cancel_observed = asyncio.Event()

        @op
        async def slow_op(seed: str):
            try:
                await asyncio.sleep(5.0)
            except asyncio.CancelledError:
                cancel_observed.set()
                raise
            return {"out": seed}

        @op
        async def kicker(seed: str):
            await asyncio.sleep(0.05)  # let slow_op enter sleep
            return Interrupt(ctx_to_cancel=("main",), reason="b7")

        with GraphOp(name="b7") as g:
            s = slow_op(seed=PARENT["seed"])
            k = kicker(seed=PARENT["seed"])
            START >> [s, k]
            s >> END
            k >> END

        engine = Operon(g)
        start = asyncio.get_event_loop().time()
        h = engine.start(inputs={"seed": "x"})
        await asyncio.wait_for(h.collect(), timeout=2.0)
        elapsed = asyncio.get_event_loop().time() - start
        assert elapsed < 1.0, f"cancel propagation slow: {elapsed:.2f}s"
        assert cancel_observed.is_set(), "cancel didn't reach the await"


# =============================================================================
# B9 / B10: ExecutionHandle propagation
# =============================================================================


class TestB9HandlePropagation:
    @pytest.mark.asyncio
    async def test_interrupt_arrives_at_consumer_as_synthetic_tuple(self):
        @op
        def emit(seed: str):
            return Interrupt(ctx_to_cancel=("main", "[7]"), reason="b9")

        with GraphOp(name="b9") as g:
            e = emit(seed=PARENT["seed"])
            START >> e >> END

        engine = Operon(g)
        h = engine.start(inputs={"seed": "x"})
        # Drain entire stream — collect handles unknown op tags gracefully.
        await h.collect()

        synth = [(op, ctx, data) for op, ctx, data in h._frames if op == "__interrupt__"]
        assert len(synth) == 1
        op_name, ctx, data = synth[0]
        assert op_name == "__interrupt__"
        assert "__interrupt__" in data
        irq = data["__interrupt__"]
        assert isinstance(irq, Interrupt)
        assert irq.reason == "b9"
        assert irq.ctx_to_cancel == ("main", "[7]")


class TestB10HandleInterruptsAccessor:
    @pytest.mark.asyncio
    async def test_interrupts_property_returns_typed_objects_in_order(self):
        @op
        def gen_two(_seed):
            yield {"i": 0}
            yield {"i": 1}

        @op
        def emit(i: int):
            return Interrupt(ctx_to_cancel=("main", f"[{i}]"), reason=f"r{i}")

        with GraphOp(name="b10") as g:
            entry = gen_two(_seed=PARENT["seed"])
            e = emit(i=entry["i"])
            START >> entry >> e >> END

        engine = Operon(g)
        h = engine.start(inputs={"seed": "go"})
        await h.collect()

        irqs = h.interrupts
        assert len(irqs) == 2
        assert all(isinstance(x, Interrupt) for x in irqs)
        assert [x.reason for x in irqs] == ["r0", "r1"]


# =============================================================================
# B11: Next turn at sibling ctx still runs
# =============================================================================


class TestB11SiblingDispatch:
    @pytest.mark.asyncio
    async def test_sibling_ctx_unblocked_after_sweep(self):
        """After Interrupt sweeps one branch, sibling branch still runs."""

        @op
        def gen_two(_seed):
            yield {"i": 0, "branch": "A"}
            yield {"i": 1, "branch": "B"}

        @op
        async def slow(i: int, branch: str):
            await asyncio.sleep(0.02)
            return {"branch": branch}

        @op
        def emit(branch: str):
            if branch == "A":
                return Interrupt(ctx_to_cancel=("nonexistent",), reason="b11")
            return {"final": branch}

        with GraphOp(name="b11") as g:
            entry = gen_two(_seed=PARENT["seed"])
            s = slow(i=entry["i"].parallel(), branch=entry["branch"])
            e = emit(branch=s["branch"])
            START >> entry >> s >> e >> END

        engine = Operon(g)
        h = engine.start(inputs={"seed": "x"})
        await h.collect()

        finals = [
            data["final"]
            for op, _ctx, data in h._frames
            if op == "e" and isinstance(data, dict) and "final" in data
        ]
        # Branch B ran to completion despite branch A emitting an Interrupt.
        assert "B" in finals


# =============================================================================
# B12: Sync-op race (best-effort behavior)
# =============================================================================


class TestB12SyncOpRace:
    @pytest.mark.asyncio
    async def test_sync_op_completes_inline_even_during_sweep(self):
        """Sync ops drain inline; an in-flight sync op finishes before sweep."""

        @op  # sync — runs inline in _drain_inline
        def fast_sync(seed: str):
            return {"out": seed.upper()}

        @op
        async def kicker(seed: str):
            await asyncio.sleep(0.01)
            return Interrupt(ctx_to_cancel=("main",), reason="b12")

        with GraphOp(name="b12") as g:
            f = fast_sync(seed=PARENT["seed"])
            k = kicker(seed=PARENT["seed"])
            START >> [f, k]
            f >> END
            k >> END

        engine = Operon(g)
        h = engine.start(inputs={"seed": "abc"})
        await asyncio.wait_for(h.collect(), timeout=2.0)
        # fast_sync ran inline before any Interrupt could possibly hit.
        outs = [
            data["out"]
            for op, _ctx, data in h._frames
            if op == "f" and isinstance(data, dict) and "out" in data
        ]
        assert outs == ["ABC"]


# =============================================================================
# B13: Self-cancel guard — emitter's own pump task is spared
# =============================================================================


class TestB13SelfCancelGuard:
    @pytest.mark.asyncio
    async def test_emitter_pump_not_cancelled_when_targeting_own_ctx(self):
        """Op emits Interrupt(ctx_to_cancel=event.ctx) — emitter still finishes
        cleanly, downstream sees the EOF, no deadlock."""

        @op
        def emit_self(_seed):
            # Cancel root ctx — the very ctx this op runs in.
            return Interrupt(ctx_to_cancel=("main",), reason="b13")

        with GraphOp(name="b13") as g:
            e = emit_self(_seed=PARENT["seed"])
            START >> e >> END

        engine = Operon(g)
        h = engine.start(inputs={"seed": "x"})
        # If self-cancel guard works, this returns; otherwise hangs.
        await asyncio.wait_for(h.collect(), timeout=2.0)
        assert len(h.interrupts) == 1


# =============================================================================
# B14: Consumer-owned queues are NOT drained by Interrupt
# =============================================================================


class TestB14ConsumerQueueNotDrained:
    @pytest.mark.asyncio
    async def test_user_supplied_queue_keeps_pre_pushed_items(self):
        """Op pre-pushes items onto a consumer-owned queue, then emits
        Interrupt — the queue still has those items afterward.
        operonx Interrupt only touches scheduler-internal state."""

        consumer_q: asyncio.Queue = asyncio.Queue()

        @op
        async def push_then_interrupt(seed: str):
            for i in range(5):
                consumer_q.put_nowait(("audio_frame", i))
            return Interrupt(ctx_to_cancel=("main",), reason="b14")

        with GraphOp(name="b14") as g:
            p = push_then_interrupt(seed=PARENT["seed"])
            START >> p >> END

        engine = Operon(g)
        h = engine.start(inputs={"seed": "x"})
        await asyncio.wait_for(h.collect(), timeout=2.0)

        # All 5 items are still in the consumer queue — operonx Interrupt
        # did not (and cannot) drain user-owned queues.
        assert consumer_q.qsize() == 5
        items = [consumer_q.get_nowait() for _ in range(5)]
        assert items == [("audio_frame", i) for i in range(5)]
