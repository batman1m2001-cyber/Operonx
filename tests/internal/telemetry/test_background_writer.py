"""The background writer: a slow or dead sink never reaches the caller.

The gates:

* ``submit`` returns at once, whatever the sink does — hangs, raises,
  or is slow — and never raises itself;
* past the bound, items are dropped and counted, not queued;
* a failing batch is retried, then dropped and counted;
* drops and failures are logged once per outage, not once per item;
* batches respect the size and the interval; ``flush`` waits for what was
  submitted; ``close`` stops the thread.
"""

from __future__ import annotations

import logging
import threading
import time

import pytest

from operonx.telemetry.writer import BackgroundWriter


class Sink:
    def __init__(self, *, hang: threading.Event = None, fail: int = 0, delay: float = 0.0):
        self.batches = []
        self.hang = hang
        self.fail = fail
        self.delay = delay
        self.calls = 0

    def __call__(self, batch):
        self.calls += 1
        if self.hang is not None:
            self.hang.wait()
        if self.delay:
            time.sleep(self.delay)
        if self.fail:
            self.fail -= 1
            raise ConnectionError("clickhouse is down")
        self.batches.append(list(batch))


class _Records(logging.Handler):
    def __init__(self):
        super().__init__(logging.DEBUG)
        self.records = []

    def emit(self, record):
        self.records.append(record)


@pytest.fixture
def caplog():
    """operonx's logger does not propagate to the root, where pytest's own
    caplog listens — so listen on it directly."""
    logger = logging.getLogger("operonx.core")
    h = _Records()
    logger.addHandler(h)
    h.set_level = lambda *a, **k: None
    try:
        yield h
    finally:
        logger.removeHandler(h)


def _writer(sink, **kw):
    kw.setdefault("flush_interval", 0.02)
    kw.setdefault("retry_backoff", (0.001, 0.002))
    return BackgroundWriter(sink, name="test", **kw)


def test_submit_never_blocks_on_a_hanging_sink_and_drops_past_the_bound(caplog):
    gate = threading.Event()
    w = _writer(Sink(hang=gate), max_queue=10, batch_size=1)
    try:
        caplog.set_level(logging.WARNING, logger="operonx")
        start = time.perf_counter()
        results = [w.submit(i) for i in range(1000)]
        elapsed = time.perf_counter() - start
        assert elapsed < 0.5, f"1000 submits took {elapsed:.3f}s against a hung sink"
        # one in flight (taken by the hung thread) at most, ten queued
        assert sum(results) <= 11 and w.stats["dropped_full"] >= 989
        drops = [r for r in caplog.records if "dropped" in r.getMessage()]
        assert len(drops) == 1  # once, not 989 times
    finally:
        gate.set()
        w.close(timeout=2)


def test_submit_returns_fast_while_the_sink_is_slow():
    w = _writer(Sink(delay=0.2), max_queue=100, batch_size=1)
    try:
        worst = 0.0
        for i in range(50):
            t = time.perf_counter()
            w.submit(i)
            worst = max(worst, time.perf_counter() - t)
        assert worst < 0.01
    finally:
        w.close(timeout=0.1)


def test_a_failing_sink_is_retried_then_dropped_and_counted(caplog):
    caplog.set_level(logging.WARNING, logger="operonx")
    sink = Sink(fail=100)
    w = _writer(sink, max_retries=3, batch_size=5)
    try:
        for i in range(5):
            w.submit(i)
        assert w.flush(timeout=2)
        assert sink.calls == 4  # the first try and three retries
        assert w.stats["dropped_failed"] == 5 and w.stats["written"] == 0
        assert w.stats["failed_batches"] == 1
        for i in range(5):
            w.submit(i)
        assert w.flush(timeout=2)
        fails = [r for r in caplog.records if "failed" in r.getMessage()]
        assert len(fails) == 1  # one outage, one line
    finally:
        w.close(timeout=1)


def test_a_transient_failure_is_retried_and_lands_once_and_recovery_is_logged(caplog):
    caplog.set_level(logging.INFO, logger="operonx")
    sink = Sink(fail=2)
    w = _writer(sink, max_retries=3, batch_size=10)
    try:
        for i in range(3):
            w.submit(i)
        assert w.flush(timeout=2)
        assert sink.batches == [[0, 1, 2]] and w.stats["written"] == 3
        assert any("recovered" in r.getMessage() for r in caplog.records)
    finally:
        w.close()


def test_a_sink_exception_never_escapes_or_kills_the_thread():
    class Boom(Exception):
        pass

    calls = []

    def sink(batch):
        calls.append(batch)
        if len(calls) == 1:
            raise Boom("bad row")

    w = _writer(sink, max_retries=0, batch_size=1)
    try:
        w.submit("a")
        assert w.flush(timeout=2)
        w.submit("b")
        assert w.flush(timeout=2)
        assert calls[-1] == ["b"] and w.stats["written"] == 1 and w.stats["dropped_failed"] == 1
    finally:
        w.close()


def test_batches_follow_the_size_by_weight():
    sink = Sink()
    w = _writer(sink, batch_size=10, weight=lambda item: item, flush_interval=0.5)
    try:
        for n in (4, 4, 4, 4):
            w.submit(n)
        assert w.flush(timeout=3)
        sizes = [sum(b) for b in sink.batches]
        assert sum(sizes) == 16 and all(s <= 12 for s in sizes) and len(sink.batches) >= 2
    finally:
        w.close()


def test_a_lone_item_is_written_within_the_interval():
    sink = Sink()
    w = _writer(sink, batch_size=1000, flush_interval=0.05)
    try:
        w.submit("x")
        deadline = time.time() + 2
        while not sink.batches and time.time() < deadline:
            time.sleep(0.01)
        assert sink.batches == [["x"]]
    finally:
        w.close()


def test_flush_times_out_on_a_hung_sink_and_close_does_not_hang():
    gate = threading.Event()
    w = _writer(Sink(hang=gate), batch_size=1)
    w.submit(1)
    t = time.perf_counter()
    assert w.flush(timeout=0.1) is False
    w.close(timeout=0.1)
    assert time.perf_counter() - t < 1.0
    gate.set()
    assert w.submit(2) is False  # closed: nothing more is taken


def test_flush_with_nothing_pending_is_immediate():
    w = _writer(Sink())
    t = time.perf_counter()
    assert w.flush(timeout=5)
    assert time.perf_counter() - t < 0.01
    w.close()


def test_the_thread_starts_on_first_submit_only():
    w = _writer(Sink())
    assert w._thread is None
    w.submit(1)
    assert w._thread is not None and w._thread.daemon
    w.close()


def test_a_forked_child_gets_its_own_queue(monkeypatch):
    w = _writer(Sink())
    w.submit(1)
    w.flush(timeout=1)
    parent_thread = w._thread
    monkeypatch.setattr("os.getpid", lambda: -42)
    w.submit(2)
    assert w._thread is not parent_thread and w.flush(timeout=2)
    w.close()


@pytest.mark.parametrize("bad", [0, -1])
def test_bounds_are_checked(bad):
    with pytest.raises(ValueError):
        BackgroundWriter(lambda b: None, max_queue=bad)
