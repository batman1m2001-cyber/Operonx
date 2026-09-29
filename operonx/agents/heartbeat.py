"""Heartbeat — driving an agent on a schedule instead of a user message.

Most agents are reactive: someone says something, the loop runs, it
answers. Some are not — a monitor that checks a queue every minute, an
agent that files a report each morning. Those need something to poke them,
and that is all this is: a timer that calls ``session.send()``.

It is deliberately small, because the interesting decisions are not about
timing. Three of them shape the whole design, and each one is a place a
scheduler can fail silently.

**A wake that lands while a turn is running is skipped, and counted.** An
agent turn can take minutes; a one-minute interval will overlap. Queueing
by default builds a backlog that never drains and turns a slow model into
an outage. Cancelling the live turn is worse — the schedule would destroy
work the agent was in the middle of. Skipping is the honest default, and
:attr:`Heartbeat.skipped` exists so a run that skipped 400 beats does not
look like a run that beat 400 times.

**A failing beat does not stop the clock.** A scheduler that dies on its
first exception looks exactly like a scheduler with nothing to do. Errors
are counted, reported to ``on_error``, and the next beat happens.

**Stopping is explicit and waits.** A cancelled task mid-``send()`` leaves
the conversation ending on an unanswered user turn. ``stop()`` lets the
current beat finish.
"""

from __future__ import annotations

import asyncio
import random
from typing import Any, Awaitable, Callable, Optional, Union

__all__ = ["Heartbeat", "OverlapPolicy"]

#: What to do when a beat comes due while the previous one is still running.
#:
#: ``"skip"``  — drop it and count it. The default.
#: ``"queue"`` — run it as soon as the current beat finishes. At most one
#:               is held; a second overflow is skipped, because an
#:               unbounded queue is a backlog pretending to be a schedule.
OverlapPolicy = str

_POLICIES = ("skip", "queue")

Prompt = Union[str, Callable[[], Union[str, Awaitable[str]]]]


class Heartbeat:
    """Calls an agent on an interval.

    Args:
        session: An :class:`~operonx.agents.session.AgentSession`. The
            heartbeat shares its conversation, so successive beats see
            what earlier ones did — which is usually the point, and is
            also why a long-running heartbeat needs a ``token_budget``
            on the agent so compaction keeps up.
        prompt: What to send. A string, or a callable returning one — the
            callable form is how a beat says something different each
            time ("check the queue since 10:03").
        interval: Seconds between beats.
        jitter: Fraction of ``interval`` to randomise each wait, in
            ``[0, 1]``. Several agents started by the same deploy would
            otherwise beat in lockstep and hit a provider together.
        overlap: See :data:`OverlapPolicy`.
        on_result: Called with each turn's result dict. Exceptions from it
            are counted as beat errors — a sink that throws must not be
            able to stop the schedule.
        on_error: Called with any exception raised by a beat.
        on_approval: Forwarded to ``session.send`` for gated tools. Without
            one, a heartbeat that trips a destructive tool waits out the
            approval timeout with nobody watching, so this is worth
            passing whenever gated tools are reachable.
        max_beats: Stop after this many successful beats. ``None`` runs
            until :meth:`stop`.

    Example::

        hb = Heartbeat(session, "Any new alerts?", interval=60)
        await hb.start()
        ...
        await hb.stop()
    """

    def __init__(
        self,
        session: Any,
        prompt: Prompt,
        *,
        interval: float = 60.0,
        jitter: float = 0.0,
        overlap: OverlapPolicy = "skip",
        on_result: Optional[Callable[[dict], Any]] = None,
        on_error: Optional[Callable[[BaseException], Any]] = None,
        on_approval: Optional[Callable[[Any], None]] = None,
        max_beats: Optional[int] = None,
    ) -> None:
        if interval <= 0:
            raise ValueError(f"interval must be > 0, got {interval}")
        if not 0.0 <= jitter <= 1.0:
            raise ValueError(f"jitter must be in [0, 1], got {jitter}")
        if overlap not in _POLICIES:
            raise ValueError(f"overlap must be one of {_POLICIES}, got {overlap!r}")
        if max_beats is not None and max_beats < 1:
            raise ValueError(f"max_beats must be >= 1, got {max_beats}")

        self.session = session
        self.prompt = prompt
        self.interval = interval
        self.jitter = jitter
        self.overlap = overlap
        self.on_result = on_result
        self.on_error = on_error
        self.on_approval = on_approval
        self.max_beats = max_beats

        #: Beats that completed, successfully or not.
        self.beats = 0
        #: Beats dropped because the previous one was still running.
        self.skipped = 0
        #: Beats that raised.
        self.errors = 0
        #: The last exception, for a caller that wants to inspect it.
        self.last_error: Optional[BaseException] = None

        self._task: Optional[asyncio.Task] = None
        self._beat_task: Optional[asyncio.Task] = None
        self._started = 0
        self._stopping = asyncio.Event()
        self._pending = False

    # ------------------------------------------------------------ lifecycle

    @property
    def running(self) -> bool:
        """True from :meth:`start` until the loop has actually finished —
        including the grace window in which :meth:`stop` lets a beat end."""
        return self._task is not None and not self._task.done()

    async def start(self) -> "Heartbeat":
        """Begin beating. Returns immediately; beats run in the background.

        Raises:
            RuntimeError: the heartbeat is running, or still stopping. A
                start during a stop is refused rather than waited for: the
                wait is the in-flight turn, which can take minutes, and a
                start that races a stop is a sequencing bug in the caller
                that waiting would hide. Await :meth:`stop` first.
        """
        if self.running:
            if self._stopping.is_set():
                raise RuntimeError(
                    "this heartbeat is still stopping — await stop() before starting it again"
                )
            raise RuntimeError("this heartbeat is already running")
        self._stopping.clear()
        self._pending = False
        self._started = 0
        self._task = asyncio.create_task(self._loop())
        return self

    async def stop(self, *, timeout: float = 30.0) -> None:
        """Stop beating, letting a beat already in flight finish.

        Cancelling mid-``send()`` would leave the conversation ending on
        an unanswered user turn, which the next beat would then build on.

        Every caller waits for the same thing — the loop to finish — so a
        second concurrent ``stop()`` returns no sooner than the first.

        Raises:
            asyncio.TimeoutError: the beat outran ``timeout`` and was
                cancelled.
            asyncio.CancelledError: the caller was cancelled while waiting
                (an outer deadline). The heartbeat still stops — no new
                beat starts — and ``running`` stays True until the
                in-flight one has finished.
        """
        self._stopping.set()
        # `_task` is left in place: `running` must stay True until the loop
        # has finished. Clearing it here made the heartbeat read as stopped
        # for the whole grace window, so `start()` was accepted and a second
        # loop shared this one's state while the first was still in a turn.
        task = self._task
        if task is None or task.done():
            return
        try:
            # Shielded, so a caller that gives up waiting does not cancel
            # the loop mid-beat; with `_stopping` set it winds down alone.
            # No `except CancelledError`: that deadline is the caller's, and
            # swallowing it made `wait_for(hb.stop(), 0.2)` return normally.
            await asyncio.wait_for(asyncio.shield(task), timeout=timeout)
        except asyncio.TimeoutError:
            # The beat outran the grace period. Now cancelling is the
            # lesser evil, and the caller is told by the raised error.
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            raise

    async def __aenter__(self) -> "Heartbeat":
        return await self.start()

    async def __aexit__(self, *_exc: object) -> None:
        try:
            await self.stop()
        except asyncio.TimeoutError:  # pragma: no cover - teardown
            pass

    # ---------------------------------------------------------------- loop

    def _wait_for(self) -> float:
        if not self.jitter:
            return self.interval
        spread = self.interval * self.jitter
        return max(0.0, self.interval + random.uniform(-spread, spread))

    async def _loop(self) -> None:
        """The ticker. Deliberately does **not** await the beat.

        Awaiting it here would mean the clock stops while a turn runs, so
        the effective period becomes ``interval + turn duration`` and two
        beats can never overlap — which quietly makes the whole overlap
        policy dead code. Measured before the split: `skipped` stayed 0
        with a turn six times longer than the interval.

        The beat therefore runs as its own task and the ticker keeps time.
        """
        while not self._stopping.is_set():
            try:
                await asyncio.wait_for(self._stopping.wait(), timeout=self._wait_for())
                break  # stop() was called during the wait
            except asyncio.TimeoutError:
                pass  # the interval elapsed — the normal path

            if self._stopping.is_set():
                break

            if self._quota_reached():
                # Checked *before* dispatching too, because the ticker is
                # not the only one that starts beats: under "queue" the
                # beat chain starts the queued one itself. Checking only
                # after its own dispatch let the ticker start one more
                # once the chain had used the last slot — max_beats=2 ran
                # 3 beats.
                break

            if self._beat_task is not None and not self._beat_task.done():
                if self.overlap == "queue" and not self._pending:
                    self._pending = True
                else:
                    # Counted, not silent: a run that skipped 400 beats
                    # must not look like one that beat 400 times. Under
                    # "queue" this is the *overflow* past the single slot,
                    # because an unbounded queue is a backlog wearing a
                    # schedule's clothes.
                    self.skipped += 1
                continue

            # Counted on *dispatch*, not completion. `beats` only rises
            # when a beat finishes, so gating on it starts one more than
            # asked for whenever a turn outlasts the interval.
            self._started += 1
            self._beat_task = asyncio.create_task(self._beat_chain())

            if self._quota_reached():
                break

        await self._drain()

    def _quota_reached(self) -> bool:
        """Every beat ``max_beats`` allows has been started, by either the
        ticker or the beat chain — both count into ``_started``."""
        return self.max_beats is not None and self._started >= self.max_beats

    async def _beat_chain(self) -> None:
        """One beat, plus any single queued follow-up."""
        await self._beat_once()
        while self._pending and not self._stopping.is_set():
            if self._quota_reached():
                break
            self._pending = False
            self._started += 1
            await self._beat_once()

    async def _drain(self) -> None:
        """Let a beat already in flight finish."""
        task = self._beat_task
        if task is not None and not task.done():
            await asyncio.gather(task, return_exceptions=True)

    async def _beat_once(self) -> None:
        try:
            text = self.prompt() if callable(self.prompt) else self.prompt
            if asyncio.iscoroutine(text) or isinstance(text, Awaitable):
                text = await text

            result = await self.session.send(
                str(text),
                **({"on_approval": self.on_approval} if self.on_approval else {}),
            )
            if self.on_result is not None:
                # Inside the try: a sink that throws is a beat error, not
                # a reason for the schedule to stop.
                maybe = self.on_result(result)
                if asyncio.iscoroutine(maybe):
                    await maybe
        except asyncio.CancelledError:
            raise
        except BaseException as e:  # noqa: BLE001 — a scheduler outlives its beats
            self.errors += 1
            self.last_error = e
            if self.on_error is not None:
                try:
                    maybe = self.on_error(e)
                    if asyncio.iscoroutine(maybe):
                        await maybe
                except Exception:  # noqa: BLE001 — the reporter is not the schedule
                    pass
        finally:
            self.beats += 1
