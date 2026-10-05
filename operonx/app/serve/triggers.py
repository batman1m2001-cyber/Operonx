"""Triggers: runs started by an event, not by a caller who waits.

Every other door answers someone. An email arriving, a Slack event, a
CRM record changing, eight o'clock in the morning — nobody is waiting on
those, and the work they start can take minutes. Two built-ins:

- ``webhook`` — an HTTP POST is accepted at once: ``202 {"run_id": ...}``,
  and the run goes on in the background, traced like any service run.
  Answering only when the run ended made the sender wait for the whole
  flow, and a webhook sender gives up long before an agent finishes.
- ``schedule`` — a clock: ``every=`` seconds or daily ``at="08:00"``.

Both are ordinary transports (the same protocol as http and websocket), so
the runner, tracing, ``on_session`` and the drain on shutdown are the ones
every service already has.

Three rules, each a way a trigger fails silently:

**A tick that lands while the last run is still going is skipped, and
counted.** Queueing builds a backlog that never drains; overlapping runs
of a sweep do the same work twice. ``ScheduleTransport.skipped`` says how
often it happened, so a schedule that skipped 400 ticks does not look like
one that ran 400 times.

**A failing run does not stop the clock.** Each run is its own session;
the runner logs a failure and the next tick comes anyway.

**A webhook under load says no, loudly.** With ``max_inflight=N`` the N+1th
pending event is answered ``429`` — the sender retries — instead of being
queued without bound behind a slow flow.

With ``queue=`` (a SQLite path, or ``{url = "postgresql://…"}``) a trigger is
durable across restarts and replicas (docs/RUNTIME_R4_PLAN.md D5, D6): a
webhook writes the event to the queue *before* it answers ``202``, and every
replica's transport claims events from it — one that dies mid-run leaves a
lease that lapses, and another replica runs the event again under the same
run id. ``max_inflight`` then bounds the runs a replica holds at once. A
schedule's ticks are aligned to the clock and each fires on one replica.
"""

from __future__ import annotations

import asyncio
import os
import re
import socket
import time
import uuid
from datetime import datetime, timedelta
from typing import Any, AsyncIterator, Dict, Optional, Set

from operonx.core.loggings import LOGGER

from ..queue import LeaseLost, RunQueue, open_queue
from .asgi import AsgiTransport, HttpSession

__all__ = ["ScheduleTransport", "WebhookTransport", "parse_every"]


def _worker_name() -> str:
    """This process, as a queue's rows name the worker holding them."""
    return f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:6]}"


def _queue_of(spec: Any) -> Optional[RunQueue]:
    setting = (getattr(spec, "options", None) or {}).get("queue")
    return open_queue(setting) if setting is not None else None


class QueuedSession(HttpSession):
    """A session made from a queue row; remembers whether its run failed."""

    def __init__(self, payload: Any, meta: Optional[Dict[str, Any]] = None):
        super().__init__(payload, meta=meta)
        self.failed = False

    async def run_failed(self) -> None:
        self.failed = True


class _Claims:
    """Claiming a door's rows from its queue and holding their leases: the
    part a durable webhook and a durable schedule share."""

    def __init__(self, queue: RunQueue, service: str, spec: Any):
        opts = dict(getattr(spec, "options", None) or {})
        self.queue = queue
        self.service = service
        self.worker = _worker_name()
        self.lease_s = float(opts.get("lease", 30))
        self.poll_s = float(opts.get("poll", 1.0))
        self.limit = getattr(spec, "max_inflight", None)
        self.held: Set[asyncio.Task] = set()
        self.wake = asyncio.Event()

    async def next(self, stopped: asyncio.Event) -> Optional[QueuedSession]:
        """The next row this worker claims, as a session — None once
        *stopped*. Waits while the replica holds ``max_inflight`` runs."""
        while not stopped.is_set():
            if not self.limit or len(self.held) < self.limit:
                item = await asyncio.to_thread(
                    self.queue.claim, self.service, self.worker, lease_s=self.lease_s
                )
                if item is not None:
                    meta = dict(item.meta)
                    meta["trace_id"] = item.id
                    meta["attempt"] = item.attempts
                    session = QueuedSession(item.payload, meta=meta)
                    task = asyncio.ensure_future(self._hold(item.id, session))
                    self.held.add(task)
                    task.add_done_callback(self.held.discard)
                    return session
            self.wake.clear()
            try:
                await asyncio.wait_for(self.wake.wait(), self.poll_s)
            except asyncio.TimeoutError:
                pass
        return None

    async def _hold(self, item_id: str, session: QueuedSession) -> None:
        """Renew the row's lease until its run ends, then mark it ended."""
        every = max(self.lease_s / 3, 0.05)
        while not session.finished.is_set():
            try:
                await asyncio.wait_for(session.finished.wait(), every)
                break
            except asyncio.TimeoutError:
                pass
            try:
                await asyncio.to_thread(
                    self.queue.renew, item_id, self.worker, lease_s=self.lease_s
                )
            except LeaseLost:
                LOGGER.warning(
                    f"[serve:{self.service}] lost the lease on run {item_id}: another "
                    "worker runs it now; this run's end will not count"
                )
                return
            except Exception as exc:  # noqa: BLE001 — a blip; the next renew may work
                LOGGER.error(f"[serve:{self.service}] renewing run {item_id}: {exc}")
        status = "failed" if session.failed else "done"
        try:
            await asyncio.to_thread(self.queue.finish, item_id, self.worker, status)
        except Exception as exc:  # noqa: BLE001 — the lease lapses; it runs again
            LOGGER.error(f"[serve:{self.service}] ending run {item_id}: {exc}")
        self.wake.set()  # a slot is free

    async def drain(self) -> None:
        if self.held:
            await asyncio.gather(*list(self.held), return_exceptions=True)


class WebhookTransport(AsgiTransport):
    """``session = "per_request"``, answered before the run, not after it.
    With ``queue=``, the event is durable before the answer."""

    def __init__(self, spec: Any = None):
        super().__init__(spec)
        self._pending: Set[HttpSession] = set()
        self.accepted = 0
        self.refused = 0
        self.queue = _queue_of(spec)
        self._claims: Optional[_Claims] = None
        self._stop_claims = asyncio.Event()

    async def submit(self, payload: Any, meta: Optional[Dict[str, Any]] = None) -> Optional[str]:
        """Take one event; return its run id, or ``None`` when refused.
        With a queue it is written there first: durable when this returns."""
        if self.queue is None:
            return self.accept(payload, meta)
        if self._stopped:
            self.refused += 1
            return None
        meta = dict(meta or {})
        run_id = (
            (meta.get("query") or {}).get("trace_id") or meta.get("trace_id") or uuid.uuid4().hex
        )
        meta["trace_id"] = run_id
        await asyncio.to_thread(
            self.queue.put, getattr(self.spec, "name", "webhook"), payload, meta=meta, id=run_id
        )
        self.accepted += 1
        if self._claims is not None:
            self._claims.wake.set()  # this replica can take it at once
        return run_id

    async def sessions(self) -> AsyncIterator[Any]:
        if self.queue is None:
            async for session in super().sessions():
                yield session
            return
        self._claims = _Claims(self.queue, getattr(self.spec, "name", "webhook"), self.spec)
        try:
            while True:
                session = await self._claims.next(self._stop_claims)
                if session is None:
                    return
                yield session
        finally:
            await self._claims.drain()

    async def close(self) -> None:
        await super().close()
        self._stop_claims.set()
        if self._claims is not None:
            self._claims.wake.set()

    def accept(self, payload: Any, meta: Optional[Dict[str, Any]] = None) -> Optional[str]:
        """Queue one event; return its run id, or ``None`` when refused."""
        self._pending = {s for s in self._pending if not s.finished.is_set()}
        if self._stopped or (self.max_inflight and len(self._pending) >= self.max_inflight):
            self.refused += 1
            return None
        meta = dict(meta or {})
        run_id = (
            (meta.get("query") or {}).get("trace_id") or meta.get("trace_id") or uuid.uuid4().hex
        )
        meta["trace_id"] = run_id
        session = HttpSession(payload, meta=meta)
        self._pending.add(session)
        self.offer(session)
        self.accepted += 1
        return run_id


_UNITS = {"s": 1, "m": 60, "h": 3600, "d": 86400}


def parse_every(every: Any) -> float:
    """``30``, ``"30s"``, ``"5m"``, ``"1h"``, ``"1d"`` → seconds."""
    if isinstance(every, (int, float)):
        seconds = float(every)
    else:
        m = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*([smhd]?)\s*", str(every))
        if not m:
            raise ValueError(f"every={every!r}: expected seconds or a number with s/m/h/d")
        seconds = float(m.group(1)) * _UNITS[m.group(2) or "s"]
    if seconds <= 0:
        raise ValueError(f"every={every!r}: must be more than zero")
    return seconds


def _parse_at(at: str) -> tuple:
    m = re.fullmatch(r"\s*(\d{1,2}):(\d{2})\s*", str(at))
    if not m or int(m.group(1)) > 23 or int(m.group(2)) > 59:
        raise ValueError(f"at={at!r}: expected HH:MM, local time")
    return int(m.group(1)), int(m.group(2))


class ScheduleTransport:
    """A clock that mints one run per tick. No route: it lives in the
    lifespan of the server it is declared on."""

    def __init__(self, spec: Any = None):
        self.spec = spec
        opts = dict(getattr(spec, "options", None) or {})
        every, at = opts.get("every"), opts.get("at")
        if (every is None) == (at is None):
            raise ValueError("schedule needs exactly one of every= or at=")
        self._every = parse_every(every) if every is not None else None
        self._at = _parse_at(at) if at is not None else None
        self._stop = asyncio.Event()
        self._current: Optional[HttpSession] = None
        self.ticks = 0
        self.skipped = 0
        # with a queue every replica ticks on the same slots, and a slot's
        # row decides which one runs it
        self.queue = _queue_of(spec)
        self.lost = 0  # ticks another replica fired

    def _delay(self, now: datetime) -> float:
        if self._every is not None:
            if self.queue is None:
                return self._every
            epoch = now.timestamp()
            return (int(epoch // self._every) + 1) * self._every - epoch
        nxt = now.replace(hour=self._at[0], minute=self._at[1], second=0, microsecond=0)
        if nxt <= now:
            nxt += timedelta(days=1)
        return (nxt - now).total_seconds()

    def _slot(self, now: datetime) -> str:
        """The tick that is due, named the same on every replica."""
        if self._every is not None:
            return str(round(now.timestamp() / self._every))
        return now.strftime("%Y-%m-%d")

    async def sessions(self) -> AsyncIterator[Any]:
        name = getattr(self.spec, "name", "schedule")
        while not self._stop.is_set():
            try:
                await asyncio.wait_for(self._stop.wait(), self._delay(datetime.now()))
                return  # stopped while waiting
            except asyncio.TimeoutError:
                pass
            if self._current is not None and not self._current.finished.is_set():
                self.skipped += 1
                LOGGER.warning(
                    f"[serve:{name}] tick skipped: the last run is still going "
                    f"({self.skipped} skipped so far)"
                )
                continue
            now = datetime.now()
            slot = self._slot(now)
            if self.queue is not None and not await asyncio.to_thread(
                self.queue.fire_once, f"schedule:{name}", slot
            ):
                self.lost += 1  # another replica runs this tick
                continue
            self.ticks += 1
            tick = {"tick": self.ticks, "at": now.isoformat(timespec="seconds")}
            meta = {"query": {}, "trigger": "schedule", "slot": slot, **tick}
            self._current = HttpSession(tick, meta=meta)
            yield self._current

    async def close(self) -> None:
        self._stop.set()
