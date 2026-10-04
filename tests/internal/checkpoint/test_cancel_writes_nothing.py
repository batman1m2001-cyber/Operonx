"""K5: a cancelled op writes nothing — no outputs, no reducer merge.

``handle.cancel()`` lands while an op that would merge into a reducer cell
is still working: sleeping, in a worker thread, between two yields, or in a
retry backoff. Its write is pending. The checkpointer must see the writes
that landed before the cancel and nothing of the cancelled op's — not at the
cancel, not when its thread finishes later.
"""

from __future__ import annotations

import asyncio
import operator
import threading
import time

import pytest

from operonx import PARENT, Operon, Retry
from operonx.checkpoint import InMemoryCheckpointer
from operonx.core.ops.graph.graph_op import END, START, GraphOp
from operonx.core.ops.transform.func_op import op

pytestmark = pytest.mark.unit


class _Recording(InMemoryCheckpointer):
    """Keeps every write's value and whether it came after the cancel."""

    __slots__ = ("writes", "cancelled")

    def __init__(self):
        super().__init__()
        self.writes = []
        self.cancelled = False

    def on_cell_write(self, event):
        self.writes.append((self.cancelled, event.var, event.value))
        super().on_cell_write(event)

    def on_cancel(self, ctx):
        self.cancelled = True
        super().on_cancel(ctx)


def _log_values(cp):
    return [v for _, var, v in cp.writes if var == "log"]


async def _cancel_while_pending(g, started: asyncio.Event, settle: float = 0.3):
    cp = _Recording()
    handle = Operon(g).start(inputs={}, checkpointer=cp)
    await asyncio.wait_for(started.wait(), 5)
    handle.cancel()
    try:
        await asyncio.wait_for(handle._scheduler_task, timeout=2.0)
    except (asyncio.CancelledError, asyncio.TimeoutError):
        pass
    # Long enough for anything the cancel left running (a worker thread,
    # a retry sleep) to finish and try to write.
    await asyncio.sleep(settle)
    assert cp.cancelled, "the checkpointer heard the cancel"
    return cp


def _reducer_graph(slow_op):
    with GraphOp(name="g") as g:
        PARENT.declare(log=[], reducers={"log": operator.add})

        @op
        def first():
            return {"line": ["early"]}

        a = first(name="a")
        s = slow_op(name="s")
        a["line"] >> PARENT["log"]
        s["line"] >> PARENT["log"]
        START >> a >> s >> END
    return g


@pytest.mark.asyncio
async def test_cancel_while_an_async_op_is_pending():
    started = asyncio.Event()

    @op
    async def slow():
        started.set()
        await asyncio.sleep(0.2)
        return {"line": ["late"]}

    cp = await _cancel_while_pending(_reducer_graph(slow), started)
    assert _log_values(cp) == [["early"]]
    assert not [w for w in cp.writes if w[0]], "nothing written after the cancel"


@pytest.mark.asyncio
async def test_cancel_while_a_cpu_op_runs_in_its_thread():
    started = asyncio.Event()
    loop = asyncio.get_running_loop()
    finished = threading.Event()

    @op(bound="cpu")
    def crunch():
        loop.call_soon_threadsafe(started.set)
        time.sleep(0.15)
        finished.set()
        return {"line": ["late"]}

    cp = await _cancel_while_pending(_reducer_graph(crunch), started)
    assert finished.is_set(), "the abandoned thread ran to its end"
    assert _log_values(cp) == [["early"]]
    assert not [w for w in cp.writes if w[0]]


@pytest.mark.asyncio
async def test_cancel_between_two_yields():
    started = asyncio.Event()

    @op
    async def lines():
        yield {"line": ["y0"]}
        started.set()
        await asyncio.sleep(0.2)
        yield {"line": ["y1"]}

    cp = await _cancel_while_pending(_reducer_graph(lines), started)
    assert _log_values(cp) == [["early"], ["early", "y0"]]
    assert not [w for w in cp.writes if w[0]]


@pytest.mark.asyncio
async def test_cancel_during_a_retry_backoff():
    started = asyncio.Event()

    @op(retry=Retry(max_attempts=2, initial=0.2, jitter=False))
    async def flaky():
        if not started.is_set():
            started.set()
            raise ConnectionError("503")
        return {"line": ["late"]}

    cp = await _cancel_while_pending(_reducer_graph(flaky), started, settle=0.4)
    assert _log_values(cp) == [["early"]]
    assert not [w for w in cp.writes if w[0]]


@pytest.mark.asyncio
async def test_control_an_uncancelled_op_merges():
    """The recorder sees the slow op's merge when nothing cancels it."""

    @op
    async def slow():
        await asyncio.sleep(0.01)
        return {"line": ["late"]}

    cp = _Recording()
    await Operon(_reducer_graph(slow)).run(inputs={}, checkpointer=cp)
    assert _log_values(cp) == [["early"], ["early", "late"]]
