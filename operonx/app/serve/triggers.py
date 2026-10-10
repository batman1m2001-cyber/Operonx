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

On a queued webhook (D8, D10): ``multitask=`` says what a second message on
a busy thread does — ``enqueue`` (wait its turn, the default), ``reject``
(``409``), ``interrupt`` (stop the running one, keep what it did) or
``rollback`` (stop it, mark it discarded); the thread is ``?thread_id=`` or
the ``x-operonx-thread`` header. ``callback_hosts=[...]`` lets a request
name ``?callback=<url>``, POSTed ``{run_id, service, status, output,
errors}`` by whichever worker ends the run.
"""

from __future__ import annotations

import asyncio
import os
import re
import socket
import uuid
from datetime import datetime, timedelta
from typing import Any, AsyncIterator, Dict, List, Optional, Set
from urllib.parse import urlparse

from operonx.core.loggings import LOGGER

from ..manifest import ManifestError
from ..queue import LeaseLost, RunQueue, open_queue
from .asgi import AsgiTransport, HttpSession

__all__ = ["MULTITASK", "Refused", "ScheduleTransport", "WebhookTransport", "parse_every"]

#: What a second message on a busy thread does (a queued webhook's
#: ``multitask=``).
MULTITASK = ("enqueue", "reject", "interrupt", "rollback")

#: How a callback is retried: the waits between its tries.
CALLBACK_BACKOFF = (0.5, 1.0, 2.0)


class Refused(Exception):
    """A request the door answers itself, without a run: ``status`` and
    the JSON ``body`` of the answer."""

    def __init__(self, status: int, body: Dict[str, Any]):
        super().__init__(body.get("error", ""))
        self.status, self.body = status, body


def _worker_name() -> str:
    """This process, as a queue's rows name the worker holding them."""
    return f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:6]}"


def _queue_of(spec: Any) -> Optional[RunQueue]:
    setting = (getattr(spec, "options", None) or {}).get("queue")
    return open_queue(setting) if setting is not None else None


class QueuedSession(HttpSession):
    """A session made from a queue row; remembers whether its run failed,
    and the run's handle (to stop it)."""

    def __init__(self, payload: Any, meta: Optional[Dict[str, Any]] = None):
        super().__init__(payload, meta=meta)
        self.failed = False
        self.handle: Any = None

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
        self._pokes: Dict[str, asyncio.Event] = {}

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
                    self._pokes[item.id] = asyncio.Event()
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

    def poke(self, item_id: str) -> None:
        """Renew *item_id* now, if this worker holds it: a stop asked on
        this replica is seen at once, not at the next renew."""
        event = self._pokes.get(item_id)
        if event is not None:
            event.set()

    async def _hold(self, item_id: str, session: QueuedSession) -> None:
        """Renew the row's lease until its run ends, then mark it ended.
        A renew that brings a stop request stops the run."""
        every = max(self.lease_s / 3, 0.05)
        poke = self._pokes[item_id]
        status: Optional[str] = None
        try:
            while not session.finished.is_set():
                done = asyncio.ensure_future(session.finished.wait())
                poked = asyncio.ensure_future(poke.wait())
                await asyncio.wait({done, poked}, timeout=every, return_when="FIRST_COMPLETED")
                done.cancel()
                poked.cancel()
                poke.clear()
                if session.finished.is_set():
                    break
                try:
                    stop = await asyncio.to_thread(
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
                    continue
                if stop is not None:
                    status = await self._stop(item_id, session, stop)
                    break
            if status is None:
                status = "failed" if session.failed else "done"
            try:
                await asyncio.to_thread(self.queue.finish, item_id, self.worker, status)
            except Exception as exc:  # noqa: BLE001 — the lease lapses; it runs again
                LOGGER.error(f"[serve:{self.service}] ending run {item_id}: {exc}")
                return
            await self._call_back(item_id, session, status)
        finally:
            self._pokes.pop(item_id, None)
            self.wake.set()  # a slot is free

    async def _stop(self, item_id: str, session: QueuedSession, how: str) -> str:
        """Stop the run for a newer message on its thread: ``interrupt``
        keeps what it did (drained when it has a journal), ``rollback``
        discards it."""
        handle = session.handle
        LOGGER.info(f"[serve:{self.service}] {how}: stopping run {item_id} for a newer message")
        if handle is not None:
            if how == "interrupt" and getattr(handle.state, "_durable", None) is not None:
                await handle.drain()
            else:
                handle.cancel()
        return "stopped" if how == "interrupt" else "discarded"

    async def _call_back(self, item_id: str, session: QueuedSession, status: str) -> None:
        """POST the run's end to the URL its request named, if any."""
        url = (session.meta or {}).get("callback")
        if not url:
            return
        import httpx

        handle = session.handle
        body = {
            "run_id": item_id,
            "service": self.service,
            "status": status,
            "output": session.reply,
            "errors": dict(getattr(handle, "errors", None) or {}),
        }
        async with httpx.AsyncClient(timeout=10) as client:
            for wait in (*CALLBACK_BACKOFF, None):
                try:
                    reply = await client.post(url, json=body)
                    if reply.status_code < 500:
                        return
                    problem = f"HTTP {reply.status_code}"
                except httpx.HTTPError as exc:
                    problem = f"{type(exc).__name__}: {exc}"
                if wait is None:
                    LOGGER.error(
                        f"[serve:{self.service}] callback for run {item_id} to {url} "
                        f"failed {len(CALLBACK_BACKOFF) + 1} times: {problem}"
                    )
                    return
                await asyncio.sleep(wait)

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
        opts = dict(getattr(spec, "options", None) or {})
        name = getattr(spec, "name", "webhook")
        self.multitask = opts.get("multitask")
        self.callback_hosts: List[str] = list(opts.get("callback_hosts") or [])
        for key, given in (("multitask", self.multitask), ("callback_hosts", self.callback_hosts)):
            if given and self.queue is None:
                raise ManifestError(
                    f"service {name!r}: {key}= needs queue= — the queue is where a thread's "
                    "runs and a run's callback are kept"
                )
        if self.multitask is not None and self.multitask not in MULTITASK:
            raise ManifestError(
                f"service {name!r}: multitask={self.multitask!r}; one of {', '.join(MULTITASK)}"
            )

    async def submit(self, payload: Any, meta: Optional[Dict[str, Any]] = None) -> Optional[str]:
        """Take one event; return its run id, or ``None`` when refused.
        With a queue it is written there first: durable when this returns."""
        if self.queue is None:
            return self.accept(payload, meta)
        if self._stopped:
            self.refused += 1
            return None
        meta = dict(meta or {})
        query = meta.get("query") or {}
        run_id = query.get("trace_id") or meta.get("trace_id") or uuid.uuid4().hex
        meta["trace_id"] = run_id
        service = getattr(self.spec, "name", "webhook")
        callback = query.get("callback")
        if callback:
            host = urlparse(callback).hostname
            if (
                urlparse(callback).scheme not in ("http", "https")
                or host not in self.callback_hosts
            ):
                raise Refused(
                    400,
                    {
                        "error": f"callback host {host!r} is not one this service calls "
                        f"(callback_hosts={self.callback_hosts})",
                        "service": service,
                    },
                )
            meta["callback"] = callback
        thread = query.get("thread_id") or (meta.get("headers") or {}).get("x-operonx-thread")
        if thread and self.multitask in ("reject", "interrupt", "rollback"):
            await self._make_room(service, thread)
        await asyncio.to_thread(
            self.queue.put, service, payload, meta=meta, id=run_id, thread_id=thread
        )
        self.accepted += 1
        if self._claims is not None:
            self._claims.wake.set()  # this replica can take it at once
        return run_id

    async def _make_room(self, service: str, thread: str) -> None:
        """Apply ``multitask`` to the thread's queued and running rows."""
        busy = [
            item
            for item in await asyncio.to_thread(self.queue.items, service, thread_id=thread)
            if item.status in ("queued", "running")
        ]
        if not busy:
            return
        if self.multitask == "reject":
            self.refused += 1
            raise Refused(
                409,
                {
                    "error": f"thread {thread!r} has a run going; this service rejects a "
                    "second message (multitask='reject')",
                    "service": service,
                    "thread_id": thread,
                    "run_id": busy[0].id,
                },
            )
        for item in busy:
            await asyncio.to_thread(self.queue.request_stop, item.id, self.multitask)
            if self._claims is not None:
                self._claims.poke(item.id)

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


class JobClock:
    """A scheduled job's clock (``Job(schedule=schedule(...))``): each tick
    runs the job once, with what triggered it in the run's record.

    The clock is the schedule door's own: a tick that lands while the last
    run is still going is skipped and counted (``skipped``), and with
    ``queue=`` each tick fires on one replica. A run that fails — or raises
    — is logged and counted (``failed``); the next tick comes anyway. Each
    run is fresh: a scheduled job never resumes.
    """

    def __init__(self, spec: Any):
        self.spec = spec
        self.job = spec.options["job"]
        self.transport = ScheduleTransport(spec)
        self.runs: List[Any] = []  # the records of the runs it started, newest last
        self.failed = 0
        self._tasks: Set[asyncio.Task] = set()

    @property
    def skipped(self) -> int:
        return self.transport.skipped

    async def _fire(self, session: HttpSession) -> None:
        meta = session.meta or {}
        trigger = {"by": "schedule", "slot": meta.get("slot"), "at": meta.get("at")}
        try:
            run = await self.job.run(trigger=trigger)
            self.runs.append(run)
            if getattr(run, "status", None) != "ok":
                self.failed += 1
                LOGGER.error(
                    f"[job:{self.job.name}] scheduled run {run.run_id} ended {run.status}; "
                    "the clock goes on"
                )
        except Exception as exc:  # noqa: BLE001 — one bad run never stops the clock
            self.failed += 1
            LOGGER.error(
                f"[job:{self.job.name}] scheduled run raised {type(exc).__name__}: {exc}; "
                "the clock goes on"
            )
        finally:
            await session.close()  # the tick is over: the next one may run

    async def run(self) -> None:
        LOGGER.info(
            f"[serve:{self.spec.name}] schedule "
            + ", ".join(f"{k}={v}" for k, v in self.spec.options.items() if k in ("every", "at"))
            + f" -> job {self.job.name!r}"
        )
        try:
            async for session in self.transport.sessions():
                task = asyncio.ensure_future(self._fire(session))
                self._tasks.add(task)
                task.add_done_callback(self._tasks.discard)
        finally:
            if self._tasks:
                await asyncio.gather(*list(self._tasks), return_exceptions=True)

    async def close(self) -> None:
        await self.transport.close()
