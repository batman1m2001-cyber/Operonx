"""``.collect()`` waits for the whole stream wherever it sits.

The collect buffer was flushed on its *source's* EOF. Straight from the
generator that is the end of the stream. Behind a per-item op it is the
end of one item: every item flushed its own one-item list and the
consumer ran once per item — the exact opposite of what ``.collect()``
is for.

Buffers were also keyed by the edge alone, so two runs of the same
generator in flight at once (a nested stream fanned out with
``.parallel()``) shared one buffer: the first to finish took every item
buffered so far, from both, and the second flushed what was left.
"""

from __future__ import annotations

import asyncio

from operonx import END, START, Operon, graph, op

JOINS: list = []


@op
def words(text: str):
    for w in text.split():
        yield {"word": w}


@op
async def shout(word: str) -> dict:
    await asyncio.sleep(0.005)
    return {"loud": word.upper()}


@op
def shout_sync(word: str) -> dict:
    return {"loud": word.upper()}


@op
def join(words: list) -> dict:
    JOINS.append(list(words))
    return {"line": " ".join(words)}


@graph
def straight_from_the_generator(text):
    w = words(text=text)
    j = join(words=w["word"].collect())
    START >> w >> j >> END


@graph
def behind_a_per_item_op(text):
    w = words(text=text)
    s = shout(word=w["word"])
    j = join(words=s["loud"].collect())
    START >> w >> s >> j >> END


@graph
def behind_a_sync_per_item_op(text):
    w = words(text=text)
    s = shout_sync(word=w["word"])
    j = join(words=s["loud"].collect())
    START >> w >> s >> j >> END


@graph
def behind_a_parallel_per_item_op(text):
    w = words(text=text)
    s = shout(word=w["word"].parallel())
    j = join(words=s["loud"].collect())
    START >> w >> s >> j >> END


async def _run(g, text="a b c"):
    JOINS.clear()
    return await Operon(g, params={"text": None}).run(inputs={"text": text})


async def test_straight_from_the_generator_is_unchanged():
    out = await _run(straight_from_the_generator)
    assert JOINS == [["a", "b", "c"]]
    assert out["line"] == "a b c"


async def test_behind_a_per_item_op_runs_once_with_the_full_list():
    out = await _run(behind_a_per_item_op)
    assert JOINS == [["A", "B", "C"]]
    assert out["line"] == "A B C"


async def test_behind_a_sync_per_item_op_runs_once_with_the_full_list():
    out = await _run(behind_a_sync_per_item_op)
    assert JOINS == [["A", "B", "C"]]
    assert out["line"] == "A B C"


async def test_behind_a_parallel_per_item_op_keeps_yield_order():
    out = await _run(behind_a_parallel_per_item_op, text="a b c d e f")
    assert JOINS == [["A", "B", "C", "D", "E", "F"]]
    assert out["line"] == "A B C D E F"


# ── two per-item hops, and an item that fails on the way ────────────────


@op
async def exclaim(loud: str) -> dict:
    await asyncio.sleep(0.001)
    if loud == "BAD":
        raise ValueError("no")
    return {"loud": loud + "!"}


@graph
def two_hops(text):
    w = words(text=text)
    s = shout(word=w["word"])
    e = exclaim(loud=s["loud"])
    j = join(words=e["loud"].collect())
    START >> w >> s >> e >> j >> END


async def test_two_per_item_hops_still_collect_once():
    await _run(two_hops)
    assert JOINS == [["A!", "B!", "C!"]]


async def test_an_item_that_fails_before_the_collect_is_left_out():
    await _run(two_hops, text="a bad c")
    assert JOINS == [["A!", "C!"]]


async def test_a_stream_whose_every_item_failed_collects_an_empty_list():
    """Left out one by one, every item: the collect still fires, once.

    The group was created by the first item to *reach* the collect, so
    with none reaching it there was nothing to flush and the consumer
    never ran — nor did anything after it, with no error of its own.
    """
    out = await _run(two_hops, text="bad bad")
    assert JOINS == [[]]
    assert out["line"] == ""


async def test_a_generator_that_yields_nothing_has_no_stream_to_collect():
    """No item, no stream: unchanged, and what the guide says."""
    await _run(two_hops, text="")
    assert JOINS == []


async def test_an_empty_list_per_inner_stream_whose_items_all_failed():
    JOINS.clear()
    await Operon(nested_failing, params={"n": None}).run(inputs={"n": 3})
    assert sorted(JOINS) == [[], ["G0A!", "G0B!"], ["G2A!", "G2B!"]]


# ── a nested stream: one list per run of the inner generator ────────────


@op
def groups(n: int):
    for g in range(n):
        yield {"text": f"g{g}a g{g}b g{g}c"}


@op
async def split(text: str):
    for w in text.split():
        await asyncio.sleep(0.002)
        yield {"word": w}


@graph
def nested_direct(n):
    gr = groups(n=n)
    sp = split(text=gr["text"].parallel())
    j = join(words=sp["word"].collect())
    START >> gr >> sp >> j >> END


@graph
def nested_behind_per_item(n):
    gr = groups(n=n)
    sp = split(text=gr["text"].parallel())
    s = shout(word=sp["word"])
    j = join(words=s["loud"].collect())
    START >> gr >> sp >> s >> j >> END


@op
def bad_middle(n: int):
    for g in range(n):
        yield {"text": "bad bad" if g == 1 else f"g{g}a g{g}b"}


@graph
def nested_failing(n):
    gr = bad_middle(n=n)
    sp = split(text=gr["text"].parallel())
    s = shout(word=sp["word"])
    e = exclaim(loud=s["loud"])
    j = join(words=e["loud"].collect())
    START >> gr >> sp >> s >> e >> j >> END


async def test_nested_streams_collect_per_inner_stream():
    JOINS.clear()
    await Operon(nested_direct, params={"n": None}).run(inputs={"n": 2})
    assert sorted(JOINS) == [["g0a", "g0b", "g0c"], ["g1a", "g1b", "g1c"]]


async def test_nested_streams_behind_a_per_item_op_collect_per_inner_stream():
    JOINS.clear()
    await Operon(nested_behind_per_item, params={"n": None}).run(inputs={"n": 2})
    assert sorted(JOINS) == [["G0A", "G0B", "G0C"], ["G1A", "G1B", "G1C"]]


# ── the collect does not wait for unrelated work at the same context ────

EVENTS: list = []


@op
async def unrelated() -> dict:
    await asyncio.sleep(0.2)
    EVENTS.append("unrelated done")
    return {"u": 1}


@op
def join_and_note(words: list) -> dict:
    EVENTS.append("join")
    return {"line": " ".join(words)}


@graph
def collect_beside_a_slow_op(text):
    u = unrelated()
    w = words(text=text)
    s = shout(word=w["word"])
    j = join_and_note(words=s["loud"].collect())
    START >> [u, w]
    w >> s >> j >> END
    u >> END


async def test_collect_fires_when_its_stream_ends_not_when_the_run_ends():
    EVENTS.clear()
    await Operon(collect_beside_a_slow_op, params={"text": None}).run(inputs={"text": "a b"})
    assert EVENTS == ["join", "unrelated done"]
