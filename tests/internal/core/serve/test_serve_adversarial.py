"""What the serve layer does when the things around it misbehave.

The suites in this directory prove the layer works. This one asks what
happens when a project's hook raises, a transport dies mid-accept, a
producer outruns its consumer, or a manifest names something that is not
there — because the serve layer is library code now, and the failures that
matter are the ones a project cannot see coming.
"""

import asyncio

import pytest

from operonx.core import END, START, Operon, graph
from operonx.core.manifest import ServeSpec
from operonx.core.ops import op
from operonx.core.serve import (
    BoundedSession,
    MemorySession,
    MemoryTransport,
    ServeRunner,
    egress,
    ingress,
    resolve_transport,
)


@op(bound="sync")
def echo(item=None) -> dict:
    return {"out": item}


@graph
def echo_graph():
    src = ingress()
    mid = echo(item=src["item"])
    out = egress(item=mid["out"])
    START >> src >> mid >> out >> END


def spec(**kw) -> ServeSpec:
    base = dict(name="t", kind="memory", graph="x:y",
                session="per_connection", max_inflight=8)
    base.update(kw)
    return ServeSpec(**base)


# -- hooks that raise ----------------------------------------------------

@pytest.mark.asyncio
async def test_a_raising_on_session_refuses_and_closes_the_socket():
    """The silent one: unprotected, it leaked a socket and a counter.

    `_request_for` used to run outside `_run_one`'s try block, so an
    exception left the task with nobody retrieving it — `on_close` never
    ran, the session was never closed, and nothing was logged. A project
    counting active calls in `on_session` would inflate that counter for
    the life of the process, and it is the number reported on every
    performance log line.
    """
    transport = MemoryTransport()
    runner = ServeRunner(Operon(echo_graph), spec(), transport=transport)

    def boom(session):
        raise RuntimeError("customer store timed out")

    closed = []
    runner._on_session = boom
    runner._on_close = lambda s, h: closed.append(h)

    session = transport.open()
    await session.feed("x")
    session.end_input()
    transport.stop()
    await runner.run()

    assert session.closed, "a refused session must not be left open"
    assert session.sent == []


@pytest.mark.asyncio
async def test_one_bad_connection_does_not_stop_the_next():
    """A server that dies on one caller's bad data is not a server."""
    transport = MemoryTransport()
    runner = ServeRunner(Operon(echo_graph), spec(), transport=transport)

    seen = {"n": 0}

    def every_other(session):
        seen["n"] += 1
        if seen["n"] == 1:
            raise RuntimeError("first one explodes")
        from operonx.core.serve import RunRequest
        return RunRequest()

    runner._on_session = every_other

    first, second = transport.open(), transport.open()
    for s, word in ((first, "a"), (second, "b")):
        await s.feed(word)
        s.end_input()
    transport.stop()
    await runner.run()

    assert first.sent == []          # refused
    assert second.sent == ["b"]      # served


@pytest.mark.asyncio
async def test_a_transport_that_dies_mid_accept_drains_what_it_started():
    """Sessions already running must finish even as the transport fails."""

    class DyingTransport:
        def __init__(self):
            self.sessions_made = []

        async def sessions(self):
            s = MemorySession(max_inflight=8)
            await s.feed("only")
            s.end_input()
            self.sessions_made.append(s)
            yield s
            raise RuntimeError("the listener fell over")

        async def close(self):
            pass

    transport = DyingTransport()
    runner = ServeRunner(Operon(echo_graph), spec(kind="x:y"), transport=transport)

    with pytest.raises(RuntimeError):
        await runner.run()

    # The `finally` in run() gathers in-flight sessions before letting the
    # failure out, so the call that was already accepted still completed.
    assert transport.sessions_made[0].sent == ["only"]


# -- backpressure --------------------------------------------------------

@pytest.mark.asyncio
async def test_the_bound_actually_blocks_the_producer():
    """`max_inflight` has to push back, not just count.

    This is the one property the old `operonx.io.Channel` had that the
    plain-edge rewrite gave up: a bound the producer feels. A telco pushes
    50 packets a second per call and does not slow down for anyone, so if
    `feed` returned instead of waiting, the queue would grow until the box
    did.
    """
    session = MemorySession(max_inflight=3)
    for i in range(3):
        await asyncio.wait_for(session.feed(i), timeout=0.5)

    # The fourth has nowhere to go until something is consumed.
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(session.feed(4), timeout=0.2)


@pytest.mark.asyncio
async def test_the_bound_releases_as_the_consumer_drains():
    """Backpressure, not a deadlock — the producer moves again."""
    session = MemorySession(max_inflight=2)
    await session.feed("a")
    await session.feed("b")

    consumed = []

    async def consume():
        async for item in session.recv():
            consumed.append(item)

    reader = asyncio.create_task(consume())
    await asyncio.wait_for(session.feed("c"), timeout=1.0)   # room now
    session.end_input()
    await asyncio.wait_for(reader, timeout=1.0)
    assert consumed == ["a", "b", "c"]


@pytest.mark.asyncio
async def test_an_unbounded_session_does_not_block():
    """No bound declared means no ceiling — which is why a manifest must set one."""
    session = MemorySession(max_inflight=None)
    for i in range(5000):
        await asyncio.wait_for(session.feed(i), timeout=0.5)
    assert session._queue.qsize() == 5000


# -- things that are not there -------------------------------------------

def test_an_unknown_transport_names_what_is_registered():
    with pytest.raises(LookupError, match="Registered:"):
        resolve_transport("carrier-pigeon")


def test_a_transport_import_path_that_does_not_resolve_says_so():
    with pytest.raises(ImportError, match="cannot import"):
        resolve_transport("no.such.module:Thing")
    with pytest.raises(ImportError, match="has no"):
        resolve_transport("operonx.core.serve.memory:NotAClass")


@pytest.mark.asyncio
async def test_a_session_that_yields_nothing_still_completes_its_run():
    """A peer that connects and says nothing must not hang the server."""
    transport = MemoryTransport()
    runner = ServeRunner(Operon(echo_graph), spec(), transport=transport)
    session = transport.open()
    session.end_input()                      # not one item
    transport.stop()
    await asyncio.wait_for(runner.run(), timeout=10)
    assert session.closed and session.sent == []


def test_bounded_session_requires_a_send_implementation():
    """The protocol's one abstract half, so a half-written transport says so."""

    async def check():
        with pytest.raises(NotImplementedError):
            await BoundedSession().send("x")

    asyncio.run(check())


# -- sessions whose own methods misbehave --------------------------------

class RecvExplodes(MemorySession):
    async def recv(self):
        yield "first"
        raise RuntimeError("socket reset by peer")


class SendExplodes(MemorySession):
    async def _send(self, item):
        raise RuntimeError("write to a closed socket")


class CloseExplodes(MemorySession):
    async def close(self):
        raise RuntimeError("close blew up")


@pytest.mark.asyncio
async def test_a_session_whose_recv_raises_does_not_hang_the_run():
    from operonx.core.serve import serve_session

    session = RecvExplodes(max_inflight=8)
    await asyncio.wait_for(serve_session(Operon(echo_graph), session), timeout=8)


@pytest.mark.asyncio
async def test_a_session_whose_send_raises_loses_the_item_not_the_run():
    """`Session.send` is specified to report failure, not raise.

    A transport that breaks that promise should cost its item and nothing
    else — the run may still have a record to write.
    """
    from operonx.core.serve import serve_session

    session = SendExplodes(max_inflight=8)
    await session.feed("x")
    session.end_input()
    await asyncio.wait_for(serve_session(Operon(echo_graph), session), timeout=8)


@pytest.mark.asyncio
async def test_a_close_that_raises_does_not_fail_a_completed_run():
    """Teardown failing must not rewrite the outcome of the work."""
    from operonx.core.serve import serve_session

    session = CloseExplodes(max_inflight=8)
    await session.feed("x")
    session.end_input()
    handle = await asyncio.wait_for(serve_session(Operon(echo_graph), session), timeout=8)
    assert handle is not None
    assert session.sent == ["x"]


@pytest.mark.asyncio
async def test_a_drained_session_never_blocks_a_second_reader():
    """`end_input` queues one EOF; whoever takes it must not strand the rest.

    A transport that offered the same session twice used to park a task on
    an empty queue forever, with no error and no log — the worst way for
    anything to fail.
    """
    from operonx.core.serve import serve_session

    session = MemorySession(max_inflight=8)
    await session.feed("x")
    session.end_input()
    await serve_session(Operon(echo_graph), session)
    await asyncio.wait_for(serve_session(Operon(echo_graph), session), timeout=8)


# -- hooks that return the wrong thing -----------------------------------

@pytest.mark.parametrize("bad", [{"not": "a request"}, "a string", 42, 0.5])
@pytest.mark.asyncio
async def test_on_session_returning_a_non_runrequest_is_named_and_refused(bad):
    """Otherwise it fails frames later as AttributeError on `.inputs`."""
    transport = MemoryTransport()
    runner = ServeRunner(Operon(echo_graph), spec(), transport=transport)
    runner._on_session = lambda session: bad

    session = transport.open()
    await session.feed("x")
    session.end_input()
    transport.stop()
    await asyncio.wait_for(runner.run(), timeout=8)
    assert session.sent == []          # refused, not half-run


# -- manifests that should be refused at parse ---------------------------

@pytest.mark.parametrize("block, match", [
    ({"kind": "http", "path": "/x", "graph": "m:g", "port": "nine"}, "not a number"),
    ({"kind": "http", "path": "/x", "graph": "m:g", "port": 99999}, "1-65535"),
    ({"kind": "http", "path": "/x", "graph": "m:g", "port": 0}, "1-65535"),
    ({"kind": "http", "path": "/x", "graph": "m:g", "session": "per_banana"}, "expected one of"),
    ({"kind": "websocket", "path": "/x", "graph": "m:g", "max_inflight": -5}, "positive integer"),
    ({"kind": "http", "path": "/x", "graph": "not-an-entry"}, "not a `module:function`"),
    ({"kind": "http", "path": "/x"}, "has no `graph`"),
    ({"path": "/x", "graph": "m:g"}, "has no `kind`"),
])
def test_bad_serve_blocks_are_refused_at_parse(block, match):
    """At parse, not at bind: an OSError from inside the event loop after
    everything else has started is a much worse way to learn this."""
    from operonx.core.manifest import Manifest, ManifestError

    with pytest.raises(ManifestError, match=match):
        Manifest.from_dict({"serve": [block]})


# -- import paths that name themselves -----------------------------------

def test_every_unresolvable_reference_names_the_manifest_entry():
    """A manifest can hold half a dozen import paths.

    `ModuleNotFoundError: No module named 'no'` says which module is
    missing and nothing about which line asked for it.
    """
    from operonx.core.serve.app import build_app, engine_for

    with pytest.raises(ImportError, match=r"\[\[serve\]\] 't' graph"):
        engine_for(spec(name="t", kind="http", graph="no.such.module:g"))

    with pytest.raises(ImportError, match=r"\[\[serve\]\] 't' graph"):
        engine_for(spec(name="t", kind="http", graph="operonx.core.serve:NotThere"))

    with pytest.raises(TypeError, match="is not a @graph"):
        engine_for(spec(name="t", kind="http",
                        graph="operonx.core.manifest:MANIFEST_FILENAME"))

    with pytest.raises(ImportError, match=r"\[\[serve\]\] 'a' app"):
        build_app((ServeSpec(name="a", kind="asgi", path="/", app="no.mod:app"),))
