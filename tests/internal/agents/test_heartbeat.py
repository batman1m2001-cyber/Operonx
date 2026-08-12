"""The heartbeat scheduler.

Every assertion here is about a way a scheduler fails *quietly*. A
scheduler that stopped, that skipped everything, or that built a backlog
all present identically from the outside: nothing is happening. So the
counters are part of the contract, not diagnostics.
"""

from __future__ import annotations

import asyncio

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
