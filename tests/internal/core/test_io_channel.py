"""`operonx.io` — the close protocol a streaming graph needs.

The point of this module is the *ending*, so most of these tests are about
what happens after `close()`. The one that justifies its existence is
`test_close_drains_where_cancel_discards`: cancellation is abrupt by
design, and a graph that ends a call with a spoken goodbye cannot use it.
"""

from __future__ import annotations

import asyncio

import pytest

from operonx.core import END, PARENT, START, GraphOp, Operon, op
from operonx.io import Channel, ChannelClosed, channel_source

pytestmark = pytest.mark.unit


def _drain_graph(sink: list, delay: float = 0.0):
    """A graph that records every item a channel delivers.

    ``item`` carries a default because operonx does not bind a ``None``
    output to a downstream input — the op is called without the argument
    at all. That is a property of op wiring, not of the channel, but it
    means any op consuming a channel that may carry ``None`` needs one.
    """

    @op
    async def record(item=None):
        if delay:
            await asyncio.sleep(delay)
        sink.append(item)
        return {"item": item}

    with GraphOp(name="drain") as g:
        src = channel_source(channel=PARENT["chan"])
        rec = record(item=src["item"])
        START >> src >> rec >> END
    return g


class TestTheCloseProtocol:
    @pytest.mark.asyncio
    async def test_items_pushed_before_close_all_arrive(self):
        seen: list = []
        handle = Operon(_drain_graph(seen)).start(inputs={"chan": (chan := Channel())})
        for i in range(5):
            await chan.push(i)
        await chan.close()
        await asyncio.wait_for(handle.collect(), timeout=10)
        assert seen == [0, 1, 2, 3, 4]

    @pytest.mark.asyncio
    async def test_close_is_idempotent(self):
        chan = Channel()
        await chan.close()
        await chan.close()
        assert chan.closed

    @pytest.mark.asyncio
    async def test_push_after_close_is_an_error_not_a_silent_drop(self):
        chan = Channel()
        await chan.close()
        with pytest.raises(ChannelClosed):
            await chan.push("late")
        with pytest.raises(ChannelClosed):
            chan.push_nowait("late")

    @pytest.mark.asyncio
    async def test_none_is_a_deliverable_value(self):
        """The reason the marker is a private sentinel and not ``None``.

        Hand-rolled versions of this protocol use ``None`` to mean "ended",
        which quietly makes ``None`` unsendable. Asserted at the channel
        boundary, which is what this module owns.
        """
        chan = Channel()
        await chan.push(None)
        await chan.push(1)
        await chan.close()
        assert [item async for item in chan] == [None, 1]

    @pytest.mark.asyncio
    async def test_a_none_item_reaches_an_op_that_declares_a_default(self):
        seen: list = []
        handle = Operon(_drain_graph(seen)).start(inputs={"chan": (chan := Channel())})
        await chan.push(None)
        await chan.push(1)
        await chan.close()
        await asyncio.wait_for(handle.collect(), timeout=10)
        assert seen == [None, 1]

    @pytest.mark.asyncio
    async def test_close_broadcasts_to_every_consumer(self):
        chan = Channel()
        await chan.push("x")
        await chan.close()
        first = [item async for item in chan]
        second = [item async for item in chan]
        assert first == ["x"]
        assert second == [], "the second consumer never saw the close"

    @pytest.mark.asyncio
    async def test_an_unopened_channel_ends_the_source_cleanly(self):
        seen: list = []
        handle = Operon(_drain_graph(seen)).start(inputs={"chan": None})
        await asyncio.wait_for(handle.collect(), timeout=10)
        assert seen == []


class TestBackpressure:
    @pytest.mark.asyncio
    async def test_push_waits_while_the_buffer_is_full(self):
        chan = Channel(maxsize=2)
        await chan.push(1)
        await chan.push(2)
        assert chan.qsize() == 2
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(chan.push(3), timeout=0.15)


class TestWhyThisExistsAtAll:
    @pytest.mark.asyncio
    async def test_close_drains_where_cancel_discards(self):
        """`handle.cancel()` is not a graceful end, and cannot be made one.

        Cancellation kills in-flight ops and drops queued items — correct
        for "stop now", wrong for "no more input, finish up". Both are
        needed; this is the second one.
        """
        graceful: list = []
        handle = Operon(_drain_graph(graceful, delay=0.05)).start(
            inputs={"chan": (chan := Channel())}
        )
        for i in range(3):
            await chan.push(i)
        await asyncio.sleep(0.02)  # downstream now in flight
        await chan.close()
        await asyncio.wait_for(handle.collect(), timeout=10)

        abrupt: list = []
        handle2 = Operon(_drain_graph(abrupt, delay=0.05)).start(
            inputs={"chan": (chan2 := Channel())}
        )
        for i in range(3):
            await chan2.push(i)
        await asyncio.sleep(0.02)
        handle2.cancel()
        await asyncio.sleep(0.4)

        assert graceful == [0, 1, 2], f"close should drain, got {graceful}"
        assert len(abrupt) < len(graceful), (
            f"cancel discarded nothing, so close would be redundant: {abrupt}"
        )


class TestReceiveForInterleavedConsumers:
    """`receive()` is for sources that cannot use `async for`.

    The callbot's frame source polls with a timeout so it can fire a
    silence prompt between utterances. That shape has to work, or the
    channel only serves the easy half of the problem.
    """

    @pytest.mark.asyncio
    async def test_end_of_stream_raises_rather_than_returning_none(self):
        chan = Channel()
        await chan.push(None)
        await chan.close()
        assert await chan.receive() is None, "a pushed None must come back as None"
        with pytest.raises(ChannelClosed):
            await chan.receive()

    @pytest.mark.asyncio
    async def test_poll_with_timeout_between_items(self):
        chan = Channel()
        ticks, got = 0, []
        await chan.push("a")

        async def producer():
            await asyncio.sleep(0.25)
            await chan.push("b")
            await chan.close()

        task = asyncio.create_task(producer())
        while True:
            try:
                got.append(await asyncio.wait_for(chan.receive(), timeout=0.05))
            except asyncio.TimeoutError:
                ticks += 1  # the silence-prompt slot
            except ChannelClosed:
                break
        await task
        assert got == ["a", "b"]
        assert ticks > 0, "the consumer never got a turn between items"
