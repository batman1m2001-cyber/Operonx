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

from operonx import END, START, Operon, graph, op


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
