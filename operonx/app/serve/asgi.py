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
import json
import time
from typing import Any, AsyncIterator, Dict, List, Optional, Tuple, Union

from operonx.app.manifest import door_codec
from operonx.core.loggings import LOGGER

from .protocol import BoundedSession

__all__ = [
    "AsgiTransport",
    "DoorDecodeError",
    "HttpSession",
    "HttpTransport",
    "WebSocketSession",
    "WebSocketTransport",
    "decode_payload",
]


class DoorDecodeError(ValueError):
    """What a caller sent cannot be read by the door's codec."""


def decode_payload(raw: Union[str, bytes], codec: str, what: str = "body") -> Any:
    """One payload — an HTTP body, a websocket text frame — as the item the
    graph receives, the same way at every door.

    ``codec="json"`` parses it; ``"text"`` passes text through. An empty
    HTTP body is ``None``, no payload, under either codec.

    Raises:
        DoorDecodeError: the payload is not JSON (``"json"``) or not UTF-8
            text. The door refuses it before a run is minted; it used to
            run the graph with the raw string instead.
    """
    if isinstance(raw, (bytes, bytearray)):
        if not raw:
            return None
        try:
            raw = bytes(raw).decode("utf-8")
        except UnicodeDecodeError as exc:
            raise DoorDecodeError(f"{what} is not UTF-8 text: {exc}") from None
    if codec == "text":
        return raw
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise DoorDecodeError(f"{what} is not JSON: {exc}") from None


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
    before the run starts, and a reply collected rather than streamed —
    unless the caller asked for a stream (``stream=True``: the request
    accepts ``text/event-stream``). Then each item is also handed to
    :meth:`frames` as the run sends it, and the endpoint writes it out as
    one server-sent event while the run goes on.
    """

    def __init__(self, payload: Any, meta: Optional[Dict[str, Any]] = None, stream: bool = False):
        super().__init__(meta=meta, max_inflight=None)
        # Every item the run sent, in order: the reply, and what a stream's
        # readers follow. The n-th item is event n (from 1) of the stream,
        # so a reader that dropped asks for what came after the last it saw.
        self.replies: List[Any] = []
        self.finished = asyncio.Event()
        self.ended_at: Optional[float] = None
        self.stream = stream
        self._grew = asyncio.Event()
        self._readers = 0
        # A run is never paced by a reader: one that leaves makes items
        # "not delivered" (`gone`) until another follows, not the run wait.
        self.gone = False
        #: ``(status, body)`` when the door refused the request without a
        #: run (a doorless graph's body that does not fit, `refuse`).
        self.refusal: Optional[Tuple[int, Dict[str, Any]]] = None
        self.feed_nowait(payload)
        self.end_input()

    def refuse(self, status: int, body: Dict[str, Any]) -> None:
        """The request is answered with *status* and *body*; no run."""
        self.refusal = (status, body)

    async def _send(self, item: Any) -> bool:
        self.replies.append(item)
        self._grew.set()
        if not self.stream:
            return True
        return not self.gone

    async def follow(self, after: int = 0) -> AsyncIterator[Tuple[int, Any]]:
        """``(n, item)`` for each item after the *after*-th — those sent
        already, then each as it is sent, until the run ends. Only for a
        session made with ``stream=True``; any number may follow at once."""
        if not self.stream:
            raise RuntimeError("follow() needs an HttpSession made with stream=True")
        self._readers += 1
        self.gone = False
        seen = max(int(after), 0)
        try:
            while True:
                while seen < len(self.replies):
                    seen += 1
                    yield seen, self.replies[seen - 1]
                if self.finished.is_set():
                    return
                self._grew.clear()
                if seen < len(self.replies) or self.finished.is_set():
                    continue
                await self._grew.wait()
        finally:
            self._readers -= 1
            if self._readers == 0:
                self.gone = True

    async def frames(self) -> AsyncIterator[Any]:
        """Each item the run sends, as it sends it, until the run ends.
        Only for a session made with ``stream=True``."""
        async for _, item in self.follow(0):
            yield item

    async def close(self) -> None:
        await super().close()
        if not self.finished.is_set():
            self.ended_at = time.monotonic()
        self.finished.set()
        self._grew.set()

    @property
    def reply(self) -> Any:
        """One reply unwrapped, several as a list — the shape callers expect."""
        if not self.replies:
            return None
        return self.replies[0] if len(self.replies) == 1 else self.replies


#: How long a streamed run's items stay readable after it ended, and how
#: many ended streams are kept: a reconnect after that gets a 404.
STREAM_KEEP_S = 15 * 60
STREAM_KEEP_MAX = 1000


class HttpTransport(AsgiTransport):
    """`session = "per_request"`: one request, one run, one response."""

    def __init__(self, spec: Any = None):
        super().__init__(spec)
        self._streams: List[HttpSession] = []

    def stream_of(self, run_id: str) -> Optional[HttpSession]:
        """The streamed session of run *run_id*, while it is kept: in
        this process only — a reconnect must reach the same replica."""
        for session in self._streams:
            if session.trace_id == run_id:
                return session
        return None

    def _keep(self, session: HttpSession) -> None:
        now = time.monotonic()
        self._streams = [
            s for s in self._streams if s.ended_at is None or now - s.ended_at < STREAM_KEEP_S
        ]
        ended = [s for s in self._streams if s.ended_at is not None]
        for old in ended[: max(len(ended) - STREAM_KEEP_MAX + 1, 0)]:
            self._streams.remove(old)
        self._streams.append(session)

    async def handle(self, payload: Any, meta: Optional[Dict[str, Any]] = None) -> HttpSession:
        session = HttpSession(payload, meta=meta)
        self.offer(session)
        await session.finished.wait()
        return session

    def open_stream(self, payload: Any, meta: Optional[Dict[str, Any]] = None) -> HttpSession:
        """Start the run for one request whose caller reads it as a stream;
        read what it sends from ``session.frames()``."""
        session = HttpSession(payload, meta=meta, stream=True)
        self._keep(session)
        self.offer(session)
        return session


class WebSocketSession(BoundedSession):
    """One connection, for as long as it lives.

    `recv` ends when the peer disconnects, which ends `ingress`, which
    drains the graph. The run is never cancelled from out here: work that
    has to happen after the peer is gone — writing a call record — only
    survives if the run is allowed to finish.
    """

    def __init__(
        self,
        websocket: Any,
        meta: Optional[Dict[str, Any]] = None,
        max_inflight: Optional[int] = None,
        codec: str = "json",
    ):
        super().__init__(meta=meta, max_inflight=max_inflight)
        self.websocket = websocket
        self.codec = codec
        self.sent = 0
        self.send_failures = 0
        self.refused_frames = 0

    async def _send(self, item: Any) -> bool:
        try:
            if isinstance(item, (bytes, bytearray)):
                await self.websocket.send_bytes(item)
            elif isinstance(item, str):
                await self.websocket.send_text(item)
            else:
                await self.websocket.send_json(item)
            self.sent += 1
            return True
        except Exception as exc:  # noqa: BLE001
            # The peer going away mid-reply is ordinary. It is counted and
            # reported rather than raised, because one failed frame should
            # not tear down a run that still has a record to write.
            self.send_failures += 1
            if self.send_failures == 1:
                LOGGER.info(f"[serve] websocket send failed: {type(exc).__name__}: {exc}")
            return False

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
                    try:
                        item = decode_payload(message["text"], self.codec, what="frame")
                    except DoorDecodeError as exc:
                        # One bad frame is the client's mistake, not the
                        # end of the call: it is told, and the next frame
                        # is read as usual.
                        self.refused_frames += 1
                        await self._tell({"error": str(exc)})
                        continue
                    await self.feed(item)
                elif message.get("bytes") is not None:
                    await self.feed(message["bytes"])
        except Exception as exc:  # noqa: BLE001
            LOGGER.debug(f"[serve] websocket recv ended: {type(exc).__name__}: {exc}")
        finally:
            self.end_input()

    async def run_failed(self) -> None:
        """A run that failed before sending anything says so.

        Without it the client of a run that died at its first op heard
        nothing at all. A run that already answered has told the client
        what it could; its later failures stay in the log and the trace.
        """
        if self.sent == 0:
            await self._tell(
                {"error": "the graph failed before it sent anything", "trace_id": self.trace_id}
            )

    async def _tell(self, notice: Dict[str, Any]) -> None:
        """A frame from the door itself, not the run: not counted in `sent`."""
        try:
            await self.websocket.send_json(notice)
        except Exception as exc:  # noqa: BLE001 — the peer may be gone; that is ordinary
            LOGGER.info(f"[serve] websocket notice not delivered: {type(exc).__name__}: {exc}")


class WebSocketTransport(AsgiTransport):
    """`session = "per_connection"`: one socket, one long-lived run."""

    async def handle(
        self, websocket: Any, meta: Optional[Dict[str, Any]] = None
    ) -> WebSocketSession:
        codec = door_codec(self.spec) if self.spec is not None else "json"
        session = WebSocketSession(
            websocket, meta=meta, max_inflight=self.max_inflight, codec=codec
        )
        self.offer(session)
        await session.pump_inbound()
        return session
