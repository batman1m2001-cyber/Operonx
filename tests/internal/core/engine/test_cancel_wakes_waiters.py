"""Cancelling a run ends it — for everyone waiting on it (roadmap C3, C4).

C3: ``handle.cancel()`` cancelled the scheduler and the pump that feeds
the handle, but never marked the handle done. ``result()``, ``collect()``,
``await handle[op, var]`` and ``async for`` all wait on that mark, so each
of them waited forever.

C4: ``asyncio.wait_for(engine.run(...), t)`` timed out on the caller's
side while the graph kept going: ``run()`` awaited the handle and nothing
cancelled the handle when that await was cancelled. The op after the
timeout still ran — in the repro, the one that charges the card.
"""

import asyncio

import pytest

from operonx import END, START, Operon, graph, op


def _slow_graph(seen: dict):
    @op
    async def slow(x: int) -> dict:
        await asyncio.sleep(1.0)
        seen["slow_done"] += 1
        return {"y": x}

    @op
    async def after(y: int) -> dict:
        seen["after"] += 1  # a side effect: charge a card, send an email...
        return {"z": y}

    @graph
    def flow(x):
        s = slow(x=x)
        a = after(y=s["y"])
        START >> s >> a >> END

    return Operon(flow, params={"x": None})


def _seen() -> dict:
    return {"slow_done": 0, "after": 0}


class TestCancelWakesWaiters:
    async def test_result_raises_cancelled_instead_of_waiting_forever(self):
        handle = _slow_graph(_seen()).start({"x": 1})
        handle.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(handle.result(), timeout=1)

    async def test_a_waiter_already_parked_is_woken(self):
        handle = _slow_graph(_seen()).start({"x": 1})
        waiter = asyncio.ensure_future(handle.result())
        await asyncio.sleep(0.05)  # parked on the handle
        assert not waiter.done()
        handle.cancel()
        done, _ = await asyncio.wait({waiter}, timeout=1)
        assert done, "result() still waiting after cancel()"
        with pytest.raises(asyncio.CancelledError):
            waiter.result()

    async def test_collect_and_point_query_and_iteration_end(self):
        engine = _slow_graph(_seen())

        handle = engine.start({"x": 1})
        point = asyncio.ensure_future(handle["after", "z"])
        await asyncio.sleep(0.05)
        handle.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(point, timeout=1)

        handle = engine.start({"x": 1})
        handle.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(handle.collect(), timeout=1)

        handle = engine.start({"x": 1})
        await asyncio.sleep(0.05)
        handle.cancel()

        async def iterate():
            async for _ in handle:
                pass

        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(iterate(), timeout=1)

    async def test_cancel_after_the_run_finished_keeps_its_result(self):
        @op
        def double(x: int) -> dict:
            return {"y": x * 2}

        @graph
        def quick(x):
            d = double(x=x)
            START >> d >> END

        handle = Operon(quick, params={"x": None}).start({"x": 2})
        assert (await handle.result())["y"] == 4
        handle.cancel()
        assert (await handle.result())["y"] == 4


class TestTimeoutCancelsTheGraph:
    async def test_run_timeout_cancels_graph(self):
        """`evidence/repros/timeout_leak.py`."""
        seen = _seen()
        engine = _slow_graph(seen)
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(engine.run(inputs={"x": 1}), timeout=0.3)
        await asyncio.sleep(1.5)
        assert seen == {"slow_done": 0, "after": 0}

    async def test_run_cancelled_by_its_caller_cancels_graph(self):
        seen = _seen()
        engine = _slow_graph(seen)
        task = asyncio.ensure_future(engine.run(inputs={"x": 1}))
        await asyncio.sleep(0.1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        await asyncio.sleep(1.5)
        assert seen == {"slow_done": 0, "after": 0}

    @pytest.mark.parametrize("mode", ["updates", "values", "frames", "custom"])
    async def test_stream_timeout_cancels_graph(self, mode):
        """Not a C4 regression — every mode already cancels in its
        ``finally`` — but the same contract as ``run()``, held here."""
        seen = _seen()
        engine = _slow_graph(seen)

        async def consume():
            async for _ in engine.stream({"x": 1}, mode=mode):
                pass

        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(consume(), timeout=0.3)
        await asyncio.sleep(1.5)
        assert seen == {"slow_done": 0, "after": 0}
