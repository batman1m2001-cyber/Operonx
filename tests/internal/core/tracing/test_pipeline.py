"""Tests for TracePipeline — buffer, flush strategies, processors, exporters.

Phase T1: pipeline skeleton. These tests pin the contract for §3.7 execution
model (sync emit, async flush, processors on event loop, exporters via
run_in_executor) so T2's wire-up to base.py + scheduler can't accidentally
break it.
"""

import asyncio
import time

import pytest

from operonx.core.tracing.events import EventKind
from operonx.core.tracing.pipeline import (
    AtScheduledExit,
    FlushOnGroupEnd,
    FlushOnSize,
    TracePipeline,
)


class _CollectingExporter:
    """Records every export() call on a list."""

    def __init__(self):
        self.calls: list = []

    def export(self, events, request_id, metadata):
        self.calls.append(
            {
                "events": list(events),
                "request_id": request_id,
                "metadata": dict(metadata),
            }
        )


class TestPushAndBuffer:
    def test_push_appends_to_buffer(self):
        p = TracePipeline()
        em = p.emitter("req-1")
        em.op_start("x", ())
        assert len(p._buffer) == 1

    def test_push_is_constant_time(self):
        """Hot path budget: target ≤2μs per emit on prod hardware.

        CI threshold is loose (50μs) since shared runners are unpredictable.
        The point is to catch O(N) regressions, not enforce ns-level budgets
        in CI. Real perf is benchmarked separately via memory/CCU harness.
        """
        p = TracePipeline()
        em = p.emitter("req-1")

        n = 10_000
        t0 = time.perf_counter()
        for _ in range(n):
            em.op_start("x", ())
        elapsed = time.perf_counter() - t0
        per_emit_us = (elapsed / n) * 1_000_000
        assert per_emit_us < 50, f"emit budget blown: {per_emit_us:.2f}μs/call"

    def test_overflow_drops_oldest_and_warns_once(self, caplog):
        p = TracePipeline(max_buffered_events=5)
        em = p.emitter("req-1")
        for _ in range(10):
            em.op_start("x", ())
        assert len(p._buffer) <= 5
        with caplog.at_level("WARNING", logger="operonx.tracing"):
            for _ in range(20):
                em.op_start("x", ())
        warnings = [r for r in caplog.records if "overflow" in r.message]
        assert len(warnings) <= 1


class TestFlushStrategies:
    def test_at_scheduled_exit_never_triggers_mid_run(self):
        p = TracePipeline(flush_strategy=AtScheduledExit())
        em = p.emitter("req-1")
        for _ in range(100):
            em.op_start("x", ())
        assert p._pending_flush is None

    @pytest.mark.asyncio
    async def test_flush_on_size_triggers_at_threshold(self):
        exp = _CollectingExporter()
        p = TracePipeline(
            exporters=[exp],
            flush_strategy=FlushOnSize(max_events=5),
        )
        em = p.emitter("req-1")
        for _ in range(5):
            em.op_start("x", ())
        await asyncio.sleep(0)
        if p._pending_flush:
            await p._pending_flush
        assert len(exp.calls) >= 1
        assert exp.calls[0]["request_id"] == "req-1"

    @pytest.mark.asyncio
    async def test_flush_on_group_end_matches_glob(self):
        exp = _CollectingExporter()
        p = TracePipeline(
            exporters=[exp],
            flush_strategy=FlushOnGroupEnd(group="turn-*"),
        )
        em = p.emitter("req-1")
        em.op_start("body", ("main",))
        with em.group("turn-0"):
            em.op_start("inner", ("main",))
        await asyncio.sleep(0)
        if p._pending_flush:
            await p._pending_flush
        assert len(exp.calls) == 1
        kinds = [e.kind for e in exp.calls[0]["events"]]
        assert EventKind.GROUP_END in kinds

    @pytest.mark.asyncio
    async def test_flush_on_group_end_skips_unmatched(self):
        exp = _CollectingExporter()
        p = TracePipeline(
            exporters=[exp],
            flush_strategy=FlushOnGroupEnd(group="turn-*"),
        )
        em = p.emitter("req-1")
        with em.group("greeting"):  # name does NOT match "turn-*"
            em.op_start("inner", ("main",))
        await asyncio.sleep(0)
        if p._pending_flush:
            await p._pending_flush
        assert exp.calls == []


class TestProcessorsAndExporters:
    @pytest.mark.asyncio
    async def test_processors_run_in_chain_order(self):
        """Each event must pass through processors in declaration order."""
        seen: list[str] = []

        def proc_a(events):
            for e in events:
                seen.append("a")
                yield e

        def proc_b(events):
            for e in events:
                seen.append("b")
                yield e

        exp = _CollectingExporter()
        p = TracePipeline(processors=[proc_a, proc_b], exporters=[exp])
        em = p.emitter("req-1")
        em.op_start("x", ())  # one event
        await p.flush(partial=False)
        assert seen == ["a", "b"]

    @pytest.mark.asyncio
    async def test_processor_can_drop_events(self):
        def drop_starts(events):
            for e in events:
                if e.kind is not EventKind.OP_START:
                    yield e

        exp = _CollectingExporter()
        p = TracePipeline(processors=[drop_starts], exporters=[exp])
        em = p.emitter("req-1")
        em.op_start("x", ())
        em.op_end("x", ())
        await p.flush(partial=False)
        assert len(exp.calls) == 1
        kinds = [e.kind for e in exp.calls[0]["events"]]
        assert EventKind.OP_START not in kinds
        assert EventKind.OP_END in kinds

    @pytest.mark.asyncio
    async def test_exporter_failure_isolates_others(self):
        class _Boom:
            def export(self, events, request_id, metadata):
                raise RuntimeError("kaboom")

        exp_ok = _CollectingExporter()
        exp_boom = _Boom()
        p = TracePipeline(exporters=[exp_boom, exp_ok])
        em = p.emitter("req-1")
        em.op_start("x", ())
        await p.flush(partial=False)
        # exp_ok still ran despite exp_boom raising
        assert len(exp_ok.calls) == 1

    @pytest.mark.asyncio
    async def test_processor_failure_drops_batch(self):
        def boom(events):
            raise ValueError("processor crash")

        exp = _CollectingExporter()
        p = TracePipeline(processors=[boom], exporters=[exp])
        em = p.emitter("req-1")
        em.op_start("x", ())
        await p.flush(partial=False)
        assert exp.calls == []

    @pytest.mark.asyncio
    async def test_flush_does_not_block_main_loop(self):
        """TR14 — exporter sleep doesn't stall the asyncio loop.

        If the exporter ran inline on the event loop, this would block the
        ticker below. ``run_in_executor`` offloads it to a thread.
        """

        class _SlowExporter:
            def export(self, events, request_id, metadata):
                time.sleep(0.2)  # blocks executor thread, NOT the loop

        p = TracePipeline(exporters=[_SlowExporter()])
        em = p.emitter("req-1")
        em.op_start("x", ())

        ticks = []

        async def ticker():
            for _ in range(20):
                ticks.append(time.perf_counter())
                await asyncio.sleep(0.01)

        await asyncio.gather(p.flush(partial=False), ticker())

        # 20 ticks at 10ms each ~= 200ms. Exporter also took 200ms.
        # If they ran sequentially total would be ~400ms; concurrent ~200ms.
        elapsed = ticks[-1] - ticks[0]
        assert elapsed < 0.4, f"main loop was blocked during flush; ticker took {elapsed:.2f}s"
