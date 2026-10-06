"""A ``.collect()`` inside a subgraph hands its result up once.

The scheduler listed the collect's context twice among the contexts the
subgraph reports: once when the buffer was flushed, and again when the
consumer's frame arrived there, because nothing had seeded that context's
ready counts and so it looked like a fresh stream item. The subgraph then
yielded — and stored — the same result twice. An op after the subgraph
ran once all the same (its ready count was already spent), so a minimal
"generator → op → collect" subgraph looked right; a subgraph output
written straight into a reducer cell — the agent's tool messages into
``messages`` — got every value twice.
"""

from __future__ import annotations

import asyncio

import pytest

from operonx import END, PARENT, START, Operon, graph, op
from operonx.core.ops.flow.branch_op import if_

pytestmark = pytest.mark.unit


def append(a, b):
    return (a or []) + (b or [])


@op
def numbers(n: int = 0):
    for i in range(n):
        yield {"x": i}


@op
async def double(x: int = 0) -> dict:
    await asyncio.sleep(0.001)
    return {"y": x * 2}


@op
def as_list(ys: list = None) -> dict:
    return {"out": list(ys or [])}


@graph
def from_the_generator(n):
    g = numbers(n=n)
    c = as_list(ys=g["x"].collect())
    START >> g >> c >> END


@graph
def behind_a_parallel_op(n):
    g = numbers(n=n)
    d = double(x=g["x"].parallel(max=8))
    c = as_list(ys=d["y"].collect())
    START >> g >> d >> c >> END


@op
def size(ys: list = None) -> dict:
    return {"size": len(ys or [])}


@graph
def two_collects_off_one_stream(n):
    g = numbers(n=n)
    c = as_list(ys=g["x"].collect())
    k = size(ys=g["x"].collect())
    START >> g >> [c, k]
    [c, k] >> END


@graph
def into_a_cell(n, sub):
    PARENT.declare(log=[], reducers={"log": append})
    s = sub(n=n)
    s["out"] >> PARENT["log"]
    START >> s >> END


@pytest.mark.parametrize(
    "sub, expected",
    [
        (from_the_generator, [0, 1, 2]),
        (behind_a_parallel_op, [0, 2, 4]),
        (two_collects_off_one_stream, [0, 1, 2]),
    ],
    ids=["from_the_generator", "behind_a_parallel_op", "two_collects_off_one_stream"],
)
async def test_written_into_a_reducer_cell_once(sub, expected):
    built = into_a_cell(n=None, sub=sub)
    out = await Operon(built).run(inputs={"n": 3})
    assert "$errors" not in out
    assert out["$state"][built.full_name, "log"] == expected


# ── the agent's shape: inside a back-edge loop, written into the cell ───


@op
def count(turns: int = 0) -> dict:
    turns = (turns or 0) + 1
    return {"turns": turns, "n": 3 if turns <= 2 else 0}


@op
def stop_when_empty(n: int = 0) -> dict:
    return {"stop": n == 0}


@op
def finish() -> dict:
    return {"done": True}


@graph
def looping(n):
    PARENT.declare(turns=0, log=[], reducers={"log": append})
    c = count(turns=PARENT["turns"])
    c["turns"] >> PARENT["turns"]
    d = stop_when_empty(n=c["n"])
    s = behind_a_parallel_op(n=c["n"])
    s["out"] >> PARENT["log"]
    f = finish()
    START >> c >> d
    d >> if_(d["stop"] == True, f).else_(s)  # noqa: E712
    f >> END
    s >> c  # back-edge


async def test_inside_a_loop_each_iteration_writes_once():
    built = looping(n=None)
    out = await Operon(built).run(inputs={"n": 0})
    assert "$errors" not in out
    assert out["$state"][built.full_name, "log"] == [0, 2, 4, 0, 2, 4]
