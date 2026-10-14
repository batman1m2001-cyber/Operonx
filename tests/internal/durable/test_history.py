"""A stopped run's trace says why it stopped, and its resume's trace is the
whole run (docs/N8N_LANGGRAPH_GAP_PLAN.md S1a, S1b)."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

from operonx import Operon
from operonx.durable import MemoryJournal, SqliteJournal
from operonx.telemetry.consumer import Consumer

sys.path.insert(0, str(Path(__file__).parent))
from test_park import approval  # noqa: E402
from test_retry_failed import BROKEN, order  # noqa: E402

pytestmark = pytest.mark.unit


class _Capture(Consumer):
    def __init__(self):
        super().__init__()
        self.traces = []

    def consume(self, trace):
        self.traces.append(trace)


async def _traced(capture, n):
    for _ in range(200):  # consumers run after the handle ends, in a thread
        if len(capture.traces) >= n:
            return capture.traces[n - 1]
        await asyncio.sleep(0.01)
    raise AssertionError(f"no trace #{n}")


def _ids(trace):
    return [n.op_id.split(".", 1)[1] for n in trace.nodes]


@pytest.mark.parametrize("journal", [MemoryJournal, "sqlite"])
async def test_a_parked_run_says_so_and_its_answer_keeps_the_whole_run(journal, tmp_path):
    journal = SqliteJournal(tmp_path / "j.db") if journal == "sqlite" else journal()
    capture = _Capture()
    engine = Operon(approval, params={"x": None}, journal=journal, trace=[capture])
    out = await engine.run({"x": 7}, run_id="r1")
    asked = out["$interrupted"][0]
    parked = await _traced(capture, 1)
    assert parked.status == "interrupted"
    assert parked.metadata["interrupted"] == [
        {"id": asked["interrupt_id"], "op": asked["op"], "payload": "plan-7"}
    ]
    assert sorted(_ids(parked)) == ["ask#main", "p#main", "s#main"]

    handle = await engine.resume("r1", answers={asked["interrupt_id"]: "yes"})
    assert (await handle.result())["done"] == "plan-7:yes"
    whole = await _traced(capture, 2)
    assert whole.trace_id == "r1" and whole.status == "ok"
    assert "interrupted" not in whole.metadata
    ids = _ids(whole)
    assert sorted(ids[:3]) == ["ask#main@stopped", "p#main", "s#main"]
    assert ids[3:] == ["ask#main", "a#main"]
    # the run starts when it first started: every node is inside it
    assert whole.wall_started_at == parked.wall_started_at
    assert all(
        whole.started_at <= n.start_time <= n.end_time <= whole.ended_at for n in whole.nodes
    )
    plan = next(n for n in whole.nodes if n.op_id.endswith("p#main"))
    assert plan.outputs == {"plan": "plan-7"}


async def test_a_retry_from_the_failure_keeps_the_failed_attempt():
    BROKEN["on"] = True
    capture = _Capture()
    engine = Operon(order, params={"order": None}, journal=MemoryJournal(), trace=[capture])
    await engine.run({"order": 7}, run_id="o-1")
    assert (await _traced(capture, 1)).status == "error"
    BROKEN["on"] = False
    handle = await engine.resume("o-1", retry_failed=True)
    assert (await handle.result())["done"] == "R-7:shipped"
    whole = await _traced(capture, 2)
    assert _ids(whole) == ["c#main", "s#main@stopped", "s#main"]
    assert [n.status for n in whole.nodes] == ["ok", "retried", "ok"]
    assert whole.status == "ok"


async def test_a_run_that_ends_keeps_no_trace():
    journal = MemoryJournal()
    BROKEN["on"] = False
    await Operon(order, params={"order": None}, journal=journal).run({"order": 1}, run_id="o")
    await asyncio.sleep(0.05)
    assert journal.load_trace("o") is None
