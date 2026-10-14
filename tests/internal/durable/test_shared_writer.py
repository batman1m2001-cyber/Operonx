"""One writer per journal, shared by its runs (GAP_PLAN L2b)."""

from __future__ import annotations

import concurrent.futures

import pytest

from operonx.durable import MemoryJournal
from operonx.durable.journal import RunHeader
from operonx.durable.recorder import _SharedWriter

pytestmark = pytest.mark.unit


def test_a_cancelled_waiter_does_not_stop_the_writer():
    # a stopped run cancels the future it awaited: the writer must skip it,
    # settle the rest of its batch, and keep writing
    journal = MemoryJournal()
    journal.open_run(RunHeader("a", "f"))
    journal.open_run(RunHeader("b", "f"))
    writer = _SharedWriter(journal)
    gone = concurrent.futures.Future()
    gone.cancel()
    writer._put(("append", "a", [], gone))
    end_a = writer.finish("a", "ok")
    end_b = writer.finish("b", "ok")
    end_a.result(timeout=5)
    end_b.result(timeout=5)
    assert writer._thread is not None and writer._thread.is_alive()
    assert {h.run_id: h.status for h in journal.runs()} == {"a": "ok", "b": "ok"}


def test_a_failed_run_fails_alone():
    class Refuses(MemoryJournal):
        def append(self, run_id, steps):
            if run_id == "bad":
                raise OSError("disk full")
            super().append(run_id, steps)

    journal = Refuses()
    for run in ("bad", "good"):
        journal.open_run(RunHeader(run, "f"))
    writer = _SharedWriter(journal)
    bad = writer.append("bad", ["step"], wait=True)
    good = writer.finish("good", "ok")
    with pytest.raises(Exception, match="disk full"):
        bad.result(timeout=5)
    good.result(timeout=5)
    assert isinstance(bad, concurrent.futures.Future)
