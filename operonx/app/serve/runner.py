"""Sessions in, runs out — the hop that was never an op.

`operonx.toml` says it plainly: *"nothing derived from the graph can say
this: uvicorn calls an ASGI route, which calls engine.start(), and that
hop is not an op."* This module is that hop, written once, so that no
project has to write it again. The callbot's version of it is 437 lines.
"""

from __future__ import annotations

import asyncio
import dataclasses
import inspect
from collections.abc import Mapping
from typing import Any, Callable, Dict, Optional

from operonx.app.declare import ref_name
from operonx.app.doors import RESERVED_QUERY, BindError, has_doors, plain, serve_inputs
from operonx.app.manifest import STREAM_KINDS, ServeSpec
from operonx.core.loggings import LOGGER
from operonx.core.ops.graph.task_scheduler import FINAL_TIMEOUT_S
from operonx.core.workflow_trace import unhandled

from .protocol import SESSION_KEY, RunRequest, Session
from .registry import resolve_ref, resolve_transport

__all__ = ["RunTimeout", "ServeRunner", "serve_session"]


class RunTimeout(TimeoutError):
    """The run did not finish inside ``timeout``; it was cancelled.

    ``trace_id`` is the cancelled run's: its trace is written as the run
    ends, so the run that hung can still be looked up.
    """

    def __init__(self, message: str, trace_id: Optional[str] = None) -> None:
        super().__init__(message)
        self.trace_id = trace_id


async def _drain(handle: Any) -> None:
    async for _op_name, _ctx, _data in handle:
        pass


async def serve_session(
    engine: Any,
    session: Session,
    request: Optional[RunRequest] = None,
    metadata: Optional[Dict[str, Any]] = None,
    timeout: Optional[float] = None,
    on_start: Optional[Callable[[Any], None]] = None,
    reply: bool = False,
) -> Any:
    """Run `engine` for one session, and return its handle when it ends.

    The session is seeded into the run's scratch under a reserved key, so
    `ingress` and `egress` resolve it from the run rather than from a
    string the author had to remember.

    The run is always allowed to finish. A peer that disappears surfaces as
    the session's `recv` ending, which ends `ingress`, which drains the
    graph — one signal travelling one way, instead of a race between a
    cancel and a teardown.

    ``metadata`` is merged onto the run's trace before it ends, so every
    consumer sees it: a job tags its runs here (``job``, ``job_run``,
    ``key`` and the same three as ``tags``) and a transport could name
    its route the same way. ``tags`` extend the trace's list; other keys
    are set.

    ``timeout`` is a deadline in seconds for the whole run. Past it the
    run is cancelled and :class:`RunTimeout` is raised — the one case in
    which a transport cancels the run it minted, and it does so because
    the caller asked for exactly that. A job's ``item_timeout`` is this.

    ``on_start`` is called with the run's handle as soon as the run has
    started, before it is drained — for a caller that follows the run
    while it happens (the playground watches ``handle.trace`` grow, so
    a canvas can light each op as it finishes). A callback that raises
    is logged; it never stops the run.

    ``reply=True`` is the doorless shape: when the run ends without an
    unhandled error, its outputs (without the ``$`` keys) are sent to the
    session as its one reply, before the session is closed. A failed run
    sends nothing.
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
    trace = getattr(handle, "trace", None)
    if metadata and trace is not None:
        trace.tag(metadata)
    if on_start is not None:
        try:
            on_start(handle)
        except Exception as exc:  # noqa: BLE001 — a follower's failure is its own
            LOGGER.error(f"[serve] on_start failed: {type(exc).__name__}: {exc}")
    try:
        if timeout is None:
            await _drain(handle)
        else:
            try:
                await asyncio.wait_for(_drain(handle), timeout)
            except asyncio.TimeoutError:
                handle.cancel()
                # the cancelled run still runs its op after END: let it, and
                # close the session after it (bounded), not under it
                ending = getattr(handle, "_scheduler_task", None)
                if ending is not None and not ending.done():
                    await asyncio.wait({ending}, timeout=FINAL_TIMEOUT_S + 5)
                raise RunTimeout(
                    f"run exceeded {timeout:g}s", trace_id=getattr(trace, "trace_id", None)
                ) from None
        if reply and not unhandled(handle.errors):
            await session.send(plain(await handle.result()))
    finally:
        # A transport whose `close` raises must not turn a completed run
        # into a failed one. Closing is teardown; its failure is reported
        # where it happened rather than replacing whatever the run did.
        try:
            await session.close()
        except Exception as exc:  # noqa: BLE001
            LOGGER.error(f"[serve] session close failed: {type(exc).__name__}: {exc}")
    return handle


def _note_trace_id(session: Session, handle: Any) -> None:
    """Record the run's trace id on the session that minted it, where the
    transport can hand it to its peer (`BoundedSession.trace_id`)."""
    trace = getattr(handle, "trace", None)
    if trace is not None and hasattr(session, "trace_id"):
        session.trace_id = trace.trace_id
    if hasattr(session, "handle"):  # a queued session: how its run is stopped
        session.handle = handle


class ServeRunner:
    """Drives one `[[serve]]` entry: a transport, a graph, and its runs.

    Each session becomes one run for ``per_connection``, which is the
    shape a phone call needs. ``per_request`` and ``per_message`` differ
    only in how many runs a session mints, and that difference is the only
    thing the session mode decides at this level — what a *failure* means
    is decided by the same field one layer up, where a transport can turn
    it into a status code.
    """

    def __init__(
        self,
        engine: Any,
        spec: ServeSpec,
        transport: Any = None,
        variants: Optional[Dict[str, Any]] = None,
    ):
        # One engine, or one per variant — never both. A door with
        # variants has no default: `on_session` names one every time.
        self.engine = engine
        self.variants: Dict[str, Any] = dict(variants or {})
        self.spec = spec
        self.transport = transport if transport is not None else resolve_transport(spec.kind)(spec)
        self._on_session = (
            resolve_ref(spec.on_session, field=f"[[serve]] {spec.name!r} on_session")
            if spec.on_session
            else None
        )
        self._on_close = (
            resolve_ref(spec.on_close, field=f"[[serve]] {spec.name!r} on_close")
            if spec.on_close
            else None
        )
        self._runs: set = set()

    def _request_for(self, session: Session) -> Optional[RunRequest]:
        # A transport that can refuse before completing its handshake asks
        # this early and stores the answer, so the decision is made exactly
        # once per connection however the transport is shaped.
        decided = getattr(session, "run_request", None)
        if decided is not None:
            return decided
        if self._on_session is None:
            return self._bound_request(session)
        try:
            request = self._on_session(session)
        except Exception as exc:  # noqa: BLE001
            # A hook that raises is a refusal, not a crash. Unprotected,
            # the exception left `_run_one` before its try block: the task
            # died with nobody retrieving the error, `on_close` never ran
            # and the socket was never closed — so a project counting
            # active calls in `on_session` inflated that counter
            # permanently, and the count it reports on every log line
            # became fiction. Silence was the worst part.
            LOGGER.error(
                f"[serve:{self.spec.name}] on_session raised, refusing the "
                f"connection: {type(exc).__name__}: {exc}"
            )
            return None
        if request is None:
            LOGGER.info(f"[serve:{self.spec.name}] on_session refused a connection")
        elif not isinstance(request, RunRequest):
            # Without this the wrong type failed several frames later, as
            # an AttributeError on `.inputs` from inside the run — far from
            # the hook that actually returned it.
            LOGGER.error(
                f"[serve:{self.spec.name}] on_session returned "
                f"{type(request).__name__}, expected RunRequest or None; "
                f"refusing the connection"
            )
            return None
        elif self._engine_for(request) is None:
            return None
        elif not self._inputs_fit(request):
            return None
        return request

    def _bound_request(self, session: Session) -> Optional[RunRequest]:
        """No hook: the connection's query fills the graph's parameters.

        The rules for a query, on every door (callbot plan O1): a value of
        ``""`` is absent — the default applies, or a required parameter is
        refused; a key the graph does not take is ignored (a caller adding
        one must not break the door); the last of a repeated key wins.
        ``?variant=`` picks the engine first, since variants may take
        different parameters. A graph with doors — a stream — is bound here,
        before its handshake is answered; a doorless one is bound with its
        body when the run starts (`_bind`). ``None`` refuses, and the reason
        is kept on the session (``refusal``) for the transport to say.
        """
        meta = getattr(session, "meta", None) or {}
        query = {str(k): v for k, v in (meta.get("query") or {}).items()}
        variant = None
        if self.variants:
            variant = query.get("variant") or next(iter(self.variants))
            if variant not in self.variants:
                return self._turn_away(
                    session,
                    BindError(
                        f"unknown variant {variant!r}; declared: {sorted(self.variants)}",
                        field="variant",
                    ),
                )
        engine = self.variants[variant] if self.variants else self.engine
        params = self._params(engine)
        given = {k: v for k, v in query.items() if k in params and v != ""}
        request = RunRequest(
            inputs=given,
            variant=variant,
            # a webhook mints the run id it answered with (`meta["trace_id"]`)
            trace_id=query.get("trace_id") or meta.get("trace_id"),
        )
        if not self._doorless(engine):
            try:
                request.inputs = serve_inputs(
                    params, given, None, defaults=getattr(engine, "inputs_defaults", None)
                )
            except BindError as exc:
                return self._turn_away(session, exc)
            self._declared_trace_id(request)
        return request

    def _turn_away(self, session: Session, exc: BindError) -> None:
        """Refuse a connection at the door, saying why where it can be read:
        the log, and ``session.refusal`` (the playground shows it)."""
        LOGGER.warning(f"[serve:{self.spec.name}] refused a connection: {exc}")
        try:
            session.refusal_reason = {"error": str(exc), "field": exc.field}
        except AttributeError:  # a slotted project session: the log says it
            pass
        return None

    def _declared_trace_id(self, request: RunRequest) -> None:
        """``Service(trace_id="<param>")``: that parameter's value is the
        run's id, over a ``?trace_id=`` (the studio finds a call by it)."""
        field = self.spec.options.get("trace_id")
        if field and request.inputs.get(field) not in (None, ""):
            request.trace_id = str(request.inputs[field])

    def _inputs_fit(self, request: RunRequest) -> bool:
        """The graph's runtime parameters are the door's contract: a
        declared hook must build exactly them. A mismatch is refused here,
        naming both sides, not a None deep in the first op that reads one.
        The default hook (the query string as inputs) is not held to it."""
        if self.spec.on_session is None:
            return True
        engine = self._engine_for(request)
        expected = getattr(engine, "inputs_expected", None)
        if expected is None or set(request.inputs) == set(expected):
            return True
        # Doorless: the body fills what the hook leaves; checked as it binds.
        missing = [] if self._doorless(engine) else sorted(set(expected) - set(request.inputs))
        extra = sorted(set(request.inputs) - set(expected))
        if not missing and not extra:
            return True
        LOGGER.error(
            f"[serve:{self.spec.name}] on_session built inputs {sorted(request.inputs)}; "
            f"the graph takes {sorted(expected)}"
            + (f" — missing {missing}" if missing else "")
            + (f" — not a parameter {extra}" if extra else "")
            + " — refusing the connection"
        )
        return False

    def _engine_for(self, request: RunRequest) -> Any:
        """The engine this request runs, or None (logged) when the variant
        it names is not one the door has. Decided at the gate, so a
        refusal is a refusal — not a run that fails on its first item."""
        if not self.variants:
            return self.engine
        if request.variant is None:
            LOGGER.error(
                f"[serve:{self.spec.name}] on_session returned no variant; the door "
                f"declares {sorted(self.variants)} — refusing the connection"
            )
            return None
        engine = self.variants.get(request.variant)
        if engine is None:
            LOGGER.error(
                f"[serve:{self.spec.name}] unknown variant {request.variant!r}; "
                f"declared: {sorted(self.variants)} — refusing the connection"
            )
        return engine

    def _doorless(self, engine: Any) -> bool:
        """Served by its signature: no ingress door, on a one-shot door. A
        stream door's graph without doors runs as before (its items unread)."""
        return engine is not None and self.spec.kind not in STREAM_KINDS and not has_doors(engine)

    @staticmethod
    def _params(engine: Any) -> list:
        expected = getattr(engine, "inputs_expected", None)
        return list(expected) if expected is not None else list(engine.graph.inputs)

    def _serve_inputs(
        self, engine: Any, given: Mapping[str, Any], item: Any, tick: bool = False
    ) -> Dict[str, Any]:
        return serve_inputs(
            self._params(engine),
            given,
            item,
            defaults=getattr(engine, "inputs_defaults", None),
            tick=tick,
            # the default hook hands over the whole query; a project's hook
            # builds exactly what it means
            reserved=RESERVED_QUERY if self.spec.on_session is None else (),
            input=self.spec.options.get("input"),
        )

    async def _bind(self, engine: Any, request: RunRequest, session: Session) -> RunRequest:
        """A doorless run's inputs: the hook's (or the query's), plus the
        session's one item — its body, or a schedule's tick."""
        item = None
        async for item in session.recv():
            break
        meta = getattr(session, "meta", None) or {}
        tick = meta.get("trigger") == "schedule"
        return dataclasses.replace(
            request, inputs=self._serve_inputs(engine, request.inputs, item, tick=tick)
        )

    def precheck(
        self, payload: Any, meta: Optional[Mapping[str, Any]] = None
    ) -> Optional[BindError]:
        """Whether a request fits a doorless graph, decided before the door
        answers (a webhook's ``202``) and before any run. ``None`` when it
        fits, or the graph has doors.

        Without an ``on_session`` hook this is the check the run makes.
        With one, the hook's inputs are not known yet, so only the body's
        fields are checked against the graph's parameters."""
        engines = list(self.variants.values()) or [self.engine]
        if not all(self._doorless(e) for e in engines):
            return None
        try:
            if self.spec.on_session is None and not self.variants:
                query = dict((meta or {}).get("query") or {})
                self._serve_inputs(self.engine, query, payload)
            elif isinstance(payload, Mapping) and not self.spec.options.get("input"):
                params = {p for e in engines for p in self._params(e)}
                unknown = [k for k in payload if k not in params]
                if unknown:
                    raise BindError(
                        f"{unknown[0]!r} is not a parameter of the graph "
                        f"(it takes {sorted(params) or 'nothing'})",
                        field=str(unknown[0]),
                    )
        except BindError as exc:
            return exc
        return None

    async def _refuse(self, session: Session, exc: BindError) -> None:
        """A doorless request that does not fit: no run. The session is told
        (an http door answers ``400``), and the hooks that opened something
        close it."""
        LOGGER.info(f"[serve:{self.spec.name}] refused a request: {exc}")
        refuse = getattr(session, "refuse", None)
        if refuse is not None:
            refuse(400, {"error": str(exc), "endpoint": self.spec.name, "field": exc.field})
        await self._report_failure(session)
        await session.close()
        await self._close_one(session, None)

    async def _run_one(self, session: Session) -> None:
        request = self._request_for(session)
        if request is None:
            why = getattr(session, "refusal_reason", None)
            refuse = getattr(session, "refuse", None)
            if why and refuse is not None:  # an http door answers 400, naming the field
                refuse(400, {**why, "endpoint": self.spec.name})
            await session.close()
            return
        handle = None
        failed = False
        engine = self._engine_for(request)
        doorless = self._doorless(engine)
        metadata = self._origin(request)
        served = self._recorded(session, metadata) if self.spec.options.get("replay") else session
        if doorless:
            try:
                request = await self._bind(engine, request, served)
            except BindError as exc:
                await self._refuse(session, exc)
                return
            self._declared_trace_id(request)
        try:
            handle = await serve_session(
                engine,
                served,
                request,
                metadata=metadata,
                on_start=lambda h: _note_trace_id(session, h),
                reply=doorless,
            )
            failed = bool(unhandled(handle.errors))
        except Exception as exc:  # noqa: BLE001
            # One session failing is not the server failing. It is logged
            # here rather than swallowed, because a transport that loses
            # runs quietly is the failure nobody finds in production.
            failed = True
            LOGGER.error(
                f"[serve:{self.spec.name}] session run failed: {type(exc).__name__}: {exc}"
            )
        finally:
            if failed:
                await self._report_failure(session)
            await self._close_one(session, handle)
            await self._finish(session, failed)

    async def _finish(self, session: Session, failed: bool) -> None:
        """The run is over (its op after END included): a transport that
        holds a connection open closes it — a websocket with ``1000``, or
        ``1011`` when the run failed. Before, only the peer ever closed one,
        and a peer that kept sending after the run hung the socket's reader."""
        finish = getattr(session, "finish", None)
        if finish is None:
            return
        try:
            await finish(1011 if failed else 1000)
        except Exception as exc:  # noqa: BLE001
            LOGGER.error(f"[serve:{self.spec.name}] finish failed: {type(exc).__name__}: {exc}")

    async def _report_failure(self, session: Session) -> None:
        """Let the session tell its peer the run failed (`run_failed`).

        Optional on a project's own session; its failure is contained, like
        `on_close`'s, so the accounting after it still runs."""
        hook = getattr(session, "run_failed", None)
        if hook is None:
            return
        try:
            await hook()
        except Exception as exc:  # noqa: BLE001
            LOGGER.error(f"[serve:{self.spec.name}] run_failed failed: {type(exc).__name__}: {exc}")

    def _recorded(self, session: Session, metadata: Dict[str, Any]) -> Session:
        """``Service(replay=True)``: what the client sends is written down
        on the run — a script of toy messages and the connection's query,
        the same shape a playground session keeps — so a real session can
        be replayed later. Text and JSON are kept; audio and bytes counted."""
        from operonx.app.play import Codec, codec_for

        try:
            codec = codec_for(self.spec)
        except Exception as exc:  # noqa: BLE001 — a broken codec records nothing, loudly
            LOGGER.error(f"[serve:{self.spec.name}] replay: no codec ({type(exc).__name__}: {exc})")
            return session
        script: list = []
        metadata["replay_script"] = script  # the same list: filled as the client sends
        metadata["replay_query"] = dict((getattr(session, "meta", None) or {}).get("query") or {})
        return _Recorded(session, codec or Codec(), script)

    def _origin(self, request: RunRequest) -> Dict[str, Any]:
        """What this door's runs carry: the service, its transport, and
        the variant the request chose — so a run is found by service."""
        from ..origin import ORIGIN_SERVICE, origin_metadata

        return origin_metadata(
            ORIGIN_SERVICE,
            service=self.spec.name,
            transport=self.spec.kind,
            variant=request.variant,
        )

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
        except Exception as exc:  # noqa: BLE001
            LOGGER.error(f"[serve:{self.spec.name}] on_close failed: {type(exc).__name__}: {exc}")

    async def run(self) -> None:
        """Accept sessions until the transport stops, then drain."""
        LOGGER.info(
            f"[serve:{self.spec.name}] {self.spec.kind} -> {ref_name(self.spec.graph)} "
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


class _Recorded:
    """A session whose inbound items are written down as they are read.

    Only the reading side is wrapped: the transport keeps feeding its own
    session object, and everything else is that session's."""

    def __init__(self, inner: Session, codec: Any, script: list):
        self._inner, self._codec, self._script = inner, codec, script

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)

    async def recv(self):  # noqa: ANN201 — an async iterator, as Session.recv
        from operonx.app.play import SCRIPT_LIMIT, script_entry

        async for item in self._inner.recv():
            if len(self._script) < SCRIPT_LIMIT:
                try:
                    message = self._codec.to_toy(item)
                except Exception:  # noqa: BLE001 — a codec that cannot say is counted as bytes
                    message = {"kind": "bytes", "size": len(repr(item))}
                self._script.append(script_entry(message))
            yield item

    async def send(self, item: Any) -> bool:
        return await self._inner.send(item)

    async def close(self) -> None:
        await self._inner.close()
