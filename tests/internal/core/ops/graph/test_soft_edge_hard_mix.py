"""One hard edge plus two or more ``~`` soft edges into the same op.

The soft edges into an op count as **one** arrival between them: the op
waits for every hard edge and for the first soft edge, and ignores the
later soft arrivals. The ready count used to decrement once per soft
arrival, so two soft arrivals were worth two hard ones and fired the op
before its hard edge had landed — with ``None`` for the hard input.
"""

from __future__ import annotations

import asyncio

from operonx import END, START, Operon, graph, op

ORDER: list = []


@op
async def hard_src() -> dict:
    await asyncio.sleep(0.05)
    ORDER.append("hard")
    return {"h": "H"}


@op
async def soft_a() -> dict:
    ORDER.append("a")
    return {"v": "A"}


@op
async def soft_b() -> dict:
    ORDER.append("b")
    return {"v": "B"}


@op
async def soft_c() -> dict:
    ORDER.append("c")
    return {"v": "C"}


@op
def merge(h: str = None, a: str = None, b: str = None, c: str = None) -> dict:
    ORDER.append("merge")
    return {"got_hard": h}


@graph
def one_hard_two_soft():
    h, a, b = hard_src(), soft_a(), soft_b()
    m = merge(h=h["h"], a=a["v"], b=b["v"])
    START >> [h, a, b]
    h >> m
    a >> ~m
    b >> ~m
    m >> END


@graph
def one_hard_three_soft():
    h, a, b, c = hard_src(), soft_a(), soft_b(), soft_c()
    m = merge(h=h["h"], a=a["v"], b=b["v"], c=c["v"])
    START >> [h, a, b, c]
    h >> m
    a >> ~m
    b >> ~m
    c >> ~m
    m >> END


async def test_waits_for_the_hard_edge_with_two_soft_edges():
    ORDER.clear()
    out = await Operon(one_hard_two_soft).run(inputs={})
    assert out["got_hard"] == "H"
    assert ORDER.index("hard") < ORDER.index("merge")
    assert ORDER.count("merge") == 1


async def test_waits_for_the_hard_edge_with_three_soft_edges():
    ORDER.clear()
    out = await Operon(one_hard_three_soft).run(inputs={})
    assert out["got_hard"] == "H"
    assert ORDER.index("hard") < ORDER.index("merge")
    assert ORDER.count("merge") == 1


# ── a soft race with no hard edge still fires on the first arrival ──────

RACE: list = []


@op
async def fast() -> dict:
    return {"v": "fast"}


@op
async def slow() -> dict:
    await asyncio.sleep(0.05)
    RACE.append("slow finished")
    return {"v": "slow"}


@op
def first(f: str = None, s: str = None) -> dict:
    RACE.append("first")
    return {"winner": f or s}


@graph
def race():
    f, s = fast(), slow()
    r = first(f=f["v"], s=s["v"])
    START >> [f, s]
    f >> ~r
    s >> ~r
    r >> END


async def test_all_soft_still_fires_once_on_the_first_arrival():
    RACE.clear()
    out = await Operon(race).run(inputs={})
    assert out["winner"] == "fast"
    assert RACE == ["first", "slow finished"]
