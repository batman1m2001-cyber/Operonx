"""``Operon(g, errors="raise")``: the first op failure ends the run.

R1 in docs/roadmap/ROADMAP.md, row 7 of track2_langgraph_gap.md. The default
stays ``errors="record"`` — a live session must outlive one failing op — but
a job, a test or a batch script wants the failure, not a result missing a
key.
"""

import asyncio
import time

import pytest

from operonx import END, PARENT, START, Operon, OpFailed, Retry, Timeout, graph, op
from operonx.core.ops import if_

SEEN = {"slow_done": 0, "after": 0}


@op
async def bad(x: int) -> dict:
    raise ValueError(f"cannot use {x}")


@op
async def slow_sibling(x: int) -> dict:
    await asyncio.sleep(1.0)
    SEEN["slow_done"] += 1
    return {"s": x}


@op
def after(y: int = 0) -> dict:
    SEEN["after"] += 1
    return {"z": y}


@graph
def fan(x):
    b = bad(x=x)
    s = slow_sibling(x=x)
    START >> [b, s]
    [b, s] >> END


async def test_errors_raise_mode_cancels_siblings():
    SEEN["slow_done"] = 0
    engine = Operon(fan, params={"x": None}, errors="raise")
    t0 = time.perf_counter()
    with pytest.raises(OpFailed) as caught:
        await engine.run({"x": 3})
    assert time.perf_counter() - t0 < 0.5

    failed = caught.value
    assert failed.op == f"{engine.name}.b"
    assert "ValueError: cannot use 3" in failed.error
    assert isinstance(failed.__cause__, ValueError)
    assert "cannot use 3" in str(failed)

    await asyncio.sleep(1.2)
    assert SEEN["slow_done"] == 0  # cancelled, not left to finish


async def test_errors_record_is_the_default():
    SEEN["slow_done"] = 0
    out = await Operon(fan, params={"x": None}).run({"x": 3})
    assert out["s"] == 3
    assert "cannot use 3" in str(out["$errors"])
    assert SEEN["slow_done"] == 1


@op
def sync_bad(x: int) -> dict:
    raise KeyError("missing")


@graph
def g(x):
    b = sync_bad(x=x)
    a = after(y=b["y"])
    START >> b >> a >> END


async def test_errors_raise_from_an_inline_op():
    SEEN["after"] = 0
    with pytest.raises(OpFailed, match="missing"):
        await Operon(g, params={"x": None}, errors="raise").run({"x": 1})
    assert SEEN["after"] == 0


@graph
def inner(x):
    b = bad(x=x)
    START >> b >> END


@graph
def outer(x):
    sub = inner(x=x)
    s = slow_sibling(x=x)
    START >> [sub, s]
    [sub, s] >> END


async def test_errors_raise_inside_subgraph():
    SEEN["slow_done"] = 0
    engine = Operon(outer, params={"x": None}, errors="raise")
    with pytest.raises(OpFailed) as caught:
        await engine.run({"x": 1})
    assert caught.value.op == f"{engine.name}.sub.b"
    await asyncio.sleep(1.2)
    assert SEEN["slow_done"] == 0


#: The arguments ``down`` was called with.
DOWN_CALLS = []


@op(retry=Retry(max_attempts=3, initial=0.01, jitter=False))
async def down(x: int) -> dict:
    DOWN_CALLS.append(x)
    raise ConnectionError("refused")


@graph
def down_graph(x):
    d = down(x=x)
    START >> d >> END


async def test_errors_raise_after_the_last_retry():
    DOWN_CALLS.clear()
    with pytest.raises(OpFailed, match="refused"):
        await Operon(down_graph, params={"x": None}, errors="raise").run({"x": 1})
    assert len(DOWN_CALLS) == 3


async def test_errors_raise_through_stream_and_handle():
    engine = Operon(fan, params={"x": None}, errors="raise")
    with pytest.raises(OpFailed):
        async for _ in engine.stream({"x": 1}, mode="updates"):
            pass
    handle = engine.start({"x": 1})
    with pytest.raises(OpFailed):
        await handle.result()
    assert list(handle.errors) == [f"{engine.name}.b"]


@op
def step(n: int) -> dict:
    return {"n": n + 1, "done": False}


@graph
def spin():
    PARENT.declare(n=0)
    s = step(n=PARENT["n"])
    s["n"] >> PARENT["n"]
    START >> s >> if_(s["done"] == True, END, max_iterations=3).else_(s)  # noqa: E712


async def test_errors_raise_on_loop_limit():
    with pytest.raises(OpFailed, match="LoopLimitExceeded"):
        await Operon(spin, errors="raise").run({})


def test_errors_mode_is_checked():
    with pytest.raises(ValueError, match="'record' or 'raise'"):
        Operon(fan, params={"x": None}, errors="ignore")


@op
async def stalls(x: int) -> dict:
    await asyncio.sleep(5)
    return {"y": x}


@graph
def stalling(x):
    s = stalls(x=x)
    START >> s >> END


@graph
def timed_out_subgraph(x):
    sub = stalling(x=x, timeout=Timeout(run=0.1))
    START >> sub >> END


async def test_errors_raise_on_a_subgraph_timeout():
    engine = Operon(timed_out_subgraph, params={"x": None}, errors="raise")
    with pytest.raises(OpFailed) as caught:
        await engine.run({"x": 1})
    assert caught.value.op == f"{engine.name}.sub"
    assert isinstance(caught.value.__cause__, TimeoutError)
