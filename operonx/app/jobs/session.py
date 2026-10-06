"""The session a job mints for one item of a graph that has doors.

This is why a job can reuse a served graph unchanged: `ingress` reads
``session.recv()`` and `egress` writes ``session.send()``. A
:class:`JobSession` is those two methods over one item and a list instead
of a socket. The graph cannot tell, so the same graph is served on Monday
and run over a file on Tuesday.
"""

from __future__ import annotations

from typing import Any, List, Mapping, Optional

from operonx.app.serve.protocol import BoundedSession

__all__ = ["JobSession"]


class JobSession(BoundedSession):
    """One item in; what `egress` sends, kept as the item's result."""

    #: A job keeps one result per item, not a stream of frames.
    stream = False

    def __init__(self, key: Optional[str], *, meta: Optional[Mapping[str, Any]] = None):
        super().__init__(meta={"key": key, **dict(meta or {})})
        self.key = key
        self.sent_items: List[Any] = []

    @property
    def sent(self) -> int:
        return len(self.sent_items)

    async def _send(self, item: Any) -> bool:
        self.sent_items.append(item)
        return True
