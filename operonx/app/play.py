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

**Audio.** A codec that sets ``audio = {"rate": 8000, …}`` offers the
Voice toy: the browser sends ``{"kind": "audio", "b64": <pcm16 mono at that
rate>}`` in short batches and plays what comes back the same way. The
codec cuts a batch into the door's own frames (``to_door_items``).

**Conditions.** A session can run in a worse world than the one it was
built in: ``latency_ms`` before each inbound item, ``drop`` (the share of
inbound items lost), ``noise_dbfs`` (white noise mixed into audio),
``silence_ms`` (quiet audio before the first message) and ``fail`` (resource
keys that raise for this session only — the resource objects are wrapped
once and fail only where a session asked them to).

**A simulated user.** ``simulate`` opens a session and lets an LLM persona
play the other side for a number of turns: it waits for the service to go
quiet, reads the conversation, answers in character, and ends it when the
persona says so. The run records what the persona said, like any session.

**Where it is recorded.** A playground run goes to the service's local
consumers only — files and run stores in the project — so a test session
never lands in a production Langfuse; ``"remote": true`` on a session
sends it everywhere the service traces to.

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
    {"op": "simulate", "sid": "s2", "service": "chat", "persona": "a busy parent…", "llm": "llm:x",
                       "turns": 6, "first": "user", "conditions": {...}}
    {"op": "rerun", "service": "chat", "op_name": "reply", "inputs": {...}, "of": "run-id"}
    {"op": "rerun", "job": "score_calls", "op_name": "scored", "inputs": {...}}

Events: ``doors``, ``opened``, ``refused``, ``out`` (an egress item, as a
toy message), ``said`` (what a simulated user sent), ``ended`` (status,
error, duration), ``rerun``, ``error``.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import inspect
import json
import contextvars
import math
import os
import random
import struct
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

__all__ = ["Bridge", "Codec", "JsonCodec", "PcmCodec", "TextCodec", "codec_for", "main", "toy_message"]

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
    #: For a voice door: ``{"rate": 8000, "encoding": "pcm16", "frame_ms": 40}``
    #: — the rate the toy captures and plays at.
    audio: Optional[Dict[str, Any]] = None
    #: Connection defaults the toy fills in (``{uuid}`` becomes a fresh id).
    query: Dict[str, str] = {}

    def to_door(self, message: Dict[str, Any]) -> Any:
        kind = message.get("kind")
        if kind == "text":
            return str(message.get("text", ""))
        if kind == "json":
            return message.get("value")
        if kind in ("bytes", "audio"):
            return base64.b64decode(message.get("b64") or "")
        raise ValueError(f"a toy message has kind text, json, bytes or audio — got {kind!r}")

    def to_door_items(self, message: Dict[str, Any]) -> List[Any]:
        """The ingress items one toy message becomes — one, unless the door
        takes audio in frames smaller than the toy's batches."""
        return [self.to_door(message)]

    def from_door(self, item: Any) -> Dict[str, Any]:
        return toy_message(item)


class JsonCodec(Codec):
    """An http door: a JSON payload in, JSON replies out — the Form toy."""

    toys = ("form",)


class TextCodec(Codec):
    """A websocket door: text frames both ways (the Chat toy); JSON frames
    are sent as objects and come back as events."""

    toys = ("chat", "form")


class PcmCodec(Codec):
    """A door that takes raw 16-bit mono PCM frames and sends them back —
    the Voice toy. Declared, never guessed: ``Service(...,
    playground=PcmCodec(rate=16000, frame_ms=20))``. What the door sends
    that is not audio (a transcript, an event) still reaches the toy."""

    toys = ("voice",)

    def __init__(self, rate: int = 16000, frame_ms: int = 20):
        self.audio = {"rate": int(rate), "encoding": "pcm16", "frame_ms": int(frame_ms)}

    def to_door_items(self, message: Dict[str, Any]) -> List[Any]:
        if message.get("kind") != "audio":
            return [self.to_door(message)]
        return list(pcm_frames(base64.b64decode(message.get("b64") or ""), self.audio["rate"],
                               self.audio["frame_ms"]))

    def from_door(self, item: Any) -> Dict[str, Any]:
        if isinstance(item, (bytes, bytearray)):
            return {"kind": "audio", "b64": base64.b64encode(bytes(item)).decode(), "rate": self.audio["rate"]}
        return toy_message(item)


def pcm_frames(pcm: bytes, rate: int, frame_ms: int) -> List[bytes]:
    """*pcm* (16-bit mono) cut into frames of *frame_ms*; the last is padded
    with silence, as a telco pads its final packet."""
    size = max(2, int(rate * frame_ms / 1000) * 2)
    out = [pcm[i:i + size] for i in range(0, len(pcm), size)]
    if out and len(out[-1]) < size:
        out[-1] = out[-1] + b"\x00" * (size - len(out[-1]))
    return out


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


# ── conditions ────────────────────────────────────────────────────────────

#: The resource keys failing for the session this task belongs to.
_FAILING: contextvars.ContextVar = contextvars.ContextVar("operonx_play_failing", default=frozenset())
_WRAPPED: set = set()


class PlaygroundFault(RuntimeError):
    """A resource failed because a playground session asked it to."""


def _fail_resources(keys: List[str]) -> None:
    """Make *keys* able to fail: wrap their resource objects' public methods
    once, so a call raises when — and only when — the calling session lists
    the key. Ops hold the same objects the hub hands out, so this reaches a
    warmed op too."""
    from operonx.core.registry import ResourceHub

    hub = ResourceHub.instance()
    for key in keys:
        if key in _WRAPPED:
            continue
        obj = hub.get(key)
        if obj is None:
            raise KeyError(f"no resource {key!r} to fail")
        for name in dir(obj):
            if name.startswith("_"):
                continue
            try:
                attr = getattr(obj, name)
            except Exception:  # noqa: BLE001 — a property that raises is not a method
                continue
            if not inspect.ismethod(attr):
                continue
            setattr(obj, name, _faulty(key, attr))
        _WRAPPED.add(key)


def _faulty(key: str, method: Callable) -> Callable:
    if inspect.iscoroutinefunction(method):
        async def wrapped(*a: Any, **kw: Any) -> Any:
            if key in _FAILING.get():
                raise PlaygroundFault(f"{key} failed (a playground condition)")
            return await method(*a, **kw)
    elif inspect.isasyncgenfunction(method):
        async def wrapped(*a: Any, **kw: Any) -> Any:  # type: ignore[misc]
            if key in _FAILING.get():
                raise PlaygroundFault(f"{key} failed (a playground condition)")
            async for x in method(*a, **kw):
                yield x
    else:
        def wrapped(*a: Any, **kw: Any) -> Any:  # type: ignore[misc]
            if key in _FAILING.get():
                raise PlaygroundFault(f"{key} failed (a playground condition)")
            return method(*a, **kw)
    wrapped.__name__ = getattr(method, "__name__", "method")
    return wrapped


def add_noise(pcm: bytes, dbfs: float, seed: Optional[int] = None) -> bytes:
    """16-bit mono *pcm* with white noise at *dbfs* (e.g. -30) mixed in."""
    rng = random.Random(seed)
    amp = 32767 * (10 ** (float(dbfs) / 20))
    n = len(pcm) // 2
    samples = struct.unpack(f"<{n}h", pcm[: n * 2])
    mixed = (max(-32768, min(32767, int(x + rng.gauss(0, amp)))) for x in samples)
    return struct.pack(f"<{n}h", *mixed)


def _conditions(raw: Any) -> Dict[str, Any]:
    raw = raw if isinstance(raw, dict) else {}
    out: Dict[str, Any] = {}
    if raw.get("latency_ms"):
        out["latency_ms"] = max(0.0, float(raw["latency_ms"]))
    if raw.get("drop"):
        out["drop"] = min(1.0, max(0.0, float(raw["drop"])))
    if raw.get("noise_dbfs") not in (None, ""):
        out["noise_dbfs"] = min(0.0, float(raw["noise_dbfs"]))
    if raw.get("silence_ms"):
        out["silence_ms"] = max(0.0, float(raw["silence_ms"]))
    fail = [str(k) for k in raw.get("fail") or [] if str(k).strip()]
    if fail:
        out["fail"] = fail
    return out


# ── where playground runs are recorded ──────────────────────────────────


def _split_consumers(trace: Any) -> Tuple[List[Any], List[Any]]:
    """A service's consumers as (local, remote): local ones write files or a
    run store in the project; the rest (Langfuse…) ship somewhere else."""
    from operonx.core.engine import Operon
    from operonx.telemetry.consumers.local import LocalConsumer
    from operonx.telemetry.runs.base import RunStore

    consumers = Operon._resolve_trace_consumers(trace)
    local = [c for c in consumers if isinstance(c, (LocalConsumer, RunStore))]
    return local, [c for c in consumers if c not in local]


def _remote_names(spec: Any) -> List[str]:
    """The consumers a playground session leaves out unless asked — by the
    names the service declared them with."""
    names = []
    for ref in spec.options.get("trace") or []:
        if isinstance(ref, str):
            if not (ref.startswith("trace_local:") or ref.startswith("run_store:")):
                try:
                    _, remote = _split_consumers([ref])
                except Exception:  # noqa: BLE001 — unresolvable: not ours to judge here
                    continue
                if remote:
                    names.append(ref)
        elif not _split_consumers([ref])[0]:
            names.append(type(ref).__name__)
    return names


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
        self.conditions: Dict[str, Any] = {}
        self.dropped = 0
        self.lock = asyncio.Lock()  # inbound order holds under latency
        self.heard: List[Dict[str, Any]] = []  # what came back — a simulated user reads it
        self.last_out = 0.0
        self.first_in = True

    async def _send(self, item: Any) -> bool:
        try:
            message = self.codec.from_door(item)
        except Exception as exc:  # noqa: BLE001 — a codec bug is shown, not fatal
            message = {"kind": "error", "text": f"codec: {type(exc).__name__}: {exc}"}
        self.sent += 1
        self.last_out = time.monotonic()
        self.heard.append(message)
        self.emit({"t": "out", "sid": self.sid, "msg": message, "at": time.time()})
        return True

    def note(self, message: Dict[str, Any]) -> None:
        if len(self.script) < SCRIPT_LIMIT:
            kept = dict(message)
            if kept.get("kind") in ("bytes", "audio"):
                # audio and bytes are counted, never kept: a replay script is text
                kept = {"kind": kept["kind"], "size": len(base64.b64decode(kept.get("b64") or ""))}
            kept["at"] = round(time.time(), 3)  # when it was said: a conversation reads in order
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
                "audio": getattr(codec, "audio", None) if codec else None,
                "remote_trace": _remote_names(spec),
                "query": dict(getattr(codec, "query", None) or {}) if codec else {},
                "description": spec.description,
            })
        return {"t": "doors", "protocol": PROTOCOL, "project": self.app.name, "doors": doors}

    # -- engines, compiled once per service -------------------------------

    async def _runner(self, service: str, remote: bool = False) -> Any:
        """The service's runner, its engines compiled once — tracing to the
        service's local consumers only, unless *remote* (then to all of them:
        a playground session lands in Langfuse only when asked to)."""
        cache_key = (service, bool(remote))
        runner = self._runners.get(cache_key)
        if runner is not None:
            return runner
        from .serve.app import _default_on_session, engines_for
        from .serve.memory import MemoryTransport
        from .serve.runner import ServeRunner

        spec = self.app.service(service)
        if spec.options.get("trace") is None:
            # a playground session is always a run you can open: a door
            # with no consumers declared records locally, as a job does
            # (an explicit empty list still means "trace nothing")
            from dataclasses import replace

            from .declare import default_consumers

            spec = replace(spec, options={**spec.options, "trace": default_consumers()})
        if not remote:
            from dataclasses import replace

            local, _held = _split_consumers(spec.options["trace"])
            spec = replace(spec, options={**spec.options, "trace": local})
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
        self._runners[cache_key] = runner
        return runner

    # -- sessions ----------------------------------------------------------

    async def open(self, msg: Dict[str, Any]) -> None:
        from .serve.runner import serve_session

        sid = str(msg.get("sid") or uuid.uuid4().hex[:12])
        service = str(msg.get("service") or "")
        runner = await self._runner(service, remote=bool(msg.get("remote")))
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
        session.conditions = _conditions(msg.get("conditions"))
        if session.conditions.get("fail"):
            try:
                _fail_resources(session.conditions["fail"])
            except Exception as exc:  # noqa: BLE001
                self.emit({"t": "refused", "sid": sid, "reason": f"cannot fail {exc}"})
                return
        self._sessions[sid] = session
        toy = str(msg.get("toy") or (codec.toys[0] if codec.toys else "form"))
        metadata = origin_metadata(ORIGIN_PLAYGROUND, service=spec.name, transport=spec.kind,
                                   variant=request.variant, toy=toy)
        metadata["playground_script"] = session.script  # the same list: filled as the toy sends
        metadata["playground_query"] = dict(meta["query"])  # with the script, all a replay needs
        if session.conditions:
            metadata["playground_conditions"] = dict(session.conditions)
        for key, value in (msg.get("meta") or {}).items():
            if key in ("persona", "simulated_by"):
                metadata[key] = str(value)[:2000]
        if msg.get("replay_of"):
            metadata["replay_of"] = str(msg["replay_of"])
        self.emit({"t": "opened", "sid": sid, "trace_id": request.trace_id, "service": spec.name,
                   "variant": request.variant, "inputs": _jsonable(request.inputs)})

        async def run() -> None:
            t0, handle, error = perf_counter(), None, None
            # the run's tasks inherit this: its failing resources fail here only
            _FAILING.set(frozenset(session.conditions.get("fail") or ()))
            try:
                handle = await serve_session(runner._engine_for(request), session, request,
                                             metadata=metadata)
            except Exception as exc:  # noqa: BLE001 — reported as the session's end
                error = f"{type(exc).__name__}: {exc}"
            finally:
                await runner._close_one(session, handle)
                self._sessions.pop(sid, None)
            status, first = _status(getattr(handle, "trace", None))
            ended = {"t": "ended", "sid": sid, "trace_id": request.trace_id,
                     "status": "error" if error else status, "error": error or first,
                     "ms": round((perf_counter() - t0) * 1000, 2), "sent": session.sent}
            if session.dropped:
                ended["dropped"] = session.dropped
            self.emit(ended)

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
        cond = session.conditions
        async with session.lock:  # one message at a time, in the order sent
            if cond.get("latency_ms"):
                await asyncio.sleep(cond["latency_ms"] / 1000)
            if message.get("kind") == "audio" and cond.get("noise_dbfs") is not None:
                noisy = add_noise(base64.b64decode(message.get("b64") or ""), cond["noise_dbfs"])
                message = {**message, "b64": base64.b64encode(noisy).decode()}
            items = session.codec.to_door_items(message)
            if session.first_in and cond.get("silence_ms") and session.codec.audio:
                # quiet before the first word, in the door's own framing
                rate = int(session.codec.audio.get("rate") or 8000)
                quiet = b"\x00\x00" * int(rate * cond["silence_ms"] / 1000)
                items = session.codec.to_door_items({"kind": "audio", "b64": base64.b64encode(quiet).decode(),
                                                     "rate": rate}) + items
            session.first_in = False
            session.note(message)
            for item in items:
                if cond.get("drop") and random.random() < cond["drop"]:
                    session.dropped += 1
                    continue
                await session.feed(item)

    def end(self, msg: Dict[str, Any]) -> None:
        session = self._sessions.get(str(msg.get("sid")))
        if session is not None:
            session.end_input()

    # -- a simulated user -------------------------------------------------

    async def simulate(self, msg: Dict[str, Any]) -> None:
        """An LLM persona plays the other side of a text session."""
        sid = str(msg.get("sid") or uuid.uuid4().hex[:12])
        persona = str(msg.get("persona") or "").strip()
        llm = str(msg.get("llm") or "").strip()
        if not persona or not llm:
            self.emit({"t": "refused", "sid": sid, "reason": "a simulated user needs a persona and an llm"})
            return
        runner = await self._runner(str(msg.get("service") or ""))
        codec = codec_for(runner.spec)
        if codec is None or "chat" not in codec.toys:
            self.emit({"t": "refused", "sid": sid, "reason": "a simulated user speaks text, and this door "
                       f"takes {', '.join(codec.toys) if codec else 'no toy'}"})
            return
        turns = max(1, min(int(msg.get("turns") or 6), 50))
        quiet = max(0.1, float(msg.get("quiet_ms") or 1200) / 1000)
        wait_max = max(1.0, float(msg.get("wait_s") or 45))
        await self.open({"sid": sid, "service": runner.spec.name, "toy": "simulated", "query": msg.get("query"),
                         "conditions": msg.get("conditions"), "remote": msg.get("remote"),
                         "meta": {"persona": persona, "simulated_by": llm}})
        session = self._sessions.get(sid)
        if session is None:
            return  # refused, and already said so
        history: List[Tuple[str, str]] = []

        async def replies() -> str:
            """What the service says next: wait for it to start and then go
            quiet; the text of everything it sent meanwhile."""
            mark, start = len(session.heard), time.monotonic()
            while time.monotonic() - start < wait_max and sid in self._sessions:
                if len(session.heard) > mark and time.monotonic() - session.last_out >= quiet:
                    break
                await asyncio.sleep(0.03)
            said = []
            for m in session.heard[mark:]:
                if m.get("kind") == "text" or m.get("text"):
                    said.append(str(m.get("text") or ""))
                elif m.get("kind") == "json":
                    said.append(json.dumps(m.get("value"), ensure_ascii=False, default=str)[:600])
            return " ".join(t for t in said if t).strip()

        try:
            if (msg.get("first") or "user") == "service":
                heard = await replies()
                if heard:
                    history.append(("service", heard))
            for turn in range(turns):
                if sid not in self._sessions:
                    break
                line = await self._persona_line(llm, persona, history)
                done = "[END]" in line
                line = line.replace("[END]", "").strip()
                if line:
                    self.emit({"t": "said", "sid": sid, "text": line, "turn": turn + 1, "at": time.time()})
                    history.append(("user", line))
                    await self.send({"sid": sid, "msg": {"kind": "text", "text": line}})
                if done or not line:
                    break
                heard = await replies()
                history.append(("service", heard or "(silence)"))
        except Exception as exc:  # noqa: BLE001 — the persona failing ends the session, says why
            # a KeyError's str() is its repr — quotes and escaped newlines; say the message itself
            text = exc.args[0] if isinstance(exc, KeyError) and exc.args else str(exc)
            self.emit({"t": "error", "sid": sid, "text": f"simulated user: {type(exc).__name__}: {text}"})
        finally:
            self.end({"sid": sid})

    async def _persona_line(self, llm: str, persona: str, history: List[Tuple[str, str]]) -> str:
        from operonx.core import END, START, Operon
        from operonx.core.ops.graph.graph_op import GraphOp
        from operonx.providers.ops import LLMOp

        system = ("You are role-playing a person talking to an AI product, to test it. Stay in character.\n"
                  f"Who you are and what you want:\n{persona}\n\n"
                  "Reply with only your next message — no quotes, no narration. Keep it natural and short. "
                  "When you have what you came for, or the conversation is over, reply with [END].")
        messages: List[Dict[str, str]] = [{"role": "system", "content": system}]
        for who, text in history:
            messages.append({"role": "user" if who == "service" else "assistant", "content": text})
        if not history or history[-1][0] == "user":
            messages.append({"role": "user", "content": "(The conversation starts. Say your first line.)"})
        with GraphOp(name="simulated_user") as g:
            node = LLMOp.of(resource=llm.partition(":")[2] if llm.startswith("llm:") else llm,
                            messages=messages, temperature=0.7)
            START >> node >> END
        out = await Operon(g).run(inputs={})
        if out.get("error"):
            raise RuntimeError(str(out["error"]))
        return str(out.get("content") or "").strip()

    # -- one op, again -----------------------------------------------------

    async def rerun(self, msg: Dict[str, Any]) -> Dict[str, Any]:
        """Run one op with recorded inputs, recorded as a run of its own."""
        from operonx.core.states import MemoryState, StateSchema
        from operonx.core.workflow_trace import WorkflowTrace, _current_trace, run_metadata

        if msg.get("job"):
            # a job's graph, compiled as the job compiles it
            job = self.app.job(str(msg["job"]))
            engine, owner, where = job.engine(), {"job": job.name}, f"job {job.name}"
        else:
            runner = await self._runner(str(msg.get("service") or ""))
            engine = runner.engine or next(iter(runner.variants.values()))
            if msg.get("variant") and runner.variants:
                engine = runner.variants[str(msg["variant"])]
            owner = {"service": runner.spec.name, "transport": runner.spec.kind, "variant": msg.get("variant")}
            where = f"{runner.spec.name}'s graph"
        name = str(msg.get("op_name") or "")
        op = _find_op(engine.graph, name)
        if op is None:
            return {"t": "rerun", "error": f"no op {name!r} in {where}", "status": "error"}
        trace = WorkflowTrace(
            trace_id=str(uuid.uuid4()), workflow_name=engine.name, started_at=perf_counter(),
            wall_started_at=time.time(), ended_at=0.0,
            metadata={**run_metadata(), **origin_metadata(
                ORIGIN_PLAYGROUND, **owner, toy="rerun", rerun_of=msg.get("of"), op=op.name)},
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
            elif kind == "simulate":
                await self.simulate(msg)
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
