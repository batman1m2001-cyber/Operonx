"""``max_pending=N`` bounds the items waiting on one stream edge.

The per-edge gate (sequential by default, ``.parallel(max=N)``) parks the
items its consumer is not ready for, and nothing bounded that park. A
callbot load test measured it: a generator yielding one 40 ms audio packet
at a time, a consumer that fell behind past eight calls, and 695 items
waiting on the edge at twelve. The serve layer's ``max_inflight`` never
engaged because the generator kept draining the inbound queue into the
unbounded gate, so the socket was read at full speed the whole call.

With ``max_pending=N`` the producer is not advanced while N items wait,
so the backlog stays at the edge's bound and the pressure travels back
to whatever the producer reads from. ``on_full="drop_oldest"`` trades the
wait for dropping (and counting) the stalest waiting item.

The backlog is measured from inside the ops: just before each yield the
producer records ``yielded - finished``, which is the items waiting plus
the one in flight. The unbounded guard below proves the measurement sees
the backlog when there is one.
"""

from __future__ import annotations

import asyncio
import time

import pytest

from operonx import END, PARENT, START, Operon, graph, op
from operonx.core.ops import op as core_op
from operonx.core.ops.graph import GraphOp


class Meter:
    """Counts what the producer yielded and the consumer finished."""

    def __init__(self):
        self.yielded = 0
        self.finished = 0
        self.seen: list = []
        self.backlog: list = []

    def before_yield(self):
        self.backlog.append(self.yielded - self.finished)
        self.yielded += 1

    def done(self, i):
        self.seen.append(i)
        self.finished += 1


M = Meter()


# ── producers: one async generator, one plain (sync) generator ──────────


@op
async def async_items(n: int):
    for i in range(n):
        M.before_yield()
        yield {"i": i}


@op
def sync_items(n: int):
    for i in range(n):
        M.before_yield()
        yield {"i": i}


# ── consumers: slower than the producer ──────────────────────────────────


@op
async def slow_async(i: int) -> dict:
    await asyncio.sleep(0.002)
    M.done(i)
    return {"o": i}


@op
def slow_sync(i: int) -> dict:
    time.sleep(0.002)
    M.done(i)
    return {"o": i}


PRODUCERS = {"async_gen": async_items, "sync_gen": sync_items}
CONSUMERS = {"async": slow_async, "sync": slow_sync}
KINDS = [(p, c) for p in PRODUCERS for c in CONSUMERS]


def _graph(producer: str, consumer: str, **bound):
    """``producer >> consumer``, with ``bound`` on the edge when given."""
    with GraphOp(name=f"{producer}_to_{consumer}") as g:
        it = PRODUCERS[producer](n=PARENT["n"])
        ref = it["i"].sequential(**bound) if bound else it["i"]
        w = CONSUMERS[consumer](i=ref)
        START >> it >> w >> END
    return g


async def _run(g, n=30):
    global M
    M = Meter()
    out = await Operon(g).run(inputs={"n": n})
    return out, M


@pytest.mark.parametrize("producer, consumer", KINDS)
async def test_the_producer_waits_at_the_bound(producer, consumer):
    out, m = await _run(_graph(producer, consumer, max_pending=3))
    # 3 waiting (in flight included: the in-flight one left the queue and
    # its slot went to the next). More means the producer ran ahead.
    assert max(m.backlog) <= 3, m.backlog


@pytest.mark.parametrize("producer, consumer", KINDS)
async def test_without_a_bound_the_producer_runs_ahead(producer, consumer):
    """The guard on the test above: the same graph, unbounded, backs up.

    Proves the measurement sees a backlog when there is one, so the bound
    holding it at 3 is the producer being held and not the timing.
    """
    out, m = await _run(_graph(producer, consumer))
    assert max(m.backlog) >= 20, m.backlog
    assert m.seen == list(range(30))


@pytest.mark.parametrize("producer, consumer", KINDS)
async def test_every_item_is_delivered_in_order(producer, consumer):
    out, m = await _run(_graph(producer, consumer, max_pending=3))
    assert m.seen == list(range(30))
    assert out["o"] == list(range(30))


# ── .parallel(max=N, max_pending=P) ─────────────────────────────────────


class InFlight:
    def __init__(self):
        self.now = 0
        self.peak = 0


FLIGHT = InFlight()


@op
async def slow_parallel(i: int) -> dict:
    FLIGHT.now += 1
    FLIGHT.peak = max(FLIGHT.peak, FLIGHT.now)
    await asyncio.sleep(0.003)
    FLIGHT.now -= 1
    M.done(i)
    return {"o": i}


async def test_parallel_max_with_max_pending():
    global FLIGHT
    FLIGHT = InFlight()
    with GraphOp(name="parallel_bounded") as g:
        it = async_items(n=PARENT["n"])
        w = slow_parallel(i=it["i"].parallel(max=2, max_pending=3))
        START >> it >> w >> END
    out, m = await _run(g)
    assert FLIGHT.peak == 2
    # 3 waiting + up to 2 in flight.
    assert max(m.backlog) <= 3 + 2, m.backlog
    assert sorted(out["o"]) == list(range(30))


# ── a transient producer keeps freeing its items ────────────────────────


@core_op(transient=True)
async def transient_items(n: int = 0):
    for i in range(n):
        M.before_yield()
        yield {"blob": bytes(4096), "i": i}


@op
async def slow_blob(blob: bytes = b"", i: int = 0) -> dict:
    await asyncio.sleep(0.0005)
    M.done(i)
    return {"size": len(blob)}


@graph
def transient_bounded(n=0):
    s = transient_items(n=n)
    c = slow_blob(blob=s["blob"].sequential(max_pending=3), i=s["i"])
    START >> s >> c >> END


async def test_a_transient_producer_keeps_freeing_items():
    global M
    engine = Operon(transient_bounded, params={"n": None})
    counts = []
    for n in (20, 200):
        M = Meter()
        handle = engine.start(inputs={"n": n})
        await handle.collect()
        counts.append(sum(len(cell.contexts) for cell in handle.state._cells))
        assert M.seen == list(range(n))
        assert max(M.backlog) <= 3, M.backlog
    assert counts[0] == counts[1], f"per-item state retained: {counts}"


# ── the wait gives its concurrency slot back ─────────────────────────────


async def test_a_waiting_producer_does_not_hold_the_concurrency_slot():
    """With ``concurrency=1`` a producer holding its slot while it waits
    would starve the async consumer that has to drain the edge."""
    global M
    M = Meter()

    with GraphOp(name="one_slot", concurrency=1) as g:
        it = async_items(n=PARENT["n"])
        w = slow_async(i=it["i"].sequential(max_pending=2))
        START >> it >> w >> END

    out = await asyncio.wait_for(Operon(g).run(inputs={"n": 10}), timeout=10)
    assert out["o"] == list(range(10))
    assert max(M.backlog) <= 2


# ── cancelling a run whose producer is parked on the bound ───────────────


async def test_cancel_during_the_wait_does_not_hang():
    released = asyncio.Event()
    parked = asyncio.Event()

    @op
    async def endless():
        i = 0
        try:
            while True:
                if i == 5:
                    parked.set()
                yield {"i": i}
                i += 1
        finally:
            released.set()

    @op
    async def stuck(i: int) -> dict:
        await asyncio.sleep(10)
        return {"o": i}

    with GraphOp(name="parked_on_bound") as g:
        s = endless()
        w = stuck(i=s["i"].sequential(max_pending=2))
        START >> s >> w >> END

    handle = Operon(g).start(inputs={})
    await asyncio.sleep(0.1)
    # One in flight + 2 waiting: the producer is parked after its 3rd yield.
    assert not parked.is_set(), "the producer ran past the bound"
    handle.cancel()
    await asyncio.wait_for(released.wait(), timeout=5)


# ── on_full="drop_oldest" ───────────────────────────────────────────────


@graph
def dropping(n):
    it = async_items(n=n)
    w = slow_async(i=it["i"].sequential(max_pending=3, on_full="drop_oldest"))
    START >> it >> w >> END


async def test_drop_oldest_never_waits_and_counts_what_it_dropped():
    global M
    M = Meter()
    engine = Operon(dropping, params={"n": None})
    handle = engine.start(inputs={"n": 30})
    out = await handle.collect()
    # The producer never waited: it ran all the way ahead.
    assert max(M.backlog) >= 25, M.backlog
    # What was delivered is in order, ends with the newest, and the rest
    # is accounted for as drops on that edge.
    assert M.seen == sorted(M.seen) and M.seen[-1] == 29
    assert len(M.seen) < 30
    drops = handle.drops
    assert list(drops.values()) == [30 - len(M.seen)], drops
    (edge,) = drops
    assert edge == "engine.it -> engine.w"  # full names: the root graph is `engine`
    assert out["o"] == M.seen


async def test_no_drops_reports_an_empty_dict():
    global M
    M = Meter()
    handle = Operon(_graph("async_gen", "async", max_pending=3)).start(inputs={"n": 5})
    await handle.collect()
    assert handle.drops == {}


# ── wiring errors ───────────────────────────────────────────────────────


def test_max_pending_must_be_positive():
    with pytest.raises(ValueError, match="max_pending"):
        async_items(n=1)["i"].sequential(max_pending=0)


def test_on_full_must_be_known():
    with pytest.raises(ValueError, match="on_full"):
        async_items(n=1)["i"].sequential(max_pending=2, on_full="drop_newest")


def test_unbounded_parallel_has_nothing_to_bound():
    """A bare ``.parallel()`` dispatches every item at once: nothing waits."""
    with pytest.raises(ValueError, match="max_pending"):
        async_items(n=1)["i"].parallel(max_pending=4)


@graph
def _bad(n):
    it = async_items(n=n)
    w = slow_async(i=it["i"].sequential(max_pending=2).collect())
    START >> it >> w >> END


def test_collect_with_max_pending_is_a_compile_error():
    with pytest.raises(ValueError, match="max_pending"):
        Operon(_bad, params={"n": None})
