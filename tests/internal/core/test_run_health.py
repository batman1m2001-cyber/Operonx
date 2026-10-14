"""What slowed a run down, beside its ops' own time, is on its trace
(docs/CCU_CAPACITY_PLAN.md, phase 1).

A callbot load test lost 5–35 s per reply to packets queued in front of
a busy consumer, and nothing in the traces said so: every op looked fast.
Now the run reports the edges whose items queued (``metadata["backlog"]``)
and, where a loop beacon runs, how late its event loop ran
(``metadata["loop_lag_ms"]``).
"""

from __future__ import annotations

import asyncio
import time

import pytest

from operonx import END, START, Operon, graph, op
from operonx.core import loop_lag
from operonx.telemetry.consumer import Consumer

pytestmark = pytest.mark.unit


class _Capture(Consumer):
    def __init__(self):
        super().__init__()
        self.traces = []

    def consume(self, trace):
        self.traces.append(trace)


async def _traced(capture):
    for _ in range(200):  # consumers run after the handle ends, in a thread
        if capture.traces:
            return capture.traces[0]
        await asyncio.sleep(0.01)
    raise AssertionError("no trace")


@op
async def burst(n: int):
    for i in range(n):
        yield {"i": i}


@op
async def slow(i: int) -> dict:
    await asyncio.sleep(0.02)
    return {"o": i}


@op
async def quick(i: int) -> dict:
    return {"o": i}


@graph
def backed_up(n):
    b = burst(n=n)
    s = slow(i=b["i"])
    START >> b >> s >> END


@graph
def keeping_up(n):
    b = burst(n=n)
    q = quick(i=b["i"])
    START >> b >> q >> END


@op
async def plain(x: int) -> dict:
    return {"y": x + 1}


@graph
def single(x):
    p = plain(x=x)
    START >> p >> END


@op
def hold_the_loop(y: int) -> dict:
    time.sleep(0.2)  # a sync op runs on the loop: it holds it
    return {"z": y}


@graph
def blocking(x):
    p = plain(x=x)
    h = hold_the_loop(y=p["y"])
    START >> p >> h >> END


async def test_items_queued_on_an_edge_are_reported_with_their_wait():
    capture = _Capture()
    await Operon(backed_up, params={"n": None}, trace=[capture]).start(inputs={"n": 10}).collect()
    trace = await _traced(capture)
    (edge,) = trace.metadata["backlog"]
    assert edge == "backed_up.b -> backed_up.s"
    seen = trace.metadata["backlog"][edge]
    # the burst parks 9 behind the first; the last waits for 9 consumers
    assert seen["max"] == 9
    assert seen["max_wait_ms"] >= 150


async def test_a_run_that_never_queued_reports_no_backlog():
    capture = _Capture()
    await Operon(single, params={"x": None}, trace=[capture]).run({"x": 1})
    assert "backlog" not in (await _traced(capture)).metadata


async def test_one_item_waiting_behind_one_running_is_not_a_backlog():
    capture = _Capture()
    await Operon(keeping_up, params={"n": None}, trace=[capture]).start(inputs={"n": 2}).collect()
    assert "backlog" not in (await _traced(capture)).metadata


async def test_no_loop_lag_without_a_beacon():
    capture = _Capture()
    await Operon(blocking, params={"x": None}, trace=[capture]).run({"x": 1})
    assert "loop_lag_ms" not in (await _traced(capture)).metadata


async def test_a_held_loop_shows_as_loop_lag():
    loop_lag.start()
    await asyncio.sleep(loop_lag.INTERVAL_S * 2)  # a tick or two before the run
    capture = _Capture()
    await Operon(blocking, params={"x": None}, trace=[capture]).run({"x": 1})
    lag = (await _traced(capture)).metadata["loop_lag_ms"]
    assert lag["max"] >= 150, lag  # the 200 ms sleep, seen by the beacon


def test_the_window_is_empty_outside_a_loop():
    assert loop_lag.window(0, time.time()) is None
