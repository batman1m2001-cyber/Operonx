"""The playground bridge — a service's doors, driven from outside.

The studio's playground puts a *toy* on a service's doors: a form, a chat
box, later a microphone. The run itself must happen in the project's own
interpreter (the studio never imports a project), so this module is a
small process the studio starts there and talks to in JSON lines::

    python -m operonx.app.play [--root DIR]      # stdin: requests, stdout: events

Nothing here is a second way to run a service. A playground session is a
:class:`~operonx.app.serve.protocol.BoundedSession` driven through the
same :class:`~operonx.app.serve.runner.ServeRunner` gate (the service's own
``on_session`` hook, its variants, its input contract), the same
:func:`~operonx.app.serve.runner.serve_session`, the same door ops and the
same trace consumers as production — only the origin differs:
``origin=playground``, so the run is filed apart and kept 7 days.

**Codecs.** Toys speak one small protocol — messages ``{"kind": "text",
"text": …}``, ``{"kind": "json", "value": …}``, ``{"kind": "bytes", "size":
…}`` — and a door speaks whatever it speaks. A codec translates between
them: :meth:`Codec.to_door` turns a toy message into an ingress item,
:meth:`Codec.from_door` an egress item into a toy message. The built-ins
cover plain transports (http JSON, websocket text); a service whose
protocol is its own declares ``Service(..., playground=MyCodec)`` (or
``playground = "module:attr"`` in ``[[serve]]``). A door with no codec
offers no toy. Session hooks can tell a playground session apart by
``session.meta["playground"]``.

**Re-running one op.** ``rerun`` runs a single op of a service's graph
with the inputs it had in a recorded run — a failing op deep inside a
call retried in a second, without making the call. It is recorded as a
run of its own (``toy=rerun``, ``rerun_of=<run>``).

Requests (one JSON object per line; ``id`` is echoed back)::

    {"op": "describe"}
    {"op": "open",  "sid": "s1", "service": "chat", "query": {...}, "variant": null,
                    "send": [msg, ...], "end": false}
    {"op": "send",  "sid": "s1", "msg": {"kind": "text", "text": "hi"}}
    {"op": "end",   "sid": "s1"}
    {"op": "rerun", "service": "chat", "op_name": "reply", "inputs": {...}, "of": "run-id"}

Events: ``doors``, ``opened``, ``refused``, ``out`` (an egress item, as a
toy message), ``ended`` (status, error, duration), ``rerun``, ``error``.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import inspect
import json
import os
import sys
import threading
import time
import uuid
from time import perf_counter
from typing import Any, Callable, Dict, List, Optional, Tuple

from operonx.core.loggings import LOGGER

from .origin import ORIGIN_PLAYGROUND, origin_metadata
from .serve.protocol import BoundedSession, RunRequest
from .serve.registry import resolve_ref

__all__ = ["Bridge", "Codec", "JsonCodec", "TextCodec", "codec_for", "main", "toy_message"]

PROTOCOL = 1
#: A session's script (what the toy sent) is kept on the trace for replay,
#: up to this many messages; bytes are counted, never stored.
SCRIPT_LIMIT = 500


# ── codecs ────────────────────────────────────────────────────────────────


def toy_message(item: Any) -> Dict[str, Any]:
    """Any item as a toy message: text, json, or bytes (sized, and a short
    base64 preview so a toy can play or show it later)."""
    if isinstance(item, str):
        return {"kind": "text", "text": item}
    if isinstance(item, (bytes, bytearray)):
        return {"kind": "bytes", "size": len(item), "b64": base64.b64encode(bytes(item[:65536])).decode()}
    try:
        json.dumps(item)
        return {"kind": "json", "value": item}
    except (TypeError, ValueError):
        return {"kind": "json", "value": repr(item)}


class Codec:
    """Toy messages ↔ door items. Subclass for a door with its own protocol.

    ``toys`` names the toys the codec can drive (``form``, ``chat``; the
    voice toy arrives with audio codecs).
    """

    toys: Tuple[str, ...] = ()

    def to_door(self, message: Dict[str, Any]) -> Any:
        kind = message.get("kind")
        if kind == "text":
            return str(message.get("text", ""))
        if kind == "json":
            return message.get("value")
        if kind == "bytes":
            return base64.b64decode(message.get("b64") or "")
        raise ValueError(f"a toy message has kind text, json or bytes — got {kind!r}")

    def from_door(self, item: Any) -> Dict[str, Any]:
        return toy_message(item)


class JsonCodec(Codec):
    """An http door: a JSON payload in, JSON replies out — the Form toy."""

    toys = ("form",)


class TextCodec(Codec):
    """A websocket door: text frames both ways (the Chat toy); JSON frames
    are sent as objects and come back as events."""

    toys = ("chat", "form")


BUILTIN_CODECS: Dict[str, Callable[[], Codec]] = {"http": JsonCodec, "websocket": TextCodec}


def codec_for(spec: Any) -> Optional[Codec]:
    """The codec a service's playground uses: its declared one, else the
    built-in for its transport, else None (no toy)."""
    declared = spec.options.get("playground")
    if declared:
        obj = resolve_ref(declared, field=f"[[serve]] {spec.name!r} playground")
        return obj() if isinstance(obj, type) else obj
    make = BUILTIN_CODECS.get(spec.kind)
    return make() if make else None


# ── sessions ──────────────────────────────────────────────────────────────


class PlaySession(BoundedSession):
    """A session whose peer is a toy: egress items become ``out`` events."""

    def __init__(self, sid: str, meta: Dict[str, Any], emit: Callable[[Dict[str, Any]], None],
                 codec: Codec, max_inflight: Optional[int] = None):
        super().__init__(meta=meta, max_inflight=max_inflight)
        self.sid, self.emit, self.codec = sid, emit, codec
        #: what the toy sent, in order — shared with the trace's metadata
        #: (the list object itself), so the finished run carries it
        self.script: List[Dict[str, Any]] = []
        self.sent = 0

    async def _send(self, item: Any) -> bool:
        try:
            message = self.codec.from_door(item)
        except Exception as exc:  # noqa: BLE001 — a codec bug is shown, not fatal
            message = {"kind": "error", "text": f"codec: {type(exc).__name__}: {exc}"}
        self.sent += 1
        self.emit({"t": "out", "sid": self.sid, "msg": message, "at": time.time()})
        return True

    def note(self, message: Dict[str, Any]) -> None:
        if len(self.script) < SCRIPT_LIMIT:
            kept = dict(message)
            if kept.get("kind") == "bytes":
                kept = {"kind": "bytes", "size": len(base64.b64decode(kept.get("b64") or ""))}
            self.script.append(kept)


def _status(trace: Any) -> Tuple[str, Optional[str]]:
    """A finished trace's status and its first error, as one line."""
    for node in getattr(trace, "nodes", None) or []:
        if getattr(node, "status", "ok") == "error":
            lines = str(node.error or "").strip().splitlines()
            return "error", f"{node.op_name}: {lines[-1] if lines else 'failed'}"
    return "ok", None


def _jsonable(value: Any) -> Any:
    return json.loads(json.dumps(value, default=lambda v: f"<{len(v)} bytes>" if isinstance(v, (bytes, bytearray))
                                  else repr(v)))


# ── the bridge ────────────────────────────────────────────────────────────


class Bridge:
    """Services of one application, driven by toy messages."""

    def __init__(self, app: Any, emit: Callable[[Dict[str, Any]], None]):
        self.app = app
        self.emit = emit
        self._runners: Dict[str, Any] = {}
        self._started_hooks: set = set()
        self._sessions: Dict[str, PlaySession] = {}
        self._tasks: set = set()

    # -- what there is ---------------------------------------------------

    def describe(self) -> Dict[str, Any]:
        doors = []
        for spec in self.app.services:
            if spec.kind == "asgi":
                continue
            try:
                codec = codec_for(spec)
                codec_err = None
            except Exception as exc:  # noqa: BLE001
                codec, codec_err = None, f"{type(exc).__name__}: {exc}"
            try:
                graph_fn = resolve_ref(spec.graph, field=f"[[serve]] {spec.name!r} graph")
                params = list(inspect.signature(graph_fn).parameters)
            except Exception:  # noqa: BLE001
                params = []
            doors.append({
                "service": spec.name, "kind": spec.kind, "session": spec.session, "path": spec.path,
                "inputs": params, "variants": list(spec.variants), "custom_hook": bool(spec.on_session),
                "toys": list(codec.toys) if codec else [],
                "codec": type(codec).__name__ if codec else None, "codec_error": codec_err,
                "description": spec.description,
            })
        return {"t": "doors", "protocol": PROTOCOL, "project": self.app.name, "doors": doors}

    # -- engines, compiled once per service -------------------------------

    async def _runner(self, service: str) -> Any:
        runner = self._runners.get(service)
        if runner is not None:
            return runner
        from .serve.app import _default_on_session, engines_for
        from .serve.memory import MemoryTransport
        from .serve.runner import ServeRunner

        spec = self.app.service(service)
        built = engines_for(spec)
        if spec.variants:
            engine, variants = None, {k.partition("/")[2]: e for k, e in built.items()}
        else:
            engine, variants = built[spec.name], None
        runner = ServeRunner(engine, spec, transport=MemoryTransport(), variants=variants)
        if runner._on_session is None:
            runner._on_session = _default_on_session(spec)
        # the application's startup hooks, then this service's — once each,
        # as a served worker would run them before accepting
        for hook_ref in (*self.app.manifest.on_startup, *spec.on_startup):
            key = id(hook_ref) if not isinstance(hook_ref, str) else hook_ref
            if key in self._started_hooks:
                continue
            self._started_hooks.add(key)
            result = resolve_ref(hook_ref, field="on_startup")()
            if inspect.isawaitable(result):
                await result
        self._runners[service] = runner
        return runner

    # -- sessions ----------------------------------------------------------

    async def open(self, msg: Dict[str, Any]) -> None:
        from .serve.runner import serve_session

        sid = str(msg.get("sid") or uuid.uuid4().hex[:12])
        service = str(msg.get("service") or "")
        runner = await self._runner(service)
        spec = runner.spec
        codec = codec_for(spec)
        if codec is None:
            self.emit({"t": "refused", "sid": sid, "reason": f"{service} has no playground codec"})
            return
        meta = {"query": {str(k): str(v) for k, v in (msg.get("query") or {}).items()},
                "headers": {}, "path": spec.path, "client": "playground", "playground": True}
        session = PlaySession(sid, meta, self.emit, codec, spec.max_inflight)
        request = runner._request_for(session)
        if request is None:
            self.emit({"t": "refused", "sid": sid,
                       "reason": "the service's on_session refused the session (see the bridge log)"})
            return
        if not isinstance(request, RunRequest):  # pragma: no cover — the gate returns RunRequest|None
            return
        request.trace_id = request.trace_id or str(uuid.uuid4())
        self._sessions[sid] = session
        toy = str(msg.get("toy") or (codec.toys[0] if codec.toys else "form"))
        metadata = origin_metadata(ORIGIN_PLAYGROUND, service=spec.name, transport=spec.kind,
                                   variant=request.variant, toy=toy)
        metadata["playground_script"] = session.script  # the same list: filled as the toy sends
        metadata["playground_query"] = dict(meta["query"])  # with the script, all a replay needs
        if msg.get("replay_of"):
            metadata["replay_of"] = str(msg["replay_of"])
        self.emit({"t": "opened", "sid": sid, "trace_id": request.trace_id, "service": spec.name,
                   "variant": request.variant, "inputs": _jsonable(request.inputs)})

        async def run() -> None:
            t0, handle, error = perf_counter(), None, None
            try:
                handle = await serve_session(runner._engine_for(request), session, request,
                                             metadata=metadata)
            except Exception as exc:  # noqa: BLE001 — reported as the session's end
                error = f"{type(exc).__name__}: {exc}"
            finally:
                await runner._close_one(session, handle)
                self._sessions.pop(sid, None)
            status, first = _status(getattr(handle, "trace", None))
            self.emit({"t": "ended", "sid": sid, "trace_id": request.trace_id,
                       "status": "error" if error else status, "error": error or first,
                       "ms": round((perf_counter() - t0) * 1000, 2), "sent": session.sent})

        task = asyncio.ensure_future(run())
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        for m in msg.get("send") or []:
            await self.send({"sid": sid, "msg": m})
        if msg.get("end"):
            self.end({"sid": sid})

    async def send(self, msg: Dict[str, Any]) -> None:
        session = self._sessions.get(str(msg.get("sid")))
        if session is None:
            self.emit({"t": "error", "sid": msg.get("sid"), "text": "no such open session"})
            return
        message = dict(msg.get("msg") or {})
        item = session.codec.to_door(message)
        session.note(message)
        await session.feed(item)

    def end(self, msg: Dict[str, Any]) -> None:
        session = self._sessions.get(str(msg.get("sid")))
        if session is not None:
            session.end_input()

    # -- one op, again -----------------------------------------------------

    async def rerun(self, msg: Dict[str, Any]) -> Dict[str, Any]:
        """Run one op with recorded inputs, recorded as a run of its own."""
        from operonx.core.states import MemoryState, StateSchema
        from operonx.core.workflow_trace import WorkflowTrace, _current_trace, run_metadata

        runner = await self._runner(str(msg.get("service") or ""))
        engine = runner.engine or next(iter(runner.variants.values()))
        if msg.get("variant") and runner.variants:
            engine = runner.variants[str(msg["variant"])]
        name = str(msg.get("op_name") or "")
        op = _find_op(engine.graph, name)
        if op is None:
            return {"t": "rerun", "error": f"no op {name!r} in {runner.spec.name}'s graph", "status": "error"}
        trace = WorkflowTrace(
            trace_id=str(uuid.uuid4()), workflow_name=engine.name, started_at=perf_counter(),
            wall_started_at=time.time(), ended_at=0.0,
            metadata={**run_metadata(), **origin_metadata(
                ORIGIN_PLAYGROUND, service=runner.spec.name, transport=runner.spec.kind, toy="rerun",
                rerun_of=msg.get("of"), op=op.name)},
        )
        outputs: List[Any] = []
        token = _current_trace.set(trace)
        t0 = perf_counter()
        try:
            state = MemoryState(StateSchema(op=op), inputs=dict(msg.get("inputs") or {}))
            async for _ctx, result in op.run(state):
                outputs.append(result)
        except Exception as exc:  # noqa: BLE001 — the op's own failure is recorded on its node
            LOGGER.error(f"[play] rerun {name} raised: {type(exc).__name__}: {exc}")
        finally:
            _current_trace.reset(token)
            trace.ended_at = perf_counter()
        ms = (perf_counter() - t0) * 1000
        for consumer in getattr(engine, "_trace_consumers", None) or []:
            try:
                await asyncio.to_thread(consumer.consume, trace)
            except Exception:  # noqa: BLE001
                LOGGER.exception("trace consumer failed on a playground rerun")
        status, error = _status(trace)
        return {"t": "rerun", "trace_id": trace.trace_id, "op": op.name, "status": status, "error": error,
                "ms": round(ms, 3), "outputs": _jsonable(outputs[-1] if len(outputs) == 1 else outputs)}

    # -- the loop ----------------------------------------------------------

    async def handle(self, msg: Dict[str, Any]) -> None:
        kind = msg.get("op")
        rid = msg.get("id")
        try:
            if kind == "describe":
                out = self.describe()
            elif kind == "open":
                await self.open(msg)
                return
            elif kind == "send":
                await self.send(msg)
                return
            elif kind == "end":
                self.end(msg)
                return
            elif kind == "rerun":
                out = await self.rerun(msg)
            else:
                out = {"t": "error", "text": f"unknown op {kind!r}"}
        except Exception as exc:  # noqa: BLE001 — one bad request never ends the bridge
            LOGGER.exception(f"[play] {kind} failed")
            out = {"t": "error", "sid": msg.get("sid"), "text": f"{type(exc).__name__}: {exc}"}
        if rid is not None:
            out["id"] = rid
        self.emit(out)

    async def drain(self) -> None:
        for session in list(self._sessions.values()):
            session.end_input()
        if self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)


def _find_op(graph: Any, name: str) -> Any:
    """An op of *graph* (nested graphs included) by name or full name."""
    for op_name, op in (getattr(graph, "_ops", None) or {}).items():
        if name in (op_name, getattr(op, "full_name", None)):
            return op
        inner = _find_op(op, name) if getattr(op, "_ops", None) else None
        if inner is not None:
            return inner
    return None


async def serve_stdio(app: Any) -> None:
    """Read requests from stdin, write events to stdout, until stdin ends.

    stdout carries the protocol and nothing else. The protocol keeps a
    private copy of file descriptor 1 and fd 1 itself becomes stderr, so
    anything else that writes there — ``print``, a log handler that
    captured ``sys.stdout`` at import — lands in the log instead.
    """
    sys.stdout.flush()
    proto = os.fdopen(os.dup(1), "w", encoding="utf-8")
    os.dup2(2, 1)
    lock = threading.Lock()

    def emit(event: Dict[str, Any]) -> None:
        line = json.dumps(event, default=repr)
        with lock:
            proto.write(line + "\n")
            proto.flush()

    loop = asyncio.get_running_loop()
    inbox: asyncio.Queue = asyncio.Queue()

    def reader() -> None:
        for line in sys.stdin:
            loop.call_soon_threadsafe(inbox.put_nowait, line)
        loop.call_soon_threadsafe(inbox.put_nowait, None)

    threading.Thread(target=reader, daemon=True, name="operonx-play-stdin").start()
    bridge = Bridge(app, emit)
    emit({"t": "ready", "protocol": PROTOCOL, "project": app.name})
    pending: set = set()
    while True:
        line = await inbox.get()
        if line is None:
            break
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except ValueError:
            emit({"t": "error", "text": "not JSON"})
            continue
        if not isinstance(msg, dict):
            continue
        # a rerun or an open may take a while; the bridge keeps reading
        task = asyncio.ensure_future(bridge.handle(msg))
        pending.add(task)
        task.add_done_callback(pending.discard)
    if pending:
        await asyncio.gather(*list(pending), return_exceptions=True)
    await bridge.drain()


def main(argv: Optional[List[str]] = None) -> int:
    from .application import Application

    parser = argparse.ArgumentParser(prog="operonx-play", description=__doc__.split("\n\n")[0])
    parser.add_argument("--root", default=".", help="the project (an operonx.toml at or above it)")
    args = parser.parse_args(argv)
    app = Application.find(args.root)
    app.bootstrap()
    asyncio.run(serve_stdio(app))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
