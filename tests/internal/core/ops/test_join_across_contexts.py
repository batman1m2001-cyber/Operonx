"""An op joining a stream's ops with ops outside the stream.

An op downstream of a generator runs in the stream's contexts (one per item,
or the collect context after a ``.collect()``). When it also waits for an op
that is not downstream of the generator — one that runs in the parent context
— that op's arrival in the parent context counts in the stream's contexts, and
the joining op runs once both have landed, whichever lands first.

Before, a collect context counted none of them, so an op joining a collected
stream with a sibling never ran and the run reported nothing; and an item
context counted them as landed before they had, so a per-item op could run
before a slow sibling finished, without its value.
"""

import asyncio

from operonx import END, START, Operon, graph, op, run_context


@op
def items(n: int):
    for i in range(n):
        yield {"i": i}


@op
def double(i: int) -> dict:
    return {"d": i * 2}


@op
def total(ds: list) -> dict:
    return {"total": sum(ds)}


@op
async def side(n: int, delay: float = 0.0) -> dict:
    await asyncio.sleep(delay)
    return {"side": n}


@op
def join(total: int, side: int) -> dict:
    return {"joined": total + side}


@op
def scale(i: int, side: int) -> dict:
    return {"scaled": i * side}


@graph
def collect_then_join(n, delay):
    it = items(n=n)
    db = double(i=it["i"])
    t = total(ds=db["d"].collect())
    s = side(n=n, delay=delay)
    j = join(total=t["total"], side=s["side"])
    START >> it >> db >> t >> j
    START >> s >> j
    j >> END


@graph
def summed(n):
    it = items(n=n)
    db = double(i=it["i"])
    t = total(ds=db["d"].collect())
    START >> it >> db >> t >> END


@graph
def subgraph_then_join(n, delay):
    t = summed(n=n)
    s = side(n=n, delay=delay)
    j = join(total=t["total"], side=s["side"])
    START >> t >> j
    START >> s >> j
    j >> END


@graph
def per_item_join(n, delay):
    it = items(n=n)
    s = side(n=n, delay=delay)
    sc = scale(i=it["i"], side=s["side"])
    START >> it >> sc
    START >> s >> sc
    sc >> END


async def _run(g, delay):
    return await Operon(g, params={"n": None, "delay": None}).run(inputs={"n": 3, "delay": delay})


async def test_an_op_joining_a_collected_stream_and_a_sibling_runs_once():
    for delay in (0.0, 0.05):  # the sibling lands before, then after, the collect
        out = await _run(collect_then_join, delay)
        assert "$errors" not in out
        assert out["joined"] == 9  # 0 + 2 + 4, plus 3


async def test_a_subgraph_ending_in_a_collect_joins_a_sibling():
    for delay in (0.0, 0.05):
        out = await _run(subgraph_then_join, delay)
        assert "$errors" not in out
        assert out["joined"] == 9


async def test_a_per_item_op_waits_for_a_slow_sibling():
    out = await _run(per_item_join, 0.05)
    assert "$errors" not in out
    assert sorted(out["scaled"]) == [0, 3, 6]


# ── two streams merging into one op: a call's speech and its prompts ──────
#
# Each side is a stream of its own; the merge fires per item of either, the
# other side absent. The other stream never lands in this stream's parent
# context — it arrives in contexts of its own — so waiting for it there (as a
# join with an op outside the stream does) waited for that stream's END: the
# callbot's greeting and every turn after it never ran (1.15–1.20.0).

MERGED: list = []


@op
async def packets(n: int = 0):
    for i in range(30):
        await asyncio.sleep(0.005)
        yield {"chunk": i}


@op
async def detect(chunk: int = None):
    if chunk is not None and chunk % 10 == 9:  # a segment closes on 3 of 30 packets
        yield {"frame": f"speech{chunk}"}


@op
async def prompt_frames(n: int = 0):
    yield {"frame": "greeting"}
    await asyncio.sleep(0.3)


@op(bound="sync")
def merge_frames(audio: str = None, prompt: str = None) -> dict:
    MERGED.append(audio or prompt)
    return {"kind": audio or prompt}


@graph
def call_frames():
    p = packets()
    v = detect(chunk=p["chunk"])
    b = prompt_frames()
    m = merge_frames(audio=v["frame"], prompt=b["frame"])
    START >> p >> v >> m
    START >> b >> m
    m >> END


def test_two_streams_merge_into_one_op_item_by_item():
    MERGED.clear()
    asyncio.run(asyncio.wait_for(Operon(call_frames()).run(inputs={}), 10))
    assert MERGED[0] == "greeting"  # the prompt stream's item, without waiting for speech
    assert [m for m in MERGED if m.startswith("speech")] == ["speech9", "speech19", "speech29"]


# ── two streams under one parent number their items apart ─────────────────
#
# Both generators run in the same context and both used to number their items
# "[0]", "[1]", ... under it: the second stream's item landed in a context the
# first had already opened and spent, and the merge never saw it. Each now
# numbers its items with its name ("[rare:0]"). Two streams inside each item of
# a third were also waited for as a join, and the merge never fired for one side.

FIRED: list = []
CTXS: list = []


@op
async def often(k: int = 0):
    for i in range(3):
        await asyncio.sleep(0.01)
        yield {"x": f"o{k}.{i}"}


@op
async def rare(k: int = 0):
    await asyncio.sleep(0.1)
    yield {"y": f"r{k}"}


@op
async def outer(n: int = 0):
    for i in range(2):
        yield {"k": i}


@op(bound="sync")
def merge_two(a: str = None, b: str = None) -> dict:
    FIRED.append((a, b))
    CTXS.append(run_context().ctx)
    return {}


@graph
def two_streams(n):
    o = often(k=n)
    r = rare(k=n)
    m = merge_two(a=r["y"], b=o["x"])
    START >> o >> m
    START >> r >> m
    m >> END


@graph
def two_streams_per_item(n):
    s = outer(n=n)
    o = often(k=s["k"])
    r = rare(k=s["k"])
    m = merge_two(a=r["y"], b=o["x"])
    START >> s
    s >> o >> m
    s >> r >> m
    m >> END


@graph
def one_stream(n):
    o = often(k=n)
    m = merge_two(b=o["x"])
    START >> o >> m >> END


def _fired(g):
    FIRED.clear()
    asyncio.run(asyncio.wait_for(Operon(g(n=None)).run(inputs={"n": 0}), 10))
    return sorted(a or b for a, b in FIRED)


def test_every_item_of_two_streams_reaches_the_merge():
    assert _fired(two_streams) == ["o0.0", "o0.1", "o0.2", "r0"]


def test_the_merge_reads_the_other_stream_as_absent():
    _fired(two_streams)
    assert all((a is None) != (b is None) for a, b in FIRED)


def test_two_streams_inside_each_item_of_a_third():
    assert _fired(two_streams_per_item) == [
        "o0.0",
        "o0.1",
        "o0.2",
        "o1.0",
        "o1.1",
        "o1.2",
        "r0",
        "r1",
    ]


def test_items_are_numbered_by_their_stream_only_where_streams_meet():
    CTXS.clear()
    _fired(two_streams)
    assert sorted(c[-1] for c in CTXS) == [
        "[o:0]",
        "[o:1]",
        "[o:2]",
        "[r:0]",
    ]  # the ops' names in the graph
    CTXS.clear()
    _fired(one_stream)
    assert sorted(c[-1] for c in CTXS) == ["[0]", "[1]", "[2]"]
