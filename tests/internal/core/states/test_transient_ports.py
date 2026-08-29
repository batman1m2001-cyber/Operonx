"""`@op(transient=True)` — per-item state must not survive its context.

A run never freed per-item cells: `_cells[idx]` is keyed by context, every
dispatched item mints one, and nothing removed an entry. Request-response
graphs never noticed; a streaming run grew linearly with items forever.
"""

import asyncio

import pytest

from operonx.core import END, PARENT, START, Operon, graph
from operonx.core.ops import op


@op(transient=True)
async def stream(n: int = 0):
    for i in range(n):
        yield {"blob": bytes(4096), "i": i}


@op(bound="sync")
def consume(blob: bytes = b"", i: int = 0) -> dict:
    return {"size": len(blob)}


@graph
def _flat(n=0):
    s = stream(n=n)
    c = consume(blob=s["blob"], i=s["i"])
    START >> s >> c >> END


def _cell_entries(state) -> int:
    return sum(len(cell.contexts) for cell in state._cells)


@pytest.mark.asyncio
async def test_transient_run_is_flat_in_items():
    """Cell count must not grow with the number of items streamed."""
    engine = Operon(_flat, params={"n": None})
    counts = []
    for n in (50, 500, 5000):
        handle = engine.start(inputs={"n": n})
        await handle.collect()
        counts.append(_cell_entries(handle.state))

    assert counts[0] == counts[1] == counts[2], (
        f"per-item state is being retained: {counts} entries for 50/500/5000 "
        "items. Transient cells must be released when their context finishes."
    )


@pytest.mark.asyncio
async def test_without_transient_it_grows():
    """The guard on the test above: prove the measurement can detect a leak."""

    @op
    async def retaining(n: int = 0):
        for i in range(n):
            yield {"blob": bytes(4096), "i": i}

    @graph
    def _grows(n=0):
        s = retaining(n=n)
        c = consume(blob=s["blob"], i=s["i"])
        START >> s >> c >> END

    engine = Operon(_grows, params={"n": None})
    small = engine.start(inputs={"n": 50})
    await small.collect()
    large = engine.start(inputs={"n": 500})
    await large.collect()

    assert _cell_entries(large.state) > _cell_entries(small.state)


def test_collect_on_a_transient_source_is_a_compile_error():
    """`.collect()` buffers until EOF — exactly what transient releases."""

    @graph
    def _bad(n=0):
        s = stream(n=n)
        c = consume(blob=s["blob"].collect(), i=s["i"])
        START >> s >> c >> END

    with pytest.raises(ValueError, match="collect"):
        Operon(_bad, params={"n": None})


def test_two_consumers_of_a_transient_port_is_a_compile_error():
    """Eviction fires on context completion; that is only safe for 1-1."""

    @op(bound="sync")
    def other(blob: bytes = b"") -> dict:
        return {"n": len(blob)}

    @graph
    def _fanout(n=0):
        s = stream(n=n)
        a = consume(blob=s["blob"], i=s["i"])
        b = other(blob=s["blob"])
        START >> s >> [a, b]
        [a, b] >> END

    with pytest.raises(ValueError, match="consumer"):
        Operon(_fanout, params={"n": None})


@pytest.mark.asyncio
async def test_transient_may_push_into_a_declared_cell():
    """Pushing a transient value into a shared cell is safe, and allowed.

    The push writes into the shared cell, which is never evicted, so the
    value outlives the context that produced it — that is the point. The
    guard exists for the other direction: a shared cell being *marked*
    transient by pull-propagation, which would silently drop graph-level
    state. Asserted here so the two cases do not get conflated later.
    """

    @op(transient=True)
    async def emits(n: int = 0):
        for i in range(n):
            yield {"total": i}

    @graph
    def _shared(n=0):
        PARENT.declare(total=0)
        e = emits(n=n)
        e["total"] >> PARENT["total"]
        START >> e >> END

    engine = Operon(_shared, params={"n": None})
    handle = engine.start(inputs={"n": 20})
    result = await handle.collect()
    # Every yield pushed, and the shared cell kept them all — none was lost
    # to the eviction that released the transient side.
    assert result["total"] == list(range(20))


# -- regression: the chain length that the original tests never reached --

@pytest.mark.asyncio
async def test_transient_survives_a_three_op_chain():
    """A parked sequential consumer must not have its context freed.

    The tests above use a two-op chain — producer straight into consumer —
    and pass while a three-op chain silently loses data. Streaming is
    sequential by default, so with a third op the second and later items
    wait in `seq_queues`: not a live task, not an inline pending, and so
    invisible to the release guard. The context was freed underneath them
    and every item after the first arrived as None.
    """
    seen = []

    @op(bound="io", transient=True)
    async def produce(n: int = 0):
        for i in range(n):
            yield {"item": f"i{i}"}

    @op(bound="sync")
    def relay(item: str = "") -> dict:
        return {"reply": f"{item}!"}

    @op(bound="io")
    async def collect(reply=None) -> dict:
        seen.append(reply)
        return {}

    @graph
    def chain(n=0):
        a = produce(n=n)
        b = relay(item=a["item"])
        c = collect(reply=b["reply"])
        START >> a >> b >> c >> END

    engine = Operon(chain, params={"n": None})
    for count in (3, 50):
        seen.clear()
        handle = engine.start(inputs={"n": count})
        async for _ in handle:
            pass
        assert seen == [f"i{i}!" for i in range(count)], (
            f"chain of {count} lost items: {seen[:5]}"
        )


@pytest.mark.parametrize("mid_bound", ["sync", "io"])
@pytest.mark.asyncio
async def test_transient_survives_whatever_the_consumer_is_bound_to(mid_bound):
    """The middle op's `bound` decides which dispatch path the frame takes.

    `sync` is drained inline, so by the time the release guard runs the
    consumer is already dispatched or parked in a sequential queue — both
    of which the guard learned to check. `io` is not: its frame sits on the
    scheduler queue, unhandled, so nothing anywhere had dispatched the
    consumer and the context looked idle. Every item was lost, not merely
    the ones after the first.

    Parametrised because a suite that only ever wrote `sync` middles is
    exactly how this survived being found twice.
    """
    seen = []

    @op(bound="io", transient=True)
    async def produce(n: int = 0):
        for i in range(n):
            yield {"item": f"i{i}"}

    if mid_bound == "sync":
        @op(bound="sync")
        def relay(item=None) -> dict:
            return {"out": item}
    else:
        @op(bound="io")
        async def relay(item=None) -> dict:
            await asyncio.sleep(0)
            return {"out": item}

    @op(bound="io")
    async def collect(out=None) -> dict:
        seen.append(out)
        return {}

    @graph
    def chain(n=0):
        a = produce(n=n)
        b = relay(item=a["item"])
        c = collect(out=b["out"])
        START >> a >> b >> c >> END

    engine = Operon(chain, params={"n": None})
    for count in (1, 3, 40):
        seen.clear()
        handle = engine.start(inputs={"n": count})
        async for _ in handle:
            pass
        assert seen == [f"i{i}" for i in range(count)], (
            f"{mid_bound} chain of {count} lost items: {seen[:5]}"
        )


@pytest.mark.asyncio
async def test_the_release_guard_did_not_become_a_no_op():
    """Widening the guard must not stop it releasing anything.

    Every correctness test above would still pass if contexts were simply
    never freed — and the leak transient ports exist to fix would be back.
    So this asserts the other direction: retention stays flat as the item
    count grows by two orders of magnitude.
    """
    @op(bound="io", transient=True)
    async def produce(n: int = 0):
        for _ in range(n):
            yield {"blob": bytes(4096)}

    @op(bound="io")
    async def relay(blob=None) -> dict:
        return {"out": blob}

    @op(bound="io")
    async def collect(out=None) -> dict:
        return {"size": len(out or b"")}

    @graph
    def chain(n=0):
        a = produce(n=n)
        b = relay(blob=a["blob"])
        c = collect(out=b["out"])
        START >> a >> b >> c >> END

    engine = Operon(chain, params={"n": None})

    def entries(state):
        total = 0
        for cells in state._cells:
            for cell in (cells if isinstance(cells, list) else [cells]):
                total += len(getattr(cell, "contexts", ()) or ())
        return total

    counts = []
    for n in (50, 2000):
        handle = engine.start(inputs={"n": n})
        async for _ in handle:
            pass
        counts.append(entries(handle.state))

    assert counts[0] == counts[1], (
        f"retention grew with item count: {counts} — the guard is holding "
        f"contexts it should have freed"
    )
