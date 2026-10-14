"""How late the event loop ran while a run was going.

A run that is slow because its process's loop is busy looks, in its own
trace, like ops that took long — nothing says the loop was the cause.
One beacon per loop (a heartbeat every :data:`INTERVAL_S`) records how
late each tick woke, and a run reads its own window when it ends
(``metadata["loop_lag_ms"] = {"max", "p95"}``). The same beacon reads the
process's CPU time once a second, so a run also says how busy its worker
was (``metadata["cpu_pct"] = {"avg", "max"}``, one core = 100): a loop
that is late because the process is out of CPU, or late for another reason.

The beacon runs only where :func:`start` was called — the serve layer
does, once per listener — so a plain ``engine.run`` pays one dict lookup.
"""

from __future__ import annotations

import asyncio
import time
import weakref
from collections import deque
from typing import Dict, Optional

__all__ = ["INTERVAL_S", "cpu", "start", "window"]

#: The heartbeat. A tick that wakes late by more than this is a loop that
#: was held: by a blocking call, a long synchronous op, or the GIL.
INTERVAL_S = 0.05
#: Ticks kept: 20 minutes, more than any run's window needs.
_KEEP = int(20 * 60 / INTERVAL_S)
_KEEP_S = 20 * 60  # CPU readings, one a second

#: Set by the first `start()`: until then `window()` returns at once, so a
#: process that serves nothing pays one global read per run.
_started = False
_beacons: "weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, _Beacon]" = (
    weakref.WeakKeyDictionary()
)


class _Beacon:
    __slots__ = ("ticks", "since", "seconds", "mark", "task", "__weakref__")

    def __init__(self, loop: asyncio.AbstractEventLoop) -> None:
        self.ticks: deque = deque(maxlen=_KEEP)  # (wall time, lag in ms)
        self.since = time.perf_counter()  # when the pending tick went to sleep
        self.seconds: deque = deque(maxlen=_KEEP_S)  # (wall time, CPU % over the last ~1 s)
        self.mark = (time.perf_counter(), time.process_time())  # the reading in progress
        self.task = loop.create_task(self._beat(), name="operonx-loop-lag")

    async def _beat(self) -> None:
        clock = time.perf_counter
        cpu_clock = time.process_time
        while True:
            self.since = before = clock()
            await asyncio.sleep(INTERVAL_S)
            now = clock()
            late = (now - before - INTERVAL_S) * 1000
            self.ticks.append((time.time(), late if late > 0 else 0.0))
            mark, mark_cpu = self.mark
            if now - mark >= 1.0:
                used = cpu_clock()
                self.seconds.append((time.time(), 100.0 * (used - mark_cpu) / (now - mark)))
                self.mark = (now, used)


def start() -> None:
    """Start the running loop's beacon, once. Call it from a coroutine."""
    global _started
    loop = asyncio.get_running_loop()
    _started = True
    beacon = _beacons.get(loop)
    if beacon is None or beacon.task.done():
        _beacons[loop] = _Beacon(loop)


def _beacon() -> Optional[_Beacon]:
    if not _started:
        return None
    try:
        return _beacons.get(asyncio.get_running_loop())
    except RuntimeError:
        return None


def cpu(started: float, ended: float) -> Optional[Dict[str, float]]:
    """``{"avg", "max"}`` of the process's CPU use, in % of one core, over
    the 1 s readings between two wall times, and the reading in progress
    (a run that ends right after its process was busy reads its window
    before that reading lands); ``None`` with no beacon or no reading."""
    beacon = _beacon()
    if beacon is None:
        return None
    mark, mark_cpu = beacon.mark
    elapsed = time.perf_counter() - mark
    used = [100.0 * (time.process_time() - mark_cpu) / elapsed] if elapsed >= 0.2 else []
    for at, pct in reversed(beacon.seconds):
        if at < started:
            break
        if at <= ended + 1.0:
            used.append(pct)
    if not used:
        return None
    return {"avg": round(sum(used) / len(used)), "max": round(max(used))}


def window(started: float, ended: float) -> Optional[Dict[str, float]]:
    """``{"max", "p95"}`` of the loop's lag, in ms, between two wall times;
    ``None`` with no beacon on the running loop or no tick in the window."""
    beacon = _beacon()
    if beacon is None:
        return None
    # The tick that is due but has not woken: a run that ends right after
    # the loop was held reads its window before the beacon could say so.
    overdue = (time.perf_counter() - beacon.since - INTERVAL_S) * 1000
    lags = [overdue] if overdue > 0 else []
    for at, late in reversed(beacon.ticks):
        if at < started:
            break
        if at <= ended + INTERVAL_S:
            lags.append(late)
    if not lags:
        return None
    lags.sort()
    return {"max": round(lags[-1], 1), "p95": round(lags[int(0.95 * (len(lags) - 1))], 1)}
