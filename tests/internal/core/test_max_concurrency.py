"""``Operon(g, max_concurrency=N)``: one cap for the whole run, nested graphs included.

R1 in docs/roadmap/ROADMAP.md, row 19 of track2_langgraph_gap.md. Each graph
has its own ``concurrency`` semaphore, so nested graphs multiply: probe P10
ran 4 leaf ops at once under ``concurrency=2`` at both levels.
"""

import asyncio

import pytest

from operonx import END, START, Operon, graph, op

LIVE = {"now": 0, "max": 0}


def _reset():
    LIVE["now"] = LIVE["max"] = 0


@op
def fan(n: int):
    for i in range(n):
        yield {"i": i}


@op
async def work(i: int) -> dict:
    LIVE["now"] += 1
    LIVE["max"] = max(LIVE["max"], LIVE["now"])
    await asyncio.sleep(0.02)
    LIVE["now"] -= 1
    return {"r": i}


@graph
def inner_fan(n):
    f = fan(n=n)
    w = work(i=f["i"].parallel())
    START >> f >> w >> END


@op
def outer_src(m: int):
    for _ in range(m):
        yield {"n": 4}


@graph
def outer_fan(m):
    s = outer_src(m=m)
    sub = inner_fan(n=s["n"].parallel(), concurrency=2)
    START >> s >> sub >> END


async def test_nested_graphs_multiply_without_a_shared_cap():
    _reset()
    await Operon(outer_fan(m=None, concurrency=2)).run({"m": 4})
    assert LIVE["max"] == 4  # P10: 2 subgraphs x 2 leaf ops


async def test_nested_concurrency_shared_cap():
    _reset()
    out = await Operon(outer_fan(m=None, concurrency=2), max_concurrency=2).run({"m": 4})
    assert LIVE["max"] <= 2
    assert sorted(out["r"]) == sorted([0, 1, 2, 3] * 4)


async def test_one_slot_does_not_deadlock_on_nested_graphs():
    """A subgraph holds no slot of its own: it would hold it while its ops wait for one."""
    _reset()
    out = await asyncio.wait_for(
        Operon(outer_fan(m=None, concurrency=4), max_concurrency=1).run({"m": 3}), 5
    )
    assert LIVE["max"] == 1
    assert len(out["r"]) == 12


@op
async def produce(n: int):
    for i in range(n):
        yield {"i": i}


@op
async def consume(i: int) -> dict:
    await asyncio.sleep(0.001)
    return {"o": i}


@graph
def bounded(n):
    p = produce(n=n)
    c = consume(i=p["i"].sequential(max_pending=2))
    START >> p >> c >> END


async def test_one_slot_with_a_bounded_edge_does_not_deadlock():
    """A producer parked on a full edge gives its shared slot back, as it does its graph slot."""
    out = await asyncio.wait_for(
        Operon(bounded, params={"n": None}, max_concurrency=1).run({"n": 20}), 5
    )
    assert out["o"] == list(range(20))


@pytest.mark.parametrize("bad", [0, -1, 1.5, "4", True])
def test_max_concurrency_is_checked(bad):
    with pytest.raises(ValueError, match="max_concurrency"):
        Operon(bounded, params={"n": None}, max_concurrency=bad)
