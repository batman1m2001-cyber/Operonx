"""A background writer that a slow or dead backend cannot reach through.

A trace consumer runs once per run, on the run's own path: a voice call
must not wait on a database, and must never fail because one is down.
:class:`BackgroundWriter` sits between the two:

* :meth:`~BackgroundWriter.submit` puts an item on a bounded queue and
  returns. It does no I/O, never blocks, and never raises.
* One daemon thread takes items in batches, by count (or by a ``weight``
  per item, such as rows) up to ``batch_size``, or whatever has arrived
  after ``flush_interval``, and hands each batch to the ``sink``.
* Past ``max_queue`` waiting items, new ones are **dropped and counted**
  (``stats["dropped_full"]``), so a backend that is down costs bounded
  memory, not a growing queue.
* A batch the sink raises on is retried ``max_retries`` times with
  backoff, then dropped and counted (``stats["dropped_failed"]``).
* An outage is logged once: the first drop or failure logs a warning, and
  the first write that succeeds afterwards logs what was lost.

``flush()`` waits for what was submitted; ``close()`` stops the thread.
An ``atexit`` hook flushes for ``exit_timeout`` seconds so a short
script's last items are written. A forked child starts its own queue and
thread on its first submit.
"""

from __future__ import annotations

import atexit
import os
import threading
import time
import weakref
from collections import deque
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from operonx.core.loggings import LOGGER

__all__ = ["BackgroundWriter"]


class _State:
    """Everything one process's writer thread shares with ``submit``. A
    fork gets a fresh one; the parent's thread keeps the old one."""

    def __init__(self) -> None:
        self.pid = os.getpid()
        self.queue: deque = deque()
        self.cond = threading.Condition(threading.Lock())
        self.pending = 0  # submitted, not yet written or dropped
        self.flushing = 0
        self.thread: Optional[threading.Thread] = None
        self.stop = threading.Event()


class BackgroundWriter:
    """See the module docstring."""

    def __init__(
        self,
        sink: Callable[[List[Any]], Any],
        *,
        name: str = "writer",
        max_queue: int = 1000,
        batch_size: int = 1000,
        flush_interval: float = 1.0,
        weight: Optional[Callable[[Any], int]] = None,
        max_retries: int = 3,
        retry_backoff: Tuple[float, float] = (0.5, 30.0),
        exit_timeout: float = 5.0,
    ):
        if int(max_queue) < 1:
            raise ValueError(f"max_queue must be >= 1, got {max_queue}")
        if int(batch_size) < 1:
            raise ValueError(f"batch_size must be >= 1, got {batch_size}")
        self.sink = sink
        self.name = name
        self.max_queue = int(max_queue)
        self.batch_size = int(batch_size)
        self.flush_interval = max(0.0, float(flush_interval))
        self.weight = weight
        self.max_retries = max(0, int(max_retries))
        self.retry_backoff = (float(retry_backoff[0]), float(retry_backoff[1]))
        self.exit_timeout = float(exit_timeout)
        self.stats: Dict[str, int] = {
            "submitted": 0,
            "written": 0,
            "dropped_full": 0,
            "dropped_failed": 0,
            "failed_batches": 0,
        }
        self._closed = False
        self._outage = False  # a drop or a failure since the last good write
        self._lost_mark = 0
        self._st = _State()
        ref = weakref.ref(self)
        atexit.register(lambda: (lambda w: w and w._at_exit())(ref()))

    # -- the caller's side -------------------------------------------------

    @property
    def _thread(self) -> Optional[threading.Thread]:
        return self._st.thread

    @property
    def queued(self) -> int:
        """Items waiting, not counting a batch being written."""
        return len(self._st.queue)

    def submit(self, item: Any) -> bool:
        """Queue *item*; ``False`` when it was dropped (full, or closed)."""
        try:
            st = self._st
            if st.pid != os.getpid():
                st = self._st = _State()
            if self._closed:
                return False
            with st.cond:
                if len(st.queue) >= self.max_queue:
                    self.stats["dropped_full"] += 1
                    first, self._outage = not self._outage, True
                else:
                    st.queue.append(item)
                    st.pending += 1
                    self.stats["submitted"] += 1
                    st.cond.notify()
                    first = None
            if first is None:
                if st.thread is None:
                    self._start(st)
                return True
            if first:
                LOGGER.warning(
                    "%s: queue full (%d waiting); runs are being dropped until the "
                    "backend keeps up — counted in .stats",
                    self.name,
                    self.max_queue,
                )
            return False
        except Exception:  # noqa: BLE001 — the caller's run must never see this
            return False

    def flush(self, timeout: Optional[float] = None) -> bool:
        """Wait until everything submitted so far is written or dropped.
        ``False`` if *timeout* (seconds) ran out first."""
        st = self._st
        if st.pid != os.getpid():
            return True
        with st.cond:
            if st.pending == 0:
                return True
            st.flushing += 1
            st.cond.notify_all()
            try:
                end = None if timeout is None else time.monotonic() + timeout
                while st.pending > 0:
                    left = None if end is None else end - time.monotonic()
                    if left is not None and left <= 0:
                        return False
                    st.cond.wait(left)
                return True
            finally:
                st.flushing -= 1

    def close(self, timeout: float = 5.0) -> None:
        """Write what is queued (up to *timeout*), then stop the thread.
        Nothing is accepted afterwards. Idempotent."""
        if self._closed:
            return
        self._closed = True
        st = self._st
        self.flush(timeout)
        st.stop.set()
        with st.cond:
            st.cond.notify_all()
        if st.thread is not None and st.thread is not threading.current_thread():
            st.thread.join(min(timeout, 1.0))

    def _at_exit(self) -> None:
        if not self._closed and self._st.pending:
            self.flush(self.exit_timeout)

    # -- the thread's side -------------------------------------------------

    def _start(self, st: _State) -> None:
        with st.cond:
            if st.thread is not None:
                return
            st.thread = threading.Thread(
                target=self._run, args=(st,), name=f"operonx-{self.name}", daemon=True
            )
        st.thread.start()

    def _weigh(self, item: Any) -> int:
        if self.weight is None:
            return 1
        try:
            return max(1, int(self.weight(item)))
        except Exception:  # noqa: BLE001
            return 1

    def _take(self, st: _State) -> Optional[List[Any]]:
        """The next batch, or ``None`` when stopped with nothing left."""
        with st.cond:
            while not st.queue and not st.stop.is_set():
                st.cond.wait()
            if not st.queue:
                return None
            deadline = time.monotonic() + self.flush_interval
            batch: List[Any] = []
            weight = 0
            while True:
                while st.queue and weight < self.batch_size:
                    item = st.queue.popleft()
                    batch.append(item)
                    weight += self._weigh(item)
                if weight >= self.batch_size or st.flushing or st.stop.is_set():
                    return batch
                left = deadline - time.monotonic()
                if left <= 0:
                    return batch
                st.cond.wait(left)

    def _run(self, st: _State) -> None:
        while True:
            batch = self._take(st)
            if batch is None:
                return
            try:
                self._write(batch, st)
            finally:
                with st.cond:
                    st.pending -= len(batch)
                    st.cond.notify_all()

    def _write(self, batch: Sequence[Any], st: _State) -> None:
        attempt = 0
        while True:
            try:
                self.sink(list(batch))
            except Exception as exc:  # noqa: BLE001
                if not self._outage:
                    self._outage = True
                    LOGGER.warning(
                        "%s: write failed (%s: %s); retrying, then dropping — "
                        "logged once until it recovers",
                        self.name,
                        type(exc).__name__,
                        exc,
                    )
                attempt += 1
                if attempt > self.max_retries or st.stop.is_set():
                    self.stats["failed_batches"] += 1
                    self.stats["dropped_failed"] += len(batch)
                    return
                lo, hi = self.retry_backoff
                st.stop.wait(min(hi, lo * (2 ** (attempt - 1))))
                continue
            self.stats["written"] += len(batch)
            if self._outage:
                self._outage = False
                lost = self.stats["dropped_full"] + self.stats["dropped_failed"]
                LOGGER.warning(
                    "%s: recovered; %d runs lost during the outage (%d at the bound, "
                    "%d unwritable)",
                    self.name,
                    lost - self._lost_mark,
                    self.stats["dropped_full"],
                    self.stats["dropped_failed"],
                )
                self._lost_mark = lost
            return
