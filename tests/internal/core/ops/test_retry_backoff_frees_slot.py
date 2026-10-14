"""A retry's backoff gives its concurrency slot back while it waits.

docs/RETRY_PLAN.md R1. Before it, an op waiting out ``Retry``'s backoff kept
its slot, so under ``max_concurrency=1`` a healthy op queued behind a sleeper
and, in an outage, every slot filled with ops doing nothing.
"""

import asyncio
import time

import pytest

from operonx import END, START, Operon, graph, op
from operonx.core.policy import _SLOTS, Retry, _Slots, backoff
from operonx.durable import MemoryJournal

pytestmark = pytest.mark.unit

WAIT = 0.3
LOG: list = []
RUNNING = {"now": 0, "most": 0}
TRIES = {"a": 0, "b": 0}


def _enter():
    RUNNING["now"] += 1
    RUNNING["most"] = max(RUNNING["most"], RUNNING["now"])


@op(retry=Retry(max_attempts=2, initial=WAIT, jitter=False))
async def flaky(x: int) -> dict:
    _enter()
    try:
        TRIES["a"] += 1
        LOG.append(("flaky", TRIES["a"], time.perf_counter()))
        await asyncio.sleep(0.01)
        if TRIES["a"] == 1:
            raise ConnectionError("503 from upstream")
        return {"a": x}
    finally:
        RUNNING["now"] -= 1


@op(retry=Retry(max_attempts=2, initial=WAIT, jitter=False))
async def flaky_too(x: int) -> dict:
    _enter()
    try:
        TRIES["b"] += 1
        await asyncio.sleep(0.01)
        if TRIES["b"] == 1:
            raise ConnectionError("503 from upstream")
        return {"b": x}
    finally:
        RUNNING["now"] -= 1


@op
async def healthy(x: int) -> dict:
    _enter()
    try:
        await asyncio.sleep(0.01)
        LOG.append(("healthy", 1, time.perf_counter()))
        return {"h": x}
    finally:
        RUNNING["now"] -= 1


@graph
def side_by_side(x):
    f = flaky(x=x)
    h = healthy(x=x)
    START >> f >> END
    START >> h >> END


@graph
def three(x):
    f = flaky(x=x)
    g = flaky_too(x=x)
    h = healthy(x=x)
    START >> f >> END
    START >> g >> END
    START >> h >> END


@pytest.fixture(autouse=True)
def _reset():
    LOG.clear()
    TRIES.update(a=0, b=0)
    RUNNING.update(now=0, most=0)


@pytest.mark.parametrize("journal", [None, MemoryJournal], ids=["plain", "durable"])
async def test_a_healthy_op_runs_while_a_retry_waits_at_max_concurrency_1(journal):
    kw = {"journal": journal()} if journal else {}
    engine = Operon(side_by_side, params={"x": None}, max_concurrency=1, **kw)
    out = await engine.run({"x": 4})
    assert out["a"] == 4 and out["h"] == 4 and "$errors" not in out
    at = {(name, n): t for name, n, t in LOG}
    # The healthy op ended inside the flaky op's backoff, not after it.
    assert at[("healthy", 1)] < at[("flaky", 2)]
    assert at[("flaky", 2)] - at[("flaky", 1)] >= WAIT


async def test_slots_given_back_are_taken_back_never_over_released():
    engine = Operon(three, params={"x": None}, max_concurrency=1)
    out = await engine.run({"x": 1})
    assert (out["a"], out["b"], out["h"]) == (1, 1, 1)
    assert RUNNING["most"] == 1  # two backoffs and a healthy op, one at a time


async def test_a_run_cancelled_during_a_backoff_ends_cleanly():
    engine = Operon(side_by_side, params={"x": None}, max_concurrency=1)
    handle = engine.start({"x": 1})
    await asyncio.sleep(WAIT / 2)  # the flaky op is waiting out its backoff
    handle.cancel()
    with pytest.raises(asyncio.CancelledError):
        await handle.result()
    assert TRIES["a"] == 1


async def test_backoff_outside_a_pump_is_a_plain_sleep():
    t = time.perf_counter()
    await backoff(0.02, "f", ("main",))
    assert time.perf_counter() - t >= 0.02


async def test_only_the_ops_own_pump_gives_its_slot_back():
    sem = asyncio.Semaphore(1)
    await sem.acquire()
    tasks: dict = {}
    _SLOTS.set(_Slots(sem, None, tasks))

    async def waits():
        await backoff(0.05, "f", ("main",))

    # A task an op body starts inherits the context; the slot is not its.
    other = asyncio.create_task(waits())
    await asyncio.sleep(0.02)
    assert sem.locked()
    await other

    # The pump running the op at that ctx gives it back while it waits.
    pump = asyncio.create_task(waits())
    tasks[("main",)] = {"f": pump}
    await asyncio.sleep(0.02)
    assert not sem.locked()
    await pump
    assert sem.locked()  # and took it back
