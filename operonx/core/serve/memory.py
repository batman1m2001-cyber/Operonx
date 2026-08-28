"""An in-memory transport — the one the tests use, and the reference.

It exists first, and deliberately before any network built-in. If a
transport written against the public protocol cannot drive a real graph,
the interface is decorative and shipping a WebSocket implementation would
only cement the mistake by letting the built-in quietly become the
contract.
"""

from __future__ import annotations

import asyncio
from typing import Any, AsyncIterator, List, Mapping, Optional

from .protocol import BoundedSession

__all__ = ["MemorySession", "MemoryTransport"]


class MemorySession(BoundedSession):
    """A session whose outbound side is a list you can assert against."""

    def __init__(self, meta: Optional[Mapping[str, Any]] = None, max_inflight: Optional[int] = None):
        super().__init__(meta=meta, max_inflight=max_inflight)
        self.sent: List[Any] = []
        self.closed = False

    async def _send(self, item: Any) -> None:
        self.sent.append(item)

    async def close(self) -> None:
        self.closed = True
        await super().close()


class MemoryTransport:
    """Hands out sessions that a test pushes into by hand."""

    def __init__(self, max_inflight: Optional[int] = None):
        self.max_inflight = max_inflight
        self._incoming: asyncio.Queue = asyncio.Queue()
        self._done = False
        self.opened: List[MemorySession] = []

    def open(self, meta: Optional[Mapping[str, Any]] = None) -> MemorySession:
        """Create a session and offer it to whoever is serving."""
        session = MemorySession(meta=meta, max_inflight=self.max_inflight)
        self.opened.append(session)
        self._incoming.put_nowait(session)
        return session

    def stop(self) -> None:
        self._done = True
        self._incoming.put_nowait(None)

    async def sessions(self) -> AsyncIterator[MemorySession]:
        while True:
            session = await self._incoming.get()
            if session is None:
                return
            yield session

    async def close(self) -> None:
        self.stop()
