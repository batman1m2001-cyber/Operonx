"""T1.10 — OP_END(status='cancelled') for ops cancelled by Phase B Interrupt sweep.

These tests pin the contract that:
- An op cancelled mid-flight emits OP_END with status='cancelled'
- An op cancelled before its body runs emits NO OP_START and NO OP_END (no orphan)
- The cancellation propagates correctly to the scheduler (Phase B sweep still
  works as designed)

Cancellation flow follows Phase B Interrupt: an op returns/yields ``Interrupt(...)``
and the scheduler aborts in-flight tasks at the target ctx via ``task.cancel()``.
Python's ``try/finally`` semantics guarantee op.run's finally fires on cancel,
so the OP_END emit is reliable without needing a scheduler-side helper.
"""

import asyncio
from typing import Any, Dict

import pytest

from operonx.core import END, PARENT, START, GraphOp, Operon, op
from operonx.core.ops._events import Interrupt
from operonx.core.tracing.events import EventKind
from operonx.core.tracing.pipeline import TracePipeline


class _Collector:
    def __init__(self):
        self.events = []

    def export(self, events, request_id, metadata):
        self.events.extend(events)


@op
async def long_sleep(_signal):
    """Sleeps long enough that the Interrupt sweep aborts it before completion.
    The first ``await asyncio.sleep(0)`` yields control so the body actually
    runs (and emits OP_START) before the kicker cancels it."""
    await asyncio.sleep(0)  # yield: gives scheduler chance to interleave
    await asyncio.sleep(5.0)
    return {"out": "completed"}


@op
async def kick_interrupt(_signal):
    """Async kicker — yields once so ``long_sleep`` gets to start before this
    fires the Interrupt."""
    await asyncio.sleep(0.05)
    return Interrupt(ctx_to_cancel=("main",), reason="test")


class TestCancelledOpEnd:
    @pytest.mark.asyncio
    async def test_op_cancelled_mid_flight_emits_op_end_cancelled(self):
        """``long_sleep`` is sleeping when Interrupt fires; the sweep aborts it.
        We must see OP_END(status='cancelled') for it, not a missing/open span."""
        exp = _Collector()
        pipeline = TracePipeline(exporters=[exp])

        with GraphOp(name="g") as g:
            slow = long_sleep(_signal=PARENT["seed"])
            kick = kick_interrupt(_signal=PARENT["seed"])
            START >> slow >> END
            START >> kick >> END

        engine = Operon(g, tracer=pipeline)
        # The engine completes promptly because Interrupt aborts the sleep
        await asyncio.wait_for(
            engine.run(inputs={"seed": "x"}),
            timeout=2.0,
        )

        slow_starts = [
            e for e in exp.events if e.kind is EventKind.OP_START and e.op_name == "g.slow"
        ]
        slow_ends = [e for e in exp.events if e.kind is EventKind.OP_END and e.op_name == "g.slow"]
        assert len(slow_starts) == 1, "slow op should have emitted exactly one OP_START"
        assert len(slow_ends) == 1, "slow op should have exactly one OP_END (no double-emit)"
        assert slow_ends[0].payload["status"] == "cancelled"
        # Duration is the time elapsed before cancel — should be much less than 5s.
        assert slow_ends[0].payload["duration_ms"] < 2000

    @pytest.mark.asyncio
    async def test_kick_op_completes_normally(self):
        """The kicker op itself isn't cancelled — it returns the Interrupt
        and finishes normally with status='ok'."""
        exp = _Collector()
        pipeline = TracePipeline(exporters=[exp])

        with GraphOp(name="g") as g:
            slow = long_sleep(_signal=PARENT["seed"])
            kick = kick_interrupt(_signal=PARENT["seed"])
            START >> slow >> END
            START >> kick >> END

        engine = Operon(g, tracer=pipeline)
        await asyncio.wait_for(
            engine.run(inputs={"seed": "x"}),
            timeout=2.0,
        )

        kick_ends = [e for e in exp.events if e.kind is EventKind.OP_END and e.op_name == "g.kick"]
        assert len(kick_ends) == 1
        # The kick op's finally runs to completion before sweep cancels anything
        # at its ctx; status reflects normal completion.
        assert kick_ends[0].payload["status"] == "ok"

    @pytest.mark.asyncio
    async def test_no_op_end_when_op_never_started(self):
        """Cancel-before-start case: an op cancelled before its body runs has
        no OP_START emitted, so no OP_END is emitted either — no orphan event.
        Verified via direct task cancel rather than via Phase B (which doesn't
        easily reproduce cancel-before-start in a graph)."""
        from operonx.core.tracing.emitter import EventEmitter, _current_emitter_var

        pipeline = TracePipeline()
        emitter = pipeline.emitter("req-cbs")
        token = _current_emitter_var.set(emitter)

        try:
            # Simulate an op.run-like coroutine that gets cancelled before it
            # reaches its op_start emit.
            cancellation_event = asyncio.Event()

            async def fake_op_run():
                # Yield once to let the canceller run
                await cancellation_event.wait()
                # Would emit OP_START here, but never reached
                emitter.op_start("g.never", ("main",))
                emitter.op_end("g.never", ("main",), status="ok")

            task = asyncio.create_task(fake_op_run())
            await asyncio.sleep(0)  # let task start
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

            # No events for "g.never" — cancel hit before op_start
            never_events = [e for e in pipeline._buffer if e.op_name == "g.never"]
            assert never_events == []
        finally:
            _current_emitter_var.reset(token)
