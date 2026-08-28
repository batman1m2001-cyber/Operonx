"""Built-in transports: `http`, `websocket`, `asgi`.

Shipped because most projects want them and nobody should write a
WebSocket handshake twice. They hold no privileged position — they
implement the same `Session`/`Transport` protocol a project implements for
its own transport, register through the same call, and can be replaced
without touching operonx. The in-memory transport and the third-party gate
in the test suite exist so that stays true.

Requires the ``serve`` extra: ``pip install "operonx[serve]"``.
"""

from __future__ import annotations

import asyncio
from typing import Any, AsyncIterator, Dict, List, Optional

from operonx.core.loggings import LOGGER

from .protocol import BoundedSession

__all__ = [
    "AsgiTransport",
    "HttpSession",
    "HttpTransport",
    "WebSocketSession",
    "WebSocketTransport",
]


class AsgiTransport:
    """A transport whose sessions are created by an ASGI route.

    Inverted from the in-memory case: nothing here loops accepting
    connections, because the ASGI server already does that. A route builds
    a session, offers it, and waits for the run to finish with it.
    """

    def __init__(self, spec: Any = None):
        self.spec = spec
        self.max_inflight = getattr(spec, "max_inflight", None)
        self._incoming: asyncio.Queue = asyncio.Queue()
        self._stopped = False

    def offer(self, session: "BoundedSession") -> None:
        self._incoming.put_nowait(session)

    async def sessions(self) -> AsyncIterator[Any]:
        while True:
            session = await self._incoming.get()
            if session is None:
                return
            yield session

    async def close(self) -> None:
        if not self._stopped:
            self._stopped = True
            self._incoming.put_nowait(None)


class HttpSession(BoundedSession):
    """One request in, whatever `egress` writes out.

    The degenerate case of a session: exactly one inbound item, available
    before the run starts, and a reply collected rather than streamed.
    """

    def __init__(self, payload: Any, meta: Optional[Dict[str, Any]] = None):
        super().__init__(meta=meta, max_inflight=None)
        self.replies: List[Any] = []
        self.finished = asyncio.Event()
        self.feed_nowait(payload)
        self.end_input()

    async def _send(self, item: Any) -> None:
        self.replies.append(item)

    async def close(self) -> None:
        await super().close()
        self.finished.set()

    @property
    def reply(self) -> Any:
        """One reply unwrapped, several as a list — the shape callers expect."""
        if not self.replies:
            return None
        return self.replies[0] if len(self.replies) == 1 else self.replies


class HttpTransport(AsgiTransport):
    """`session = "per_request"`: one request, one run, one response."""

    async def handle(self, payload: Any, meta: Optional[Dict[str, Any]] = None) -> HttpSession:
        session = HttpSession(payload, meta=meta)
        self.offer(session)
        await session.finished.wait()
        return session


class WebSocketSession(BoundedSession):
    """One connection, for as long as it lives.

    `recv` ends when the peer disconnects, which ends `ingress`, which
    drains the graph. The run is never cancelled from out here: work that
    has to happen after the peer is gone — writing a call record — only
    survives if the run is allowed to finish.
    """

    def __init__(self, websocket: Any, meta: Optional[Dict[str, Any]] = None,
                 max_inflight: Optional[int] = None):
        super().__init__(meta=meta, max_inflight=max_inflight)
        self.websocket = websocket
        self.sent = 0
        self.send_failures = 0

    async def _send(self, item: Any) -> None:
        try:
            if isinstance(item, (bytes, bytearray)):
                await self.websocket.send_bytes(item)
            elif isinstance(item, str):
                await self.websocket.send_text(item)
            else:
                await self.websocket.send_json(item)
            self.sent += 1
        except Exception as exc:                       # noqa: BLE001
            # The peer going away mid-reply is ordinary. It is counted
            # rather than raised, because one failed frame should not tear
            # down a run that still has a record to write.
            self.send_failures += 1
            if self.send_failures == 1:
                LOGGER.info(f"[serve] websocket send failed: {type(exc).__name__}: {exc}")

    async def pump_inbound(self) -> None:
        """Read the socket into the session until the peer stops.

        `feed` awaits when the bound is reached, so a full buffer stops
        this coroutine reading — and for TCP that is the connection's own
        flow control, applied before anything is allocated in the graph.
        """
        try:
            while True:
                message = await self.websocket.receive()
                kind = message.get("type")
                if kind == "websocket.disconnect":
                    break
                if message.get("text") is not None:
                    await self.feed(message["text"])
                elif message.get("bytes") is not None:
                    await self.feed(message["bytes"])
        except Exception as exc:                       # noqa: BLE001
            LOGGER.debug(f"[serve] websocket recv ended: {type(exc).__name__}: {exc}")
        finally:
            self.end_input()


class WebSocketTransport(AsgiTransport):
    """`session = "per_connection"`: one socket, one long-lived run."""

    async def handle(self, websocket: Any, meta: Optional[Dict[str, Any]] = None) -> WebSocketSession:
        session = WebSocketSession(websocket, meta=meta, max_inflight=self.max_inflight)
        self.offer(session)
        await session.pump_inbound()
        return session
