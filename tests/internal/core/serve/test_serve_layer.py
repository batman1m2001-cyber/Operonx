"""The serve layer: manifest, transport protocol, and the two ops.

The gate that matters here is `test_third_party_transport_drives_a_graph`.
A transport written against the public protocol only — no operonx
internals, no inheritance from a built-in — has to drive a real graph end
to end. If it cannot, the extension point is decorative, and shipping a
WebSocket implementation would cement that by letting the built-in quietly
become the contract.
"""

import asyncio
from typing import Any, AsyncIterator

import pytest

from operonx.core import END, START, Operon, graph
from operonx.core.manifest import (
    Manifest,
    ManifestError,
    ServeSpec,
    _toml,  # the tomllib/tomli pick that ships
)
from operonx.core.ops import op
from operonx.core.serve import (
    MemoryTransport,
    RunRequest,
    ServeRunner,
    current_session,
    egress,
    ingress,
    resolve_transport,
    serve_session,
)

# -- manifest ------------------------------------------------------------

MANIFEST = """
[project]
name = "demo"

[[serve]]
name = "call"
kind = "websocket"
path = "/ws/call"
port = "${DEMO_PORT:9922}"
graph = "demo.pipeline:main"
session = "per_connection"
max_inflight = 4000

[[serve]]
name = "predict"
kind = "http"
path = "/v1/predict"
port = 8080
graph = "demo.pipeline:predict"
"""


def test_a_manifest_parses_and_fills_in_what_it_leaves_out():
    m = Manifest.from_dict(_toml.loads(MANIFEST))
    call = m.serve("call")
    assert call.graph == "demo.pipeline:main"
    assert call.session == "per_connection"
    assert call.port == 9922            # ${DEMO_PORT:9922} default applied
    assert call.max_inflight == 4000
    # http defaults to per_request without being told
    assert m.serve("predict").session == "per_request"


def test_graph_must_be_an_entry_point_not_a_name():
    """A bare name used to mean "look it up in [[graph]]".

    That indirection existed so `[[serve]]` had something to refer to. Now
    serve names its entry point outright, and a name is just wrong.
    """
    with pytest.raises(ManifestError, match="not a `module:function`"):
        Manifest.from_dict({"serve": [
            {"kind": "http", "path": "/x", "graph": "pipeline"}]})


def test_a_stream_kind_must_declare_a_bound():
    """Required of everyone, always.

    A version key used to make this conditional so that older manifests
    could skip it. That was the key's only real job, and letting an
    unbounded queue sit behind a socket is not a kindness worth keeping —
    it is how `operonx.io.Channel` came to exist.
    """
    with pytest.raises(ManifestError, match="max_inflight"):
        Manifest.from_dict({"serve": [
            {"kind": "websocket", "path": "/ws", "graph": "m:g"}]})


def test_asgi_mounts_an_app_and_takes_no_graph():
    m = Manifest.from_dict({"serve": [
        {"name": "admin", "kind": "asgi", "path": "/", "app": "demo.admin:app"}]})
    assert m.serve("admin").app == "demo.admin:app"
    with pytest.raises(ManifestError, match="cannot also name a `graph`"):
        Manifest.from_dict({"serve": [
            {"kind": "asgi", "app": "a:b", "graph": "m:g"}]})


def test_endpoints_group_onto_listeners_by_address():
    m = Manifest.from_dict({"serve": [
        {"name": "a", "kind": "http", "path": "/a", "port": 8080, "graph": "m:g"},
        {"name": "b", "kind": "http", "path": "/b", "port": 8080, "graph": "m:g"},
        {"name": "c", "kind": "http", "path": "/c", "port": 9090, "graph": "m:g"}]})
    listeners = m.listeners()
    assert len(listeners) == 2
    assert [s.name for s in listeners[("0.0.0.0", 8080)]] == ["a", "b"]


def test_duplicate_routes_are_refused():
    with pytest.raises(ManifestError, match="both serve"):
        Manifest.from_dict({"serve": [
            {"name": "a", "kind": "http", "path": "/x", "graph": "m:g"},
            {"name": "b", "kind": "http", "path": "/x", "graph": "m:g"}]})


# -- a graph that is served ---------------------------------------------

@op(bound="sync")
def shout(item: str = "") -> dict:
    return {"reply": f"{item}!"}


@graph
def echo_pipeline():
    src = ingress()
    loud = shout(item=src["item"])
    out = egress(item=loud["reply"])
    START >> src >> loud >> out >> END


ENGINE = Operon(echo_pipeline)


@pytest.mark.asyncio
async def test_memory_transport_round_trip():
    transport = MemoryTransport()
    session = transport.open(meta={"who": "test"})
    for word in ("a", "b", "c"):
        await session.feed(word)
    session.end_input()

    await serve_session(ENGINE, session)
    assert session.sent == ["a!", "b!", "c!"]
    assert session.closed


@pytest.mark.asyncio
async def test_run_finishes_when_the_peer_vanishes():
    """recv ending drains the graph; nothing cancels the run.

    This is what lets work that must happen after the peer has gone —
    writing a call record, filing a CRM row — actually happen.
    """
    transport = MemoryTransport()
    session = transport.open()
    await session.feed("only")
    session.end_input()                       # peer hung up
    await serve_session(ENGINE, session)
    assert session.sent == ["only!"]          # the item still went through


@pytest.mark.asyncio
async def test_graph_with_ingress_still_runs_unserved():
    """No session: the same graph is testable with a plain start()."""
    handle = ENGINE.start(inputs={"items": ["x", "y"]})
    async for _ in handle:
        pass
    # No session to send to, and that is not an error.


@pytest.mark.asyncio
async def test_bound_is_enforced_where_items_enter():
    session = MemoryTransport(max_inflight=2).open()
    assert session.feed_nowait("1") is True
    assert session.feed_nowait("2") is True
    assert session.feed_nowait("3") is False      # bound reached
    assert session.overflowed == 1                # counted, never silent


@pytest.mark.asyncio
async def test_on_session_hook_can_refuse_a_connection():
    spec = ServeSpec(name="t", kind="memory", graph="x:y", max_inflight=8)
    transport = MemoryTransport()
    runner = ServeRunner(ENGINE, spec, transport=transport)
    runner._on_session = lambda session: None     # refuse everything

    session = transport.open()
    transport.stop()
    await runner.run()
    assert session.closed and session.sent == []


# -- the gate ------------------------------------------------------------

class ThirdPartySession:
    """Implements the protocol. Inherits nothing, imports no internals."""

    def __init__(self, script):
        self.meta = {"origin": "third-party"}
        self._script = list(script)
        self.received = []
        self.closed = False

    async def recv(self) -> AsyncIterator[Any]:
        for item in self._script:
            await asyncio.sleep(0)
            yield item

    async def send(self, item: Any) -> None:
        self.received.append(item)

    async def close(self) -> None:
        self.closed = True


class ThirdPartyTransport:
    def __init__(self, scripts):
        self._scripts = list(scripts)
        self.sessions_made = []

    async def sessions(self):
        for script in self._scripts:
            s = ThirdPartySession(script)
            self.sessions_made.append(s)
            yield s

    async def close(self) -> None:
        pass


@pytest.mark.asyncio
async def test_third_party_transport_drives_a_graph():
    """The gate: no built-in, no inheritance, no operonx internals.

    Written before any network transport exists, so the interface cannot
    be quietly defined by its first implementation.
    """
    spec = ServeSpec(name="third", kind="x:y", graph="m:g", session="per_connection")
    transport = ThirdPartyTransport([["hello", "world"], ["again"]])
    await ServeRunner(ENGINE, spec, transport=transport).run()

    first, second = transport.sessions_made
    assert first.received == ["hello!", "world!"]
    assert second.received == ["again!"]
    assert first.closed and second.closed


def test_a_transport_can_be_named_by_import_path():
    """`kind = "module:Class"` needs no registration at all."""
    resolved = resolve_transport(
        "tests.internal.core.serve.test_serve_layer:ThirdPartyTransport")
    assert resolved.__name__ == "ThirdPartyTransport"
