"""A declared cell's default must not leak from one run into the next.

`PARENT.declare(bag=set())` evaluates `set()` once, when the graph is
built. Every run gets its own MemoryState and its own Cell — but both used
to be handed *that* object, so an op mutating it in place wrote into the
value every future run started from.

The symptom is nasty because it appears on the SECOND run, and most tests
only make one. This file makes two, deliberately.
"""

import pytest

from operonx.core import END, PARENT, START, Operon, graph
from operonx.core.ops import op


@op
def add_to(n: int = 0, bag=None) -> dict:
    bag.add(n)
    return {"seen": sorted(bag)}


@op
def append_to(n: int = 0, bag=None) -> dict:
    bag.append(n)
    return {"seen": list(bag)}


@op
def put_in(n: int = 0, bag=None) -> dict:
    bag[n] = n
    return {"seen": sorted(bag)}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kind, initial, opfn",
    [("set", set(), add_to), ("list", [], append_to), ("dict", {}, put_in)],
)
async def test_mutable_default_is_per_run(kind, initial, opfn):
    @graph
    def g(n=0):
        PARENT.declare(bag=initial)
        t = opfn(n=n, bag=PARENT["bag"])
        START >> t >> END

    ENGINE = Operon(g, params={"n": None})

    first = ENGINE.start(inputs={"n": 1})
    await first.collect()
    assert first.state[ENGINE.name, "bag"] and len(first.state[ENGINE.name, "bag"]) == 1

    second = ENGINE.start(inputs={"n": 2})
    await second.collect()
    got = second.state[ENGINE.name, "bag"]
    assert len(got) == 1, (
        f"{kind} default leaked between runs: run 2 started with run 1's data ({got!r})"
    )
    assert 1 not in got


@pytest.mark.asyncio
async def test_non_container_default_keeps_its_identity():
    """Only list/dict/set are copied. A handle stays the same object.

    Deep-copying everything would break callers who put a resource handle
    in a default — ONNX sessions and Triton clients reject deepcopy — so
    anything that is not one of the three containers is left aliased.
    """

    class Handle:  # stands in for a session that cannot be copied
        pass

    handle = Handle()

    @op
    def read(n: int = 0, h=None) -> dict:
        return {"same": h is handle}

    @graph
    def g(n=0):
        PARENT.declare(h=handle)
        t = read(n=n, h=PARENT["h"])
        START >> t >> END

    ENGINE = Operon(g, params={"n": None})
    for _ in range(2):
        h = ENGINE.start(inputs={"n": 1})
        await h.collect()
        assert h.state[ENGINE.name, "h"] is handle
