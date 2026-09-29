"""``.parallel(max=N)`` caps how many items of a stream one consumer runs at once.

``max`` was stored on the stream policy and never read: the scheduler
dispatched every item immediately, exactly like a bare ``.parallel()``.
The cap is per consumer edge — sequential (the default) is the same gate
with a cap of 1 — and the graph-wide ``concurrency=`` is unchanged.
"""

from __future__ import annotations

import asyncio

import pytest

from operonx import END, START, Operon, graph, op


class Meter:
    """Counts how many ``work`` calls are inside their body at once."""

    def __init__(self):
        self.now = 0
        self.peak = 0

    async def enter_and_leave(self):
        self.now += 1
        self.peak = max(self.peak, self.now)
        # Long enough that every dispatched item is inside the body at
        # the same time; the assertion that matters (peak <= cap) does
        # not depend on it.
        await asyncio.sleep(0.02)
        self.now -= 1


METER = Meter()


@op
def items(n: int):
    for i in range(n):
        yield {"i": i}


@op
async def work(i: int) -> dict:
    await METER.enter_and_leave()
    return {"o": i}


@graph
def capped_2(n):
    it = items(n=n)
    w = work(i=it["i"].parallel(max=2))
    START >> it >> w >> END


@graph
def capped_3(n):
    it = items(n=n)
    w = work(i=it["i"].parallel(max=3))
    START >> it >> w >> END


@graph
def unbounded(n):
    it = items(n=n)
    w = work(i=it["i"].parallel())
    START >> it >> w >> END


@graph
def sequential(n):
    it = items(n=n)
    w = work(i=it["i"])
    START >> it >> w >> END


async def _peak(g, n=10):
    global METER
    METER = Meter()
    out = await Operon(g, params={"n": None}).run(inputs={"n": n})
    assert sorted(out["o"]) == list(range(n))  # every item still ran
    return METER.peak


@pytest.mark.parametrize("g, cap", [(capped_2, 2), (capped_3, 3)])
async def test_max_caps_items_in_flight(g, cap):
    assert await _peak(g) == cap


async def test_unbounded_parallel_is_unchanged():
    assert await _peak(unbounded) == 10


async def test_sequential_default_is_unchanged():
    assert await _peak(sequential) == 1


async def test_cap_larger_than_the_stream_runs_everything_at_once():
    assert await _peak(capped_3, n=2) == 2


# ── an async generator: items arrive over time, the cap still holds ─────


@op
async def slow_items(n: int):
    for i in range(n):
        await asyncio.sleep(0.001)
        yield {"i": i}


@graph
def capped_slow_source(n):
    it = slow_items(n=n)
    w = work(i=it["i"].parallel(max=2))
    START >> it >> w >> END


async def test_cap_holds_while_the_source_is_still_yielding():
    assert await _peak(capped_slow_source, n=8) == 2
