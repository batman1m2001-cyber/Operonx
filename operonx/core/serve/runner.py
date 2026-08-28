"""Sessions in, runs out — the hop that was never an op.

`operonx.toml` says it plainly: *"nothing derived from the graph can say
this: uvicorn calls an ASGI route, which calls engine.start(), and that
hop is not an op."* This module is that hop, written once, so that no
project has to write it again. The callbot's version of it is 437 lines.
"""

from __future__ import annotations

import asyncio
import inspect
from typing import Any, Optional

from operonx.core.loggings import LOGGER
from operonx.core.manifest import ServeSpec

from .protocol import SESSION_KEY, RunRequest, Session
from .registry import load_object, resolve_transport

__all__ = ["ServeRunner", "serve_session"]


async def serve_session(engine: Any, session: Session, request: Optional[RunRequest] = None) -> Any:
    """Run `engine` for one session, and return its handle when it ends.

    The session is seeded into the run's scratch under a reserved key, so
    `ingress` and `egress` resolve it from the run rather than from a
    string the author had to remember.

    The run is always allowed to finish. A peer that disappears surfaces as
    the session's `recv` ending, which ends `ingress`, which drains the
    graph — one signal travelling one way, instead of a race between a
    cancel and a teardown.
    """
    request = request or RunRequest()
    scratch = dict(request.scratch)
    scratch[SESSION_KEY] = session

    handle = engine.start(
        inputs=dict(request.inputs),
        scratch=scratch,
        request_id=request.request_id,
        user_id=request.user_id,
        session_id=request.session_id,
        trace_id=request.trace_id,
    )
    try:
        async for _op_name, _ctx, _data in handle:
            pass
    finally:
        await session.close()
    return handle


class ServeRunner:
    """Drives one `[[serve]]` entry: a transport, a graph, and its runs.

    Each session becomes one run for ``per_connection``, which is the
    shape a phone call needs. ``per_request`` and ``per_message`` differ
    only in how many runs a session mints, and that difference is the only
    thing the session mode decides at this level — what a *failure* means
    is decided by the same field one layer up, where a transport can turn
    it into a status code.
    """

    def __init__(self, engine: Any, spec: ServeSpec, transport: Any = None):
        self.engine = engine
        self.spec = spec
        self.transport = transport if transport is not None else resolve_transport(spec.kind)(spec)
        self._on_session = load_object(spec.on_session) if spec.on_session else None
        self._on_close = load_object(spec.on_close) if spec.on_close else None
        self._runs: set = set()

    def _request_for(self, session: Session) -> Optional[RunRequest]:
        if self._on_session is None:
            return RunRequest()
        request = self._on_session(session)
        if request is None:
            LOGGER.info(f"[serve:{self.spec.name}] on_session refused a connection")
        return request

    async def _run_one(self, session: Session) -> None:
        request = self._request_for(session)
        if request is None:
            await session.close()
            return
        handle = None
        try:
            handle = await serve_session(self.engine, session, request)
        except Exception as exc:                          # noqa: BLE001
            # One session failing is not the server failing. It is logged
            # here rather than swallowed, because a transport that loses
            # runs quietly is the failure nobody finds in production.
            LOGGER.error(
                f"[serve:{self.spec.name}] session run failed: "
                f"{type(exc).__name__}: {exc}"
            )
        finally:
            await self._close_one(session, handle)

    async def _close_one(self, session: Session, handle: Any) -> None:
        """Whatever `on_session` opened, close — even on the failure path.

        Runs after the run has ended however it ended, which is the only
        place a counter gets decremented and a record gets written exactly
        once. Its own failure is contained: a teardown hook that raises
        must not take out the accounting that follows it.
        """
        if self._on_close is None:
            return
        try:
            result = self._on_close(session, handle)
            if inspect.isawaitable(result):
                await result
        except Exception as exc:                          # noqa: BLE001
            LOGGER.error(
                f"[serve:{self.spec.name}] on_close failed: "
                f"{type(exc).__name__}: {exc}"
            )

    async def run(self) -> None:
        """Accept sessions until the transport stops, then drain."""
        LOGGER.info(
            f"[serve:{self.spec.name}] {self.spec.kind} -> {self.spec.graph} "
            f"({self.spec.session}"
            + (f", max_inflight={self.spec.max_inflight}" if self.spec.max_inflight else "")
            + ")"
        )
        try:
            async for session in self.transport.sessions():
                task = asyncio.ensure_future(self._run_one(session))
                self._runs.add(task)
                task.add_done_callback(self._runs.discard)
        finally:
            if self._runs:
                await asyncio.gather(*list(self._runs), return_exceptions=True)

    async def close(self) -> None:
        await self.transport.close()
