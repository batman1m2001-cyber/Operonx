"""The session a job mints: items in from a source, results out to a sink.

This is the whole reason a job can reuse the graph unchanged. `ingress`
reads ``session.recv()`` and `egress` writes ``session.send()``; a
:class:`JobSession` is those two methods over a source and a sink instead
of a socket. The graph cannot tell, and so the same graph is served on
Monday and run over a file on Tuesday.
"""

from __future__ import annotations

from typing import Any, Mapping, Optional

from operonx.core.loggings import LOGGER
from operonx.core.serve.protocol import BoundedSession

from .sinks import Sink

__all__ = ["JobSession"]


class JobSession(BoundedSession):
    """One item — or, in stream mode, all of them — in; `egress` out to the sink.

    ``send`` keeps the session contract (report, never raise), but a sink
    that cannot write is not a peer that hung up: it is the item failing.
    So the last error is kept on ``sink_error`` and the runner marks the
    item failed from it.
    """

    def __init__(self, sink: Sink, key: str, *, meta: Optional[Mapping[str, Any]] = None,
                 max_inflight: Optional[int] = None):
        super().__init__(meta={"key": key, **dict(meta or {})}, max_inflight=max_inflight)
        self.sink = sink
        self.key = key
        self.sent = 0
        self.sink_error: Optional[str] = None

    async def _send(self, item: Any) -> bool:
        try:
            await self.sink.write(self.key, item)
        except Exception as exc:                          # noqa: BLE001
            self.sink_error = f"{type(exc).__name__}: {exc}"
            LOGGER.error(f"[job] sink write failed for key {self.key!r}: {self.sink_error}")
            return False
        self.sent += 1
        return True
