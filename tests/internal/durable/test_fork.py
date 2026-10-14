"""fork(run_id, at=op, outputs=...): run a journalled run again from one op,
as if it had answered otherwise (docs/N8N_LANGGRAPH_GAP_PLAN.md L4) — what
ended before it is not run again, what follows it runs with the new value,
and the original run is not changed."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

from operonx import Operon
from operonx.durable import JournalError, MemoryJournal, SqliteJournal
from operonx.telemetry.consumer import Consumer

sys.path.insert(0, str(Path(__file__).parent))
from test_park import approval  # noqa: E402
from test_retry_failed import BROKEN, CALLS, order  # noqa: E402

pytestmark = pytest.mark.unit


class _Capture(Consumer):
    def __init__(self):
        super().__init__()
        self.traces = []

    def consume(self, trace):
        self.traces.append(trace)


async def _traced(capture, n):
    for _ in range(200):
        if len(capture.traces) >= n:
            return capture.traces[n - 1]
        await asyncio.sleep(0.01)
    raise AssertionError(f"no trace #{n}")


@pytest.fixture(autouse=True)
def _working():
    CALLS.clear()
    BROKEN["on"] = False


@pytest.mark.parametrize("journal", ["memory", "sqlite"])
async def test_a_fork_runs_what_follows_with_the_new_output(journal, tmp_path):
    journal = SqliteJournal(tmp_path / "j.db") if journal == "sqlite" else MemoryJournal()
    capture = _Capture()
    engine = Operon(order, params={"order": None}, journal=journal, trace=[capture])
    out = await engine.run({"order": 7}, run_id="o")
    assert out["done"] == "R-7:shipped" and CALLS == ["charge", "ship"]
    before = journal.read("o")[1]

    handle = await engine.fork("o", at="c", outputs={"receipt": "R-99"})
    forked = await handle.result()
    assert forked["done"] == "R-99:shipped"
    assert CALLS == ["charge", "ship", "ship"]  # charged once: `c` was replayed
    new_id = handle.trace.trace_id
    assert new_id.startswith("o~") and new_id != "o"

    # the original is as it was
    assert journal.read("o")[1] == before
    assert [h.status for h in journal.runs() if h.run_id == "o"] == ["ok"]

    trace = await _traced(capture, 2)
    assert trace.trace_id == new_id
    assert trace.metadata["forked_from"] == "o" and trace.metadata["fork_at"] == "c@main"
    (c,) = [n for n in trace.nodes if n.op_name == "c"]
    assert c.attrs["operonx.replayed"] is True and c.outputs == {"receipt": "R-99"}
    assert [n.op_name for n in trace.nodes] == ["c", "s"]


async def test_a_fork_at_the_last_op_changes_the_runs_answer():
    # the op's output also went to the graph's own cell: the copy changes too
    engine = Operon(order, params={"order": None}, journal=MemoryJournal())
    await engine.run({"order": 7}, run_id="o")
    handle = await engine.fork("o", at="s", outputs={"done": "lost in transit"})
    assert (await handle.result())["done"] == "lost in transit"
    assert CALLS == ["charge", "ship"]  # nothing ran again


async def test_a_fork_is_refused_what_it_cannot_mean():
    engine = Operon(order, params={"order": None}, journal=MemoryJournal())
    await engine.run({"order": 7}, run_id="o")
    with pytest.raises(JournalError, match="no op 'nope'"):
        await engine.fork("o", at="nope", outputs={})
    with pytest.raises(JournalError, match="has no output 'price'"):
        await engine.fork("o", at="c", outputs={"price": 1})
    with pytest.raises(JournalError, match="did not end there 2 time"):
        await engine.fork("o", at="c", outputs={"receipt": "x"}, occurrence=1)
    with pytest.raises(JournalError, match="no run"):
        await engine.fork("missing", at="c", outputs={"receipt": "x"})


async def test_a_fork_of_a_parked_run_asks_again_after_the_changed_step():
    engine = Operon(approval, params={"x": None}, journal=MemoryJournal())
    out = await engine.run({"x": 7}, run_id="r")
    assert out["$interrupted"][0]["payload"] == "plan-7"
    handle = await engine.fork("r", at="p", outputs={"plan": "plan-8"})
    forked = await handle.result()
    assert forked["$interrupted"][0]["payload"] == "plan-8"


async def test_a_resume_with_no_trace_kept_records_what_it_replayed():
    # a process killed outright keeps no trace: its resume still shows the run
    journal, capture = MemoryJournal(), _Capture()
    engine = Operon(order, params={"order": None}, journal=journal, trace=[capture])
    BROKEN["on"] = True
    await engine.run({"order": 7}, run_id="o")
    journal._traces.clear()  # as if it had never stopped cleanly
    BROKEN["on"] = False
    await (await engine.resume("o", retry_failed=True)).result()
    trace = await _traced(capture, 2)
    assert [(n.op_name, n.status, n.attrs.get("operonx.replayed")) for n in trace.nodes] == [
        ("c", "ok", True),
        ("s", "ok", None),
    ]
