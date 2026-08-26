"""Feeding a graph from a stream that ends.

A graph input can be any object, so streaming into one has always been
possible: hand it an ``asyncio.Queue`` and write a generator op that drains
it. What that leaves to every project is the *ending* — how a producer says
"no more input, finish what you started".

``handle.cancel()`` does not answer it. Cancellation is abrupt by design:
in-flight ops are killed and queued items are dropped. Measured on a graph
whose downstream op takes 300 ms per item, with three items pushed:

.. code-block:: text

    sentinel  started=[0, 1, 2]  finished=[0, 1, 2]
    cancel    started=[0]        finished=[]

For a call that ends with a spoken goodbye, the second column is the
goodbye being cut off mid-sentence. So projects invent an in-band sentinel
instead — usually ``None`` — and reimplement the same drain loop around it.
That works, and it is why this module is small: the only thing it adds is a
close protocol that is shared rather than reinvented, and one op that
implements the receiving half correctly.

Usage::

    from operonx.io import Channel, channel_source

    audio = Channel(maxsize=256)
    handle = engine.start(inputs={"audio": audio})

    async for msg in websocket.iter_text():
        await audio.push(decode(msg))     # backpressure when full
    await audio.close()                   # graph drains, then completes

    @graph
    def call(audio):
        frames = channel_source(audio)
        step = work(chunk=frames["item"])
        START >> frames >> step >> END

``Channel`` is deliberately not a transport. A WebSocket, an SSE stream, a
Kafka consumer and a file are all the same three lines against it, written
where that transport's own concerns already live — so no adapters ship.
"""

from __future__ import annotations

import asyncio
from typing import Any, AsyncIterator, Optional

from operonx.core.ops import op

__all__ = ["Channel", "ChannelClosed", "channel_source"]


class ChannelClosed(RuntimeError):
    """Raised by :meth:`Channel.push` after the channel has been closed."""


class _Sentinel:
    """The end-of-stream marker.

    Private to this module and compared by identity, so *any* value a
    producer legitimately sends — including ``None`` — passes through
    untouched. That is the flaw in the hand-rolled version this replaces:
    ``None`` is a fine thing to want to send.
    """

    __slots__ = ()

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return "<channel-closed>"


_CLOSED = _Sentinel()


class Channel:
    """A bounded conduit from outside a graph to an op inside it.

    Args:
        maxsize: Items buffered before :meth:`push` waits. ``0`` means
            unbounded, which is rarely what you want against a live
            producer — an unbounded buffer converts backpressure into
            memory growth.

    Closing is idempotent and broadcasts: the marker is put back after each
    consumer sees it, so several ops reading one channel all terminate.
    """

    __slots__ = ("_queue", "_closed")

    def __init__(self, maxsize: int = 0) -> None:
        self._queue: asyncio.Queue = asyncio.Queue(maxsize=maxsize)
        self._closed = False

    # ── producer side ───────────────────────────────────────────────────

    async def push(self, item: Any) -> None:
        """Append *item*, waiting while the buffer is full.

        Raises:
            ChannelClosed: the channel is already closed.
        """
        if self._closed:
            raise ChannelClosed("push after close")
        await self._queue.put(item)

    def push_nowait(self, item: Any) -> None:
        """Append *item* without waiting.

        Raises:
            ChannelClosed: the channel is already closed.
            asyncio.QueueFull: the buffer is full.
        """
        if self._closed:
            raise ChannelClosed("push after close")
        self._queue.put_nowait(item)

    async def close(self) -> None:
        """Signal end-of-stream. Idempotent.

        Items already pushed are still delivered — this is a graceful end,
        not a cancellation. The graph drains what it has and completes on
        its own.
        """
        if self._closed:
            return
        self._closed = True
        await self._queue.put(_CLOSED)

    @property
    def closed(self) -> bool:
        """True once :meth:`close` has been called."""
        return self._closed

    def qsize(self) -> int:
        """Items currently buffered. Useful for a backpressure metric."""
        return self._queue.qsize()

    # ── consumer side ───────────────────────────────────────────────────

    async def receive(self) -> Any:
        """Next item.

        For consumers that cannot use ``async for`` because they interleave
        the read with something else — a timeout, a periodic check. The
        callbot's frame source does exactly that, polling with a 200 ms
        timeout so it can fire a silence prompt between utterances::

            try:
                item = await asyncio.wait_for(chan.receive(), timeout=0.2)
            except asyncio.TimeoutError:
                ...                       # nothing arrived; do other work
            except ChannelClosed:
                return                    # producer finished

        Raises:
            ChannelClosed: the channel is closed and drained. It raises
                rather than returning ``None`` because ``None`` is itself a
                deliverable item — returning it here would reintroduce the
                ambiguity this module exists to remove.
        """
        item = await self._queue.get()
        if item is _CLOSED:
            # Put it back so every consumer terminates, not just the first.
            self._queue.put_nowait(_CLOSED)
            raise ChannelClosed("channel closed and drained")
        return item

    def __aiter__(self) -> AsyncIterator[Any]:
        return self._iterate()

    async def _iterate(self) -> AsyncIterator[Any]:
        while True:
            item = await self._queue.get()
            if item is _CLOSED:
                self._queue.put_nowait(_CLOSED)
                return
            yield item


@op
async def channel_source(channel: Optional[Channel] = None):
    """Yield one frame per item pushed to *channel*, then end on close.

    The receiving half of the protocol, written once. Ends by returning
    rather than raising, so the graph completes normally and downstream ops
    finish the work they already have.

    Yields:
        ``{"item": <pushed value>}``.
    """
    if channel is None:
        return
    async for item in channel:
        yield {"item": item}
