"""Every `stream()` mode raises the fatal error `run()` raises (C2).

`mode="updates"`, `"values"` and `"custom"` drain the run's frames in a
background task and pace themselves on something else. When the run died,
that task's exception was never retrieved: the loop read `done()` as
"finished", flushed, and ended cleanly — while `run()` and
`mode="frames"` raised. Same graph, success or failure by mode.
"""

import asyncio
import gc

import pytest

from operonx.checkpoint import ObserveBudgetExceeded
from operonx.core import END, PARENT, START, GraphOp, Operon, op

MODES = ["updates", "values", "custom", "frames"]


def _fatal():
    """`r8b.py`: the op's second write trips its observe budget."""
    with GraphOp(name="fatal_g") as g:
        PARENT.declare(count=0)

        @op(observe_max=1)
        def burst():
            return {"a": 1, "b": 2}

        b = burst(name="burst")
        b["a"] >> PARENT["count"]
        START >> b >> END
    return g


@op
def parse(x: str):
    return {"n": int(x)}


def _op_fails():
    with GraphOp(name="op_fails") as g:
        p = parse(x=PARENT["x"])
        START >> p >> END
    return g


async def test_run_raises_it():
    with pytest.raises(ObserveBudgetExceeded):
        await Operon(_fatal()).run(inputs={})


@pytest.mark.parametrize("mode", MODES)
async def test_every_mode_raises_it_and_leaves_no_task_unretrieved(mode):
    loop = asyncio.get_running_loop()
    unretrieved = []
    previous = loop.get_exception_handler()
    loop.set_exception_handler(lambda _loop, context: unretrieved.append(context))
    try:
        with pytest.raises(ObserveBudgetExceeded):
            async for _ in Operon(_fatal()).stream({}, mode=mode):
                pass
        await asyncio.sleep(0)
        gc.collect()
    finally:
        loop.set_exception_handler(previous)
    assert [c.get("message") for c in unretrieved] == []


@pytest.mark.parametrize("mode", MODES)
async def test_an_op_that_raises_ends_every_mode_cleanly(mode):
    """Only a fatal error raises. An op's own exception does not end
    `run()`, so it must not end a stream either."""
    async for _ in Operon(_op_fails()).stream({"x": "nope"}, mode=mode):
        pass
