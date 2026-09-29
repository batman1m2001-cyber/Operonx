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
from operonx.core.ops.transform.func_op import FuncOp


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


# ── an exception escaping a sync op's run() ─────────────────────────────
#
# BaseOp.run records an op's own error and emits nothing, so this only
# happens when run() itself fails. `_drain_inline` answered it by writing
# `state[op_name, "error", ctx]` with the op's *local* name, which is not a
# schema key: the caller got `KeyError('(a, error) not found in schema')`
# instead of the error. The async path (`_pump`) hands it to `fatal` and the
# run raises it; the inline path now does the same.


class RunFailure(RuntimeError):
    pass


@op
def fine() -> dict:
    return {"x": 1}


class _BrokenRun(FuncOp):
    __slots__ = ()

    async def run(self, state, context_id=None):
        raise RunFailure("run() itself failed")
        yield  # pragma: no cover — makes this an async generator


@graph
def broken_sync_run():
    a = fine()
    START >> a >> END


async def test_exception_escaping_a_sync_ops_run_is_raised():
    g = broken_sync_run()
    g._ops["a"].__class__ = _BrokenRun
    assert g._ops["a"].bound == "sync"
    with pytest.raises(RunFailure):
        await asyncio.wait_for(Operon(g).run(inputs={}), timeout=10)
