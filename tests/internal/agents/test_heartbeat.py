"""The heartbeat scheduler.

Every assertion here is about a way a scheduler fails *quietly*. A
scheduler that stopped, that skipped everything, or that built a backlog
all present identically from the outside: nothing is happening. So the
counters are part of the contract, not diagnostics.
"""

from __future__ import annotations

import asyncio
import gc

import pytest

from operonx.agents.heartbeat import Heartbeat

pytestmark = pytest.mark.unit

TICK = 0.05


class FakeSession:
    """Stands in for AgentSession — records what it was asked."""

    def __init__(self, *, delay: float = 0.0, fail: bool = False):
        self.sent: list[str] = []
        self.delay = delay
        self.fail = fail
        self.approvals: list = []

    async def send(self, text: str, *, on_approval=None):
        self.sent.append(text)
        if on_approval is not None:
            self.approvals.append(on_approval)
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.fail:
            raise RuntimeError("model unavailable")
        return {"final": {"role": "assistant", "content": f"ok:{text}"}}


class GatedSession:
    """``send()`` blocks until the test opens that call's gate — a turn in
    flight for exactly as long as the test wants, with no timing guesses.
    Calls past the scripted gates return at once."""

    def __init__(self, gates: int = 1):
        self.sent: list[str] = []
        self.gates = [asyncio.Event() for _ in range(gates)]

    async def send(self, text: str, *, on_approval=None):
        index = len(self.sent)
        self.sent.append(text)
        if index < len(self.gates):
            await self.gates[index].wait()
        return {"final": {"role": "assistant", "content": "ok"}}


async def _until(predicate, timeout: float = 5.0) -> None:
    started = asyncio.get_running_loop().time()
    while not predicate():
        if asyncio.get_running_loop().time() - started > timeout:
            raise AssertionError(f"condition not met within {timeout}s")
        await asyncio.sleep(0.005)


async def _beat_until(hb: Heartbeat, count: int, timeout: float = 5.0) -> None:
    started = asyncio.get_running_loop().time()
    while hb.beats < count:
        if asyncio.get_running_loop().time() - started > timeout:
            raise AssertionError(f"only {hb.beats} beats after {timeout}s")
        await asyncio.sleep(0.01)


class TestValidation:
    @pytest.mark.parametrize("bad", [0, -1])
    def test_a_nonpositive_interval_is_refused(self, bad):
        with pytest.raises(ValueError, match="interval"):
            Heartbeat(FakeSession(), "x", interval=bad)

    @pytest.mark.parametrize("bad", [-0.1, 1.5])
    def test_jitter_outside_the_unit_range_is_refused(self, bad):
        with pytest.raises(ValueError, match="jitter"):
            Heartbeat(FakeSession(), "x", jitter=bad)

    def test_an_unknown_overlap_policy_is_refused(self):
        """A typo falling through to a default would silently pick a
        backlog strategy nobody chose."""
        with pytest.raises(ValueError, match="overlap"):
            Heartbeat(FakeSession(), "x", overlap="cancel")

    def test_zero_max_beats_is_refused(self):
        with pytest.raises(ValueError, match="max_beats"):
            Heartbeat(FakeSession(), "x", max_beats=0)


class TestBeating:
    @pytest.mark.asyncio
    async def test_it_sends_on_the_interval(self):
        session = FakeSession()
        hb = Heartbeat(session, "check", interval=TICK)
        async with hb:
            await _beat_until(hb, 3)
        assert session.sent[:3] == ["check", "check", "check"]

    @pytest.mark.asyncio
    async def test_a_callable_prompt_is_evaluated_each_beat(self):
        counter = {"n": 0}

        def prompt():
            counter["n"] += 1
            return f"beat {counter['n']}"

        session = FakeSession()
        async with Heartbeat(session, prompt, interval=TICK) as hb:
            await _beat_until(hb, 3)
        assert session.sent[:3] == ["beat 1", "beat 2", "beat 3"]

    @pytest.mark.asyncio
    async def test_an_async_prompt_is_awaited(self):
        async def prompt():
            await asyncio.sleep(0)
            return "async prompt"

        session = FakeSession()
        async with Heartbeat(session, prompt, interval=TICK) as hb:
            await _beat_until(hb, 1)
        assert session.sent[0] == "async prompt"

    @pytest.mark.asyncio
    async def test_results_reach_the_sink(self):
        seen: list = []
        async with Heartbeat(FakeSession(), "x", interval=TICK, on_result=seen.append) as hb:
            await _beat_until(hb, 2)
        assert seen[0]["final"]["content"] == "ok:x"

    @pytest.mark.asyncio
    async def test_max_beats_stops_it(self):
        session = FakeSession()
        hb = Heartbeat(session, "x", interval=TICK, max_beats=2)
        await hb.start()
        await asyncio.sleep(TICK * 8)
        assert hb.beats == 2
        assert not hb.running
        await hb.stop()


class TestOverlap:
    @pytest.mark.asyncio
    async def test_a_slow_turn_causes_skips_and_they_are_counted(self):
        """The failure this guards: a run that skipped everything looks
        exactly like a run that beat normally."""
        session = FakeSession(delay=TICK * 6)
        async with Heartbeat(session, "x", interval=TICK) as hb:
            await asyncio.sleep(TICK * 10)
            assert hb.skipped > 0, "overlapping beats must be counted"
            assert len(session.sent) < 5, "a slow turn must not be re-entered"

    @pytest.mark.asyncio
    async def test_queue_runs_one_afterwards_not_a_backlog(self):
        """At most one is held. An unbounded queue is a backlog wearing a
        schedule's clothes — it never drains, and every entry is stale."""
        session = FakeSession(delay=TICK * 6)
        async with Heartbeat(session, "x", interval=TICK, overlap="queue") as hb:
            await asyncio.sleep(TICK * 14)
            assert hb.skipped > 0, "overflow past the single slot must still be counted"

    @pytest.mark.asyncio
    async def test_beats_do_not_overlap_each_other(self):
        concurrent = {"now": 0, "max": 0}

        class Tracking(FakeSession):
            async def send(self, text: str, *, on_approval=None):
                concurrent["now"] += 1
                concurrent["max"] = max(concurrent["max"], concurrent["now"])
                await asyncio.sleep(TICK * 3)
                concurrent["now"] -= 1
                return {"final": {"content": "ok"}}

        async with Heartbeat(Tracking(), "x", interval=TICK) as hb:
            await _beat_until(hb, 3)
        assert concurrent["max"] == 1

    @pytest.mark.asyncio
    async def test_max_beats_is_exact_when_the_last_beat_was_queued(self):
        """Under "queue" the beat chain starts the queued beat itself. The
        ticker only checked the limit *after* dispatching, so once the
        chain had used the last slot the ticker started one more:
        max_beats=2 ran 3 beats."""
        session = GatedSession(gates=2)
        hb = Heartbeat(session, "x", interval=TICK, overlap="queue", max_beats=2)
        await hb.start()
        try:
            await _until(lambda: len(session.sent) == 1)
            # Two ticks inside beat 1: the first queues a beat, the second
            # overflows the single slot and is counted as skipped.
            await _until(lambda: hb.skipped >= 1)
            session.gates[0].set()
            await _until(lambda: len(session.sent) == 2)  # the queued beat
            session.gates[1].set()
            await _until(lambda: not hb.running)
        finally:
            await hb.stop()
        assert len(session.sent) == 2
        assert hb.beats == 2


class TestFailure:
    @pytest.mark.asyncio
    async def test_a_failing_beat_does_not_stop_the_clock(self):
        """A scheduler that dies on its first exception is indistinguishable
        from one with nothing to do."""
        session = FakeSession(fail=True)
        async with Heartbeat(session, "x", interval=TICK) as hb:
            await _beat_until(hb, 3)
            assert hb.errors >= 3
            assert hb.running

    @pytest.mark.asyncio
    async def test_the_error_reaches_the_handler(self):
        seen: list = []
        async with Heartbeat(
            FakeSession(fail=True), "x", interval=TICK, on_error=seen.append
        ) as hb:
            await _beat_until(hb, 2)
        assert isinstance(seen[0], RuntimeError)
        assert "model unavailable" in str(seen[0])

    @pytest.mark.asyncio
    async def test_the_last_error_is_kept_for_inspection(self):
        async with Heartbeat(FakeSession(fail=True), "x", interval=TICK) as hb:
            await _beat_until(hb, 1)
        assert isinstance(hb.last_error, RuntimeError)

    @pytest.mark.asyncio
    async def test_a_throwing_sink_cannot_stop_the_schedule(self):
        def bad_sink(_result):
            raise ValueError("sink is broken")

        async with Heartbeat(FakeSession(), "x", interval=TICK, on_result=bad_sink) as hb:
            await _beat_until(hb, 3)
            assert hb.running
            assert hb.errors >= 2

    @pytest.mark.asyncio
    async def test_a_throwing_error_handler_cannot_stop_it_either(self):
        def bad_handler(_e):
            raise ValueError("reporter is broken")

        async with Heartbeat(
            FakeSession(fail=True), "x", interval=TICK, on_error=bad_handler
        ) as hb:
            await _beat_until(hb, 3)
            assert hb.running


class ReporterDown(BaseException):
    """Not an ``Exception`` — the kind an ``except Exception`` guard lets by.
    Operonx raises several of these on purpose (interrupts, budgets)."""


def _unretrieved(contexts: list) -> list:
    return [c for c in contexts if "never retrieved" in c.get("message", "")]


class TestGuards:
    """`send`/`on_result` were guarded against any ``BaseException``, the
    `on_error` reporter only against ``Exception``. What got past it ended
    the beat task, whose exception nobody read: the next dispatch replaced
    `_beat_task`, and asyncio reported it only when the task was collected."""

    @pytest.fixture
    async def loop_reports(self):
        loop = asyncio.get_running_loop()
        contexts: list = []
        previous = loop.get_exception_handler()
        loop.set_exception_handler(lambda _loop, context: contexts.append(context))
        yield contexts
        loop.set_exception_handler(previous)

    @pytest.mark.asyncio
    async def test_a_base_exception_from_the_error_handler_is_contained(self, loop_reports):
        def bad_handler(_e):
            raise ReporterDown("the reporter is down")

        hb = Heartbeat(FakeSession(fail=True), "x", interval=TICK, on_error=bad_handler)
        await hb.start()
        await _beat_until(hb, 3)
        assert hb.running
        await hb.stop()
        assert hb.errors >= 3
        del hb
        gc.collect()
        assert _unretrieved(loop_reports) == []

    @pytest.mark.asyncio
    async def test_a_beat_that_escapes_its_guard_is_still_retrieved(self, loop_reports):
        """The guards are the first line; the beat task's exception is read
        regardless, so a future gap in them is reported, not lost."""
        hb = Heartbeat(FakeSession(), "x", interval=TICK)

        async def escaped() -> None:
            raise ReporterDown("past the guard")

        hb._beat_once = escaped
        await hb.start()
        await _until(lambda h=hb: h.errors >= 2)
        await hb.stop()
        assert isinstance(hb.last_error, ReporterDown)
        del hb, escaped
        gc.collect()
        assert _unretrieved(loop_reports) == []

    @pytest.mark.asyncio
    @pytest.mark.parametrize("signal", [KeyboardInterrupt, SystemExit])
    @pytest.mark.parametrize("where", ["send", "on_error"])
    async def test_process_exits_are_not_swallowed(self, signal, where):
        """KeyboardInterrupt and SystemExit mean "end the process", and
        asyncio lets them through from any task for that reason. Counting
        a Ctrl-C that lands inside a turn as a failed beat kept the
        schedule going through it. Both guards now let them pass."""

        class Session(FakeSession):
            async def send(self, text, *, on_approval=None):
                if where == "send":
                    raise signal()
                raise RuntimeError("model unavailable")

        reported: list = []

        def handler(e):
            reported.append(e)
            if where == "on_error":
                raise signal()

        hb = Heartbeat(Session(), "x", on_error=handler)
        with pytest.raises(signal):
            await hb._beat_once()
        if where == "send":
            assert reported == [], "a process exit is not a beat error to report"


class TestLifecycle:
    @pytest.mark.asyncio
    async def test_stop_lets_the_current_beat_finish(self):
        """Cancelling mid-send leaves the conversation ending on an
        unanswered user turn, which the next beat would build on."""
        session = FakeSession(delay=TICK * 4)
        hb = Heartbeat(session, "x", interval=TICK)
        await hb.start()
        await _beat_until(hb, 1)
        await asyncio.sleep(TICK * 1.5)  # land inside a beat
        await hb.stop()
        assert not hb.running

    @pytest.mark.asyncio
    async def test_stop_is_idempotent(self):
        hb = Heartbeat(FakeSession(), "x", interval=TICK)
        await hb.start()
        await hb.stop()
        await hb.stop()

    @pytest.mark.asyncio
    async def test_starting_twice_is_refused(self):
        hb = Heartbeat(FakeSession(), "x", interval=TICK)
        await hb.start()
        try:
            with pytest.raises(RuntimeError, match="already running"):
                await hb.start()
        finally:
            await hb.stop()

    @pytest.mark.asyncio
    async def test_stopping_during_the_wait_is_prompt(self):
        """Waiting out a 60s interval to honour stop() would make shutdown
        take a minute."""
        hb = Heartbeat(FakeSession(), "x", interval=30)
        await hb.start()
        await asyncio.sleep(TICK)
        await asyncio.wait_for(hb.stop(), timeout=2.0)
        assert not hb.running

    @pytest.mark.asyncio
    async def test_restart_after_stop(self):
        session = FakeSession()
        hb = Heartbeat(session, "x", interval=TICK)
        await hb.start()
        await _beat_until(hb, 1)
        await hb.stop()
        await hb.start()
        try:
            await _beat_until(hb, 2)
        finally:
            await hb.stop()


class TestStopping:
    """`stop()` lets an in-flight beat finish. That grace window is where
    it went wrong: the caller's own deadline was swallowed, and the
    heartbeat claimed to be stopped while a turn was still running."""

    @pytest.mark.asyncio
    async def test_an_outer_deadline_on_stop_is_raised_not_swallowed(self):
        """A shutdown that gives up on `stop()` must be told it gave up —
        returning normally reads as "stopped" to the caller."""
        session = GatedSession()
        hb = Heartbeat(session, "x", interval=TICK)
        await hb.start()
        await _until(lambda: session.sent)  # a beat is in flight
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(hb.stop(timeout=30), timeout=TICK)
        assert hb.running, "the loop is still finishing the beat stop() let run"

        session.gates[0].set()
        await asyncio.wait_for(hb.stop(), timeout=5)
        assert not hb.running
        assert len(session.sent) == 1, "no beat may start once stop() was asked"

    @pytest.mark.asyncio
    async def test_running_holds_through_the_grace_window_and_start_is_refused(self):
        """`running` read False while the in-flight turn was still going,
        so `start()` was accepted and two loops shared one heartbeat."""
        session = GatedSession()
        hb = Heartbeat(session, "x", interval=TICK)
        await hb.start()
        await _until(lambda: session.sent)
        stopper = asyncio.create_task(hb.stop())
        await asyncio.sleep(0)  # stop() has begun, and is waiting on the beat
        assert not stopper.done()
        assert hb.running
        with pytest.raises(RuntimeError, match="stopping"):
            await hb.start()

        session.gates[0].set()
        await asyncio.wait_for(stopper, timeout=5)
        assert not hb.running
        await hb.start()  # once the stop has finished, a restart is fine
        await hb.stop()

    @pytest.mark.asyncio
    async def test_a_second_stop_waits_as_long_as_the_first(self):
        """The second call found nothing to wait for and returned while the
        first was still waiting for the beat."""
        session = GatedSession()
        hb = Heartbeat(session, "x", interval=TICK)
        await hb.start()
        await _until(lambda: session.sent)
        first = asyncio.create_task(hb.stop())
        second = asyncio.create_task(hb.stop())
        for _ in range(5):
            await asyncio.sleep(0)
        assert not second.done(), "the beat is still in flight"

        session.gates[0].set()
        await asyncio.wait_for(asyncio.gather(first, second), timeout=5)
        assert not hb.running

    @pytest.mark.asyncio
    async def test_a_stop_that_runs_out_of_grace_cancels_the_beat(self):
        session = GatedSession()
        hb = Heartbeat(session, "x", interval=TICK)
        await hb.start()
        await _until(lambda: session.sent)
        with pytest.raises(asyncio.TimeoutError):
            await hb.stop(timeout=TICK)
        assert not hb.running
        await hb.stop()  # and stopping again is a no-op, not a CancelledError


class TestJitter:
    @pytest.mark.asyncio
    async def test_jitter_still_beats(self):
        """Several agents from one deploy would otherwise hit a provider in
        lockstep; the point is that spreading them does not break them."""
        session = FakeSession()
        async with Heartbeat(session, "x", interval=TICK, jitter=0.5) as hb:
            await _beat_until(hb, 3, timeout=8.0)
        assert len(session.sent) >= 3

    @pytest.mark.asyncio
    async def test_full_jitter_never_waits_a_negative_time(self):
        async with Heartbeat(FakeSession(), "x", interval=TICK, jitter=1.0) as hb:
            await _beat_until(hb, 2, timeout=8.0)


class TestApproval:
    @pytest.mark.asyncio
    async def test_the_approval_callback_is_forwarded(self):
        """Without one, a heartbeat that trips a gated tool waits out the
        approval timeout with nobody watching."""

        def approve(_event):
            pass

        session = FakeSession()
        async with Heartbeat(session, "x", interval=TICK, on_approval=approve) as hb:
            await _beat_until(hb, 1)
        assert session.approvals and session.approvals[0] is approve
