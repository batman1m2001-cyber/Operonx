"""A Service is a socket and a graph (callbot plan revision 2, O1–O7).

The connection's data is the graph's parameters on every door, a stream
included: bound from the handshake query before the socket is accepted,
refused when a required one is missing. ``trace_id=`` names the parameter
a run is found by, ``?variant=`` picks a variant, the graph's own
``END >> op`` closes what a run opened, and the door closes the socket
when the run is over. ``on_session`` / ``on_close`` / ``session`` are
deprecated.
"""

from __future__ import annotations

import asyncio
import logging
import time
import warnings

import pytest
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from operonx import END, START, graph, op
from operonx.app import Application, Service, http, webhook, websocket
from operonx.app.doors import serve_inputs
from operonx.app.manifest import ManifestError
from operonx.app.serve import current_session, egress, ingress
from operonx.core.runtime import run_context

pytestmark = pytest.mark.unit

SEEN: list = []


@pytest.fixture(autouse=True)
def _clear():
    SEEN.clear()
    yield
    SEEN.clear()


def _wait(predicate, timeout=5.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if predicate():
            return True
        time.sleep(0.02)
    return False


# ── a call-shaped graph: parameters from the query, a door for the stream ──


@op
async def open_call(call_id: str, agent: str) -> dict:
    ctx = run_context()
    SEEN.append(("open", call_id, agent, ctx.run_id if ctx else None))
    return {"call_id": call_id}


@op
def echo(item=None) -> dict:
    return {"said": item}


@op
async def close_call(call_id: str = "") -> dict:
    session = current_session()
    sent = await session.send({"event": "bye", "call_id": call_id}) if session else None
    SEEN.append(("close", call_id, sent))
    return {}


@graph
def call(call_id, agent="reminder"):
    opened = open_call(call_id=call_id, agent=agent)
    src = ingress()
    said = echo(item=src["item"])
    out = egress(item=said["said"])
    closed = close_call(call_id=call_id)
    START >> opened >> src >> said >> out >> END
    END >> closed


def _call(**kw):
    return Service("call", websocket("/ws", port=8951), graph=call, max_inflight=16, **kw)


def _app(*services):
    return Application("t", services=list(services)).asgi()


class TestTheQueryIsTheParameters:
    def test_bound_before_the_handshake_extra_keys_ignored(self):
        with TestClient(_app(_call())) as client:
            with client.websocket_connect("/ws?call_id=c1&lead_id=9&campaign_id=") as ws:
                ws.send_json({"t": "hi"})
                assert ws.receive_json() == {"t": "hi"}
        assert _wait(lambda: any(s[0] == "close" for s in SEEN))
        assert SEEN[0][:3] == ("open", "c1", "reminder")

    @pytest.mark.parametrize("query", ["", "?call_id=", "?agent=x"])
    def test_a_missing_or_empty_required_parameter_is_refused(self, query, caplog):
        caplog.set_level(logging.INFO)
        with TestClient(_app(_call())) as client:
            with pytest.raises(WebSocketDisconnect):
                with client.websocket_connect(f"/ws{query}"):
                    pass
        assert SEEN == []  # no run
        assert "missing required parameter 'call_id'" in caplog.text

    def test_the_last_of_a_repeated_key_wins(self):
        with TestClient(_app(_call())) as client:
            with client.websocket_connect("/ws?call_id=a&call_id=b"):
                pass
        assert _wait(lambda: any(s[0] == "close" for s in SEEN))
        assert SEEN[0][1] == "b"

    def test_trace_id_names_the_parameter_a_run_is_found_by(self):
        with TestClient(_app(_call(trace_id="call_id"))) as client:
            with client.websocket_connect("/ws?call_id=c-77&trace_id=other"):
                pass
        assert _wait(lambda: any(s[0] == "close" for s in SEEN))
        assert SEEN[0][3] == "c-77"


class TestTheRunEnds:
    def test_the_op_after_end_talks_to_the_peer_then_the_door_closes(self):
        with TestClient(_app(_call())) as client:
            with client.websocket_connect("/ws?call_id=c2") as ws:
                ws.send_json({"t": "x"})
                assert ws.receive_json() == {"t": "x"}
                ws.send_json({"event": "stop"})  # still open: the peer stays
        assert _wait(lambda: any(s[0] == "close" for s in SEEN))
        assert ("close", "c2", False) in SEEN or ("close", "c2", True) in SEEN

    def test_the_server_closes_a_socket_whose_run_ended(self):
        """The run ends when its ingress ends; here a graph that reads one
        item. The peer then hears the bye and a close, instead of an open
        socket nobody reads (which used to hang the reader)."""
        with TestClient(
            _app(Service("one", websocket("/one", port=8952), graph=one_item, max_inflight=2))
        ) as client:
            with client.websocket_connect("/one?call_id=c3") as ws:
                ws.send_json("only")
                assert ws.receive_json() == {"event": "bye", "call_id": "c3"}
                for _ in range(10):  # more than the bound: must not hang
                    ws.send_json("late")
                with pytest.raises(WebSocketDisconnect) as closed:
                    ws.receive_json()
        assert closed.value.code == 1000


@op(door="ingress")
async def take_one(n: int = 1) -> dict:
    session = current_session()
    async for item in session.recv():
        return {"item": item}
    return {"item": None}


@graph
def one_item(call_id):
    first = take_one()
    closed = close_call(call_id=call_id)
    START >> first >> END
    END >> closed


# ── variants by query, webhook run ids ──────────────────────────────────


@op
def greet(who: str, style: str) -> dict:
    return {"text": f"{style} {who}"}


@graph
def greeter(who, style):
    g = greet(who=who, style=style)
    START >> g >> END


def test_variant_is_picked_by_query_first_declared_by_default_unknown_refused():
    service = Service(
        "greet",
        http("POST", "/greet", port=8953),
        graph=greeter,
        variants={"formal": {"style": "Dear"}, "casual": {"style": "Hey"}},
    )
    with TestClient(_app(service)) as client:
        assert client.post("/greet?variant=casual", json={"who": "Lan"}).json() == {
            "text": "Hey Lan"
        }
        assert client.post("/greet", json={"who": "Lan"}).json() == {"text": "Dear Lan"}
        refused = client.post("/greet?variant=rude", json={"who": "Lan"})
    assert refused.status_code == 400
    assert refused.json()["field"] == "variant"


@op
def note(call_id: str) -> dict:
    SEEN.append(call_id)
    return {"ok": True}


@graph
def hooked(call_id):
    n = note(call_id=call_id)
    START >> n >> END


def test_a_webhook_answers_with_the_declared_run_id():
    service = Service("hook", webhook("/hook", port=8954), graph=hooked, trace_id="call_id")
    with TestClient(_app(service)) as client:
        reply = client.post("/hook?trace_id=ignored", json={"call_id": "w-1"})
    assert reply.status_code == 202
    assert reply.json()["run_id"] == "w-1"
    assert _wait(lambda: SEEN == ["w-1"])


# ── what is checked when the app is built ───────────────────────────────


@graph
def takes_variant(variant):
    n = note(call_id=variant)
    START >> n >> END


@graph
def with_default(call_id="x"):
    n = note(call_id=call_id)
    START >> n >> END


def test_a_parameter_named_like_a_door_read_is_refused():
    with pytest.raises(ManifestError, match="'variant'"):
        _app(Service("v", http("POST", "/v", port=8955), graph=takes_variant))


@pytest.mark.parametrize(
    "graph_fn, field, match",
    [(hooked, "nope", "takes"), (with_default, "call_id", "must be required")],
)
def test_trace_id_must_name_a_required_parameter(graph_fn, field, match):
    with pytest.raises(ManifestError, match=match):
        _app(Service("t", http("POST", "/t", port=8956), graph=graph_fn, trace_id=field))


def test_key_ops_name_ops_the_graph_has():
    with pytest.raises(ManifestError, match="no op called"):
        _app(_call(key_ops=["opened", "transcribe"]))
    _app(_call(key_ops=["opened", "said"]))  # names that exist build


def test_the_hooks_are_deprecated():
    from operonx.app import declare

    declare._WARNED_HOOKS.clear()
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        _call(on_session=lambda s: None, on_close=lambda s, h: None, session="per_connection")
    names = " ".join(str(w.message) for w in caught if w.category is DeprecationWarning)
    assert "on_session=" in names and "on_close=" in names and "session=" in names


def test_a_default_is_a_copy_per_request():
    defaults = {"seen": []}  # one @graph default, shared by every request
    first = serve_inputs(["x", "seen"], {"x": 1}, None, defaults=defaults)
    first["seen"].append("a")
    second = serve_inputs(["x", "seen"], {"x": 2}, None, defaults=defaults)
    assert second["seen"] == [] and defaults["seen"] == []


@op(door="ingress")
async def never_ends(n: int = 1):
    while True:
        await asyncio.sleep(0.01)
        yield {"item": n}


@op
async def check_open() -> dict:
    session = current_session()
    SEEN.append(("open at end", not session._finished))
    return {}


@graph
def times_out():
    src = never_ends()
    c = check_open()
    START >> src >> END
    END >> c


@pytest.mark.asyncio
async def test_a_timed_out_run_runs_its_end_op_before_the_session_closes():
    from operonx.app.serve import MemoryTransport, RunTimeout, serve_session
    from operonx.core import Operon

    session = MemoryTransport().open()
    with pytest.raises(RunTimeout):
        await serve_session(Operon(times_out), session, timeout=0.2)
    assert SEEN == [("open at end", True)]
    assert session._finished  # and closed after it
