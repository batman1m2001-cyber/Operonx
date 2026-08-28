"""What a transport is, and how an op reaches the connection that minted it.

The interface, not an implementation. WebSocket and HTTP are shipped
built-ins that register through the same call any project uses for its own
transport, and they hold no privileged position: a SIP trunk, a Kafka
consumer, a serial port or an in-house RPC is two methods and a
registration, and is then a first-class part of a run — traced, bounded,
swept and ended like everything else.

The unit is a **session**, not a channel of items. That distinction is the
whole design. A channel pair describes `POST /predict`, a file, and a queue
worker perfectly well; it does not describe a phone call, where one socket
mints one long-lived run, carries items both ways for its lifetime, and
holds state — the VAD's buffers, an end-of-call event — belonging to the
connection rather than to any item on it. Design for the session and
request/response falls out as the degenerate case with one item in and one
out. The reverse does not.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Dict, Mapping, Optional, Protocol, runtime_checkable

__all__ = [
    "Session",
    "Transport",
    "RunRequest",
    "BoundedSession",
    "current_session",
    "SESSION_KEY",
]

#: Where the serve layer parks the session on the run's scratch. Reserved
#: and internal: ops reach it through :func:`current_session`, so the
#: binding is typed and greppable instead of a string literal spread
#: through project code.
SESSION_KEY = "__operonx_session__"


@dataclass
class RunRequest:
    """A connection, turned into the arguments for one run.

    Returned by a project's ``on_session`` hook. Turning
    ``?call_id=&customer_info=`` into real inputs, refusing an unknown
    agent, hitting a customer store — that is project logic, and no amount
    of TOML will express it. One declared hook is the honest answer;
    frameworks that pretended config was enough grew a plugin system
    instead.

    Returning ``None`` from the hook refuses the connection.
    """

    inputs: Dict[str, Any] = field(default_factory=dict)
    scratch: Dict[str, Any] = field(default_factory=dict)
    trace_id: Optional[str] = None
    request_id: Optional[str] = None
    user_id: Optional[str] = None
    session_id: Optional[str] = None


@runtime_checkable
class Session(Protocol):
    """One connection, for as long as it lives.

    ``recv`` ending is the **only** end-of-input signal, and a transport
    never cancels the run it minted. That is not a new rule — it is what
    the callbot already relies on: `terminal_events` writes the CRM record
    *after* the caller has hung up, so a framework that killed the run on
    disconnect would silently lose the record for every completed call.
    A transport wanting cancellation cancels the run explicitly.
    """

    meta: Mapping[str, Any]

    def recv(self) -> AsyncIterator[Any]: ...

    async def send(self, item: Any) -> None: ...

    async def close(self) -> None: ...


@runtime_checkable
class Transport(Protocol):
    """A source of sessions.

    ``sessions()`` yields for as long as the transport is up — one per
    connection, per request, or per file, depending on what it is.
    """

    def sessions(self) -> AsyncIterator[Session]: ...

    async def close(self) -> None: ...


class BoundedSession:
    """A `Session` whose inbound side has a ceiling.

    The bound lives here rather than in the ingress op, because this is
    where items enter. When the buffer is full the transport stops reading
    its socket, and for TCP that is the connection's own flow control —
    correct, free, and applied before anything is allocated inside the
    graph.

    It is mandatory for stream transports rather than defaulted, because
    operonx guards concurrency and — since transient ports — retention,
    but it has never guarded *volume*. An unbounded queue behind a network
    socket is precisely how this project's Channels came to exist, and a
    ceiling nobody chose is how one gets shipped again.

    Subclasses implement :meth:`_send`. Anything that overflows is counted
    and reported, never dropped in silence.
    """

    def __init__(self, meta: Optional[Mapping[str, Any]] = None, max_inflight: Optional[int] = None):
        self.meta: Mapping[str, Any] = dict(meta or {})
        self.max_inflight = max_inflight
        self._queue: asyncio.Queue = asyncio.Queue(maxsize=max_inflight or 0)
        self._closed = asyncio.Event()
        self.overflowed = 0

    async def feed(self, item: Any) -> None:
        """Push one inbound item, waiting when the bound is reached."""
        await self._queue.put(item)

    def feed_nowait(self, item: Any) -> bool:
        """Push without waiting. ``False`` means the bound was hit.

        Counted rather than silent — a transport that drops has to be able
        to say how much.
        """
        try:
            self._queue.put_nowait(item)
            return True
        except asyncio.QueueFull:
            self.overflowed += 1
            return False

    def end_input(self) -> None:
        """No more inbound items. `recv` drains what is left, then stops."""
        self._closed.set()
        self._queue.put_nowait(_EOF)

    async def recv(self) -> AsyncIterator[Any]:
        while True:
            item = await self._queue.get()
            if item is _EOF:
                return
            yield item

    async def send(self, item: Any) -> None:
        await self._send(item)

    async def _send(self, item: Any) -> None:
        raise NotImplementedError

    async def close(self) -> None:
        if not self._closed.is_set():
            self.end_input()


class _Eof:
    __slots__ = ()

    def __repr__(self) -> str:
        return "<eof>"


_EOF = _Eof()


def current_session() -> Optional[Session]:
    """The session that minted the run this op is running inside.

    Reads the run's state through the same ContextVar `SCRATCH` uses, so
    it is bound inside an op body and unbound everywhere else. Returns
    ``None`` when the graph was started directly rather than served —
    which is what makes an `ingress`-bearing graph still testable by
    calling ``engine.start()`` with ordinary inputs.
    """
    from operonx.core.ops._edges import _current_state_var

    try:
        state = _current_state_var.get()
    except LookupError:
        return None
    return state._scratch.get(SESSION_KEY)
