"""`on_startup` and `on_close` — the hooks the callbot migration demanded.

Neither was in the plan. Both turned up the moment the serve layer had to
carry a real system instead of a graph written to suit it:

* `on_session` had no symmetric half, so nothing could decrement a CCU
  counter or write a CRM record exactly once on every path — including the
  paths where the run failed.
* Once `operonx serve` owns the process, it owns process startup. Warming
  an LLM connection after the first caller has arrived is the same as not
  warming it: measured at 0.6-1.2 s that the first caller of the day hears.
"""

import pytest

from operonx.core import END, START, Operon, graph
from operonx.core.manifest import ServeSpec
from operonx.core.ops import op
from operonx.core.serve import MemoryTransport, ServeRunner, egress, ingress

CALLS = {"opened": 0, "closed": 0, "started": 0, "handles": []}


def reset():
    CALLS.update(opened=0, closed=0, started=0)
    CALLS["handles"].clear()


@op(bound="sync")
def passthrough(item=None) -> dict:
    return {"out": item}


@graph
def tiny():
    src = ingress()
    mid = passthrough(item=src["item"])
    out = egress(item=mid["out"])
    START >> src >> mid >> out >> END


@op(bound="sync")
def explode(item=None) -> dict:
    raise RuntimeError("this turn is lost")


@graph
def broken():
    src = ingress()
    boom = explode(item=src["item"])
    START >> src >> boom >> END


def note_startup():
    CALLS["started"] += 1


def note_close(session, handle):
    CALLS["closed"] += 1
    CALLS["handles"].append(handle)


def raising_close(session, handle):
    raise RuntimeError("teardown blew up")


def _spec(**kw) -> ServeSpec:
    base = dict(name="t", kind="memory", graph="x:y", session="per_connection",
                max_inflight=16)
    base.update(kw)
    return ServeSpec(**base)


@pytest.mark.asyncio
async def test_on_close_runs_once_per_session_with_the_handle():
    reset()
    transport = MemoryTransport()
    runner = ServeRunner(Operon(tiny), _spec(), transport=transport)
    runner._on_close = note_close

    for word in ("a", "b"):
        s = transport.open()
        await s.feed(word)
        s.end_input()
    transport.stop()
    await runner.run()

    assert CALLS["closed"] == 2
    assert all(h is not None for h in CALLS["handles"])
    # The handle carries the graph's name, which is the first half of every
    # declared cell key — without it a teardown hook cannot read what the
    # run decided, and the callbot filed ARId=UNKNOWN for exactly that.
    assert all(h.graph_name for h in CALLS["handles"])


@pytest.mark.asyncio
async def test_on_close_still_runs_when_the_run_fails():
    """The path that matters: a record written on the bad calls too."""
    reset()
    transport = MemoryTransport()
    runner = ServeRunner(Operon(broken), _spec(), transport=transport)
    runner._on_close = note_close

    s = transport.open()
    await s.feed("x")
    s.end_input()
    transport.stop()
    await runner.run()

    assert CALLS["closed"] == 1


@pytest.mark.asyncio
async def test_a_raising_on_close_does_not_take_out_the_server():
    reset()
    transport = MemoryTransport()
    runner = ServeRunner(Operon(tiny), _spec(), transport=transport)
    runner._on_close = raising_close

    s = transport.open()
    await s.feed("x")
    s.end_input()
    transport.stop()
    await runner.run()          # must not raise
    assert s.sent == ["x"]


def test_on_startup_runs_before_any_endpoint_accepts():
    pytest.importorskip("starlette")
    import importlib

    from starlette.testclient import TestClient

    from operonx.core.serve.app import build_app

    # `load_object` imports by path, and pytest may hold this module under
    # a different name — so the hook increments that copy's counter, not
    # necessarily this one. Assert against the copy the loader actually got.
    here = "tests.internal.core.serve.test_lifecycle_hooks"
    mod = importlib.import_module(here)
    mod.reset()

    spec = ServeSpec(name="h", kind="http", path="/go", method="POST",
                     graph=f"{here}:tiny")
    app = build_app((spec,), engines={"h": Operon(tiny)},
                    on_startup=(f"{here}:note_startup",))
    assert mod.CALLS["started"] == 0          # not at build time
    with TestClient(app) as client:
        assert mod.CALLS["started"] == 1      # before the first request
        assert client.post("/go", json="hi").json() == "hi"
    assert mod.CALLS["started"] == 1          # once, not per request


# -- the guard the callbot's worst bug asked for -------------------------

@pytest.mark.parametrize("raw", ["[1,2,3]", '"hello"', "42", "true", "null",
                                 "not json at all", "", "{"])
def test_json_object_never_returns_a_non_dict(raw):
    """Five of these parse cleanly and are not objects.

    That is the whole trap: guarding `JSONDecodeError` guards only the
    last three. A callbot shipped this on a query parameter the telco
    controls, and every one of `[1,2,3]`, `"hello"`, `42`, `true` and
    `null` killed the call at accept.
    """
    from operonx.core.serve import json_object

    assert json_object(raw) == {}


def test_json_object_reports_what_it_rejected():
    """Falling back silently is how the same input goes unnoticed twice."""
    from operonx.core.serve import json_object

    seen = []
    assert json_object("[1,2,3]", default={"ok": 1}, on_reject=seen.append) == {"ok": 1}
    assert seen == [[1, 2, 3]]


def test_json_object_passes_a_real_object_through():
    from operonx.core.serve import json_object

    assert json_object('{"a": 1}') == {"a": 1}
    assert json_object({"already": "a dict"}) == {"already": "a dict"}


def test_a_refused_connection_never_completes_the_handshake():
    """Refusing is not accepting-then-closing.

    A callbot's endpoint declared call_id, phone_number and customer_id as
    required query params, so a bare connect was refused with 403. Moving
    to the serve layer accepted every socket and only then asked
    `on_session` — so a call with no identity at all got a successful
    upgrade followed by silence. The peer cannot tell that from a server
    that died.
    """
    pytest.importorskip("starlette")
    import websockets.exceptions
    from starlette.testclient import TestClient
    from starlette.websockets import WebSocketDisconnect

    from operonx.core.serve.app import build_app

    spec = ServeSpec(name="w", kind="websocket", path="/ws",
                     session="per_connection", max_inflight=8,
                     graph="tests.internal.core.serve.test_lifecycle_hooks:tiny")
    app = build_app((spec,), engines={"w": Operon(tiny)})
    app.state.operonx_runners[0]._on_session = lambda session: None

    with TestClient(app) as client:
        with pytest.raises((WebSocketDisconnect, Exception)) as exc:
            with client.websocket_connect("/ws"):
                pass
    # Starlette answers an un-accepted close with an HTTP rejection rather
    # than a websocket close frame.
    assert exc.value is not None


def test_send_reports_whether_the_item_reached_the_peer():
    """`Session.send` returns a bool so a pacing caller can audit itself.

    The callbot's `play_frame` sends forty-odd chunks per spoken frame and
    reports chunks/ok/fail/failed-indices — the row you read when audio
    sounds wrong. A fire-and-forget send would have forced that op to keep
    talking to the socket directly, which is the exact coupling this
    interface exists to remove.
    """
    import asyncio

    from operonx.core.serve import MemoryTransport

    class DeadPeer(MemoryTransport().open().__class__):
        async def _send(self, item):
            return False

    async def check():
        good = MemoryTransport().open()
        assert await good.send("x") is True
        bad = DeadPeer()
        assert await bad.send("x") is False

    asyncio.run(check())
