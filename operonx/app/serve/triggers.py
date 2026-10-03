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
"""

from __future__ import annotations

import asyncio
import re
import uuid
from datetime import datetime, timedelta
from typing import Any, AsyncIterator, Dict, Optional, Set

from operonx.core.loggings import LOGGER

from .asgi import AsgiTransport, HttpSession

__all__ = ["ScheduleTransport", "WebhookTransport", "parse_every"]


class WebhookTransport(AsgiTransport):
    """``session = "per_request"``, answered before the run, not after it."""

    def __init__(self, spec: Any = None):
        super().__init__(spec)
        self._pending: Set[HttpSession] = set()
        self.accepted = 0
        self.refused = 0

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

    def _delay(self, now: datetime) -> float:
        if self._every is not None:
            return self._every
        nxt = now.replace(hour=self._at[0], minute=self._at[1], second=0, microsecond=0)
        if nxt <= now:
            nxt += timedelta(days=1)
        return (nxt - now).total_seconds()

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
            self.ticks += 1
            tick = {"tick": self.ticks, "at": datetime.now().isoformat(timespec="seconds")}
            self._current = HttpSession(tick, meta={"query": {}, "trigger": "schedule", **tick})
            yield self._current

    async def close(self) -> None:
        self._stop.set()
