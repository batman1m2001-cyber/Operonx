"""resume(retry_failed=True): a failed run runs again from its first failed
op (docs/N8N_LANGGRAPH_GAP_PLAN.md L1) — what had ended before the failure
is not run again."""

from __future__ import annotations

import pytest

from operonx import END, START, Operon, graph, op
from operonx.durable import JournalError, MemoryJournal

pytestmark = pytest.mark.unit

CALLS: list = []
BROKEN = {"on": True}


@op
async def charge(order: int) -> dict:
    CALLS.append("charge")  # a side effect that must not happen twice
    return {"receipt": f"R-{order}"}


@op
async def ship(receipt: str) -> dict:
    CALLS.append("ship")
    if BROKEN["on"]:
        raise RuntimeError("the courier is down")
    return {"done": f"{receipt}:shipped"}


@graph
def order(order):
    c = charge(order=order)
    s = ship(receipt=c["receipt"])
    START >> c >> s >> END


@pytest.fixture(autouse=True)
def _reset():
    CALLS.clear()
    BROKEN["on"] = True


async def _failed_run(engine: Operon, run_id: str) -> dict:
    out = await engine.run({"order": 7}, run_id=run_id)
    assert "done" not in out and out.get("$errors"), out
    assert CALLS == ["charge", "ship"]
    return out


async def test_retry_failed_runs_the_failed_op_again_and_not_what_ended_before():
    engine = Operon(order, params={"order": None}, journal=MemoryJournal())
    await _failed_run(engine, "o-1")
    BROKEN["on"] = False  # the courier is back (or the bug is fixed)
    handle = await engine.resume("o-1", retry_failed=True)
    out = await handle.result()
    assert out["done"] == "R-7:shipped"
    assert CALLS == ["charge", "ship", "ship"]  # charged once


async def test_a_plain_resume_keeps_the_failure():
    engine = Operon(order, params={"order": None}, journal=MemoryJournal())
    await _failed_run(engine, "o-2")
    BROKEN["on"] = False
    out = await (await engine.resume("o-2")).result()
    assert "done" not in out  # the failed op was replayed as it ended
    assert CALLS == ["charge", "ship"]


async def test_retry_failed_of_a_run_that_did_not_fail_is_refused():
    BROKEN["on"] = False
    engine = Operon(order, params={"order": None}, journal=MemoryJournal())
    out = await engine.run({"order": 7}, run_id="o-3")
    assert out["done"] == "R-7:shipped"
    with pytest.raises(JournalError, match="no op of the run failed"):
        await engine.resume("o-3", retry_failed=True)
