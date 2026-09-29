"""A fatal error raised while the scheduler drains its last inline ops.

``fatal`` was checked after the first drain and after each ``queue.get()``
— never after the drain that ends the main-loop body. When the event that
brings ``inflight`` to zero dispatches an inline op, and that op appends
to ``fatal``, the loop condition fails before anything looks at it: the
run returned normally and the error was lost.

The shape that reaches it: a loop's successors are dispatched from the
loop's final EOF, at the loop's own context. At the root that context is
``("main",)``, where an ``Interrupt.SELF`` is an ``InterruptTargetError``.
"""

from __future__ import annotations

import asyncio

import pytest

from operonx import END, PARENT, START, Operon, graph, op
from operonx.core.ops import if_
from operonx.core.ops._events import Interrupt
from operonx.core.ops.graph.task_scheduler import InterruptTargetError


@op
async def step(n: int) -> dict:
    await asyncio.sleep(0)
    return {"n": n + 1, "done": n + 1 >= 2}


@op
def stop_self(n: int = None):
    return Interrupt(ctx_to_cancel=Interrupt.SELF)


@graph
def self_interrupt_after_loop():
    PARENT.declare(n=0)
    s = step(n=PARENT["n"])
    s["n"] >> PARENT["n"]
    x = stop_self(n=s["n"])
    START >> s >> if_(s["done"] == True, x).else_(s)  # noqa: E712
    x >> END


async def test_fatal_from_the_final_drain_is_raised():
    with pytest.raises(InterruptTargetError):
        await asyncio.wait_for(Operon(self_interrupt_after_loop).run(inputs={}), timeout=10)
