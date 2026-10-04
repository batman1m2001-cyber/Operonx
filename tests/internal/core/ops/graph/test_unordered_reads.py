"""A read of another op's output needs an edge that makes the producer run first.

``s = slow(y=f["y"])`` reads ``f`` but orders nothing. With no ``f >> s``
both may start together: ``s`` got ``f``'s value when ``f`` happened to be
fast, and a missing argument (or its default) when it was not — the same
graph passing or failing on timing alone. A reviewer hit it with a retried
``f``: three attempts made ``f`` slow enough to lose the race.
"""

import asyncio
import operator

import pytest

from operonx import END, PARENT, SCRATCH, START, Operon, graph, op
from operonx.core.ops import if_
from operonx.core.ops.graph.validation import GraphValidationError


@op
async def make(x: int) -> dict:
    await asyncio.sleep(0.03)
    return {"y": x + 1}


@op
async def use(y: int) -> dict:
    return {"z": y}


def test_build_rejects_a_read_nothing_orders():
    @graph
    def g(x):
        f = make(x=x)
        s = use(y=f["y"])
        return s  # no edges: f and s both start as entries

    with pytest.raises(GraphValidationError) as caught:
        Operon(g, params={"x": None})
    text = str(caught.value)
    assert "op 's' reads 'f'['y']" in text
    assert "START >> f >> s" in text
    assert "PARENT.declare(y=None)" in text  # the way to read whatever is there


def test_a_read_beside_the_producer_is_rejected():
    @graph
    def g(x):
        f = make(x=x)
        s = use(y=f["y"])
        START >> [f, s]
        [f, s] >> END

    with pytest.raises(GraphValidationError, match="nothing makes 'f' run first"):
        Operon(g, params={"x": None})


def test_the_push_form_is_checked_too():
    @graph
    def g(x):
        f = make(x=x)
        s = use()
        f["y"] >> s["y"]
        START >> [f, s]
        [f, s] >> END

    with pytest.raises(GraphValidationError, match="op 's' is fed 'f'\\['y'\\]"):
        Operon(g, params={"x": None})


async def test_ordered_reads_build_and_run():
    @graph
    def g(x):
        f = make(x=x)
        s = use(y=f["y"])
        START >> f >> s >> END

    assert (await Operon(g, params={"x": None}).run({"x": 1}))["z"] == 2


@op
async def cached() -> dict:
    return {"answer": "cached"}


@op
async def computed() -> dict:
    await asyncio.sleep(0.05)
    return {"answer": "computed"}


@op
def reply(a: str = None, b: str = None) -> dict:
    return {"text": a or b}


@op
def check(n: int) -> dict:
    return {"big": n > 10}


@op
def label(n: int) -> dict:
    return {"label": f"n={n}"}


@op
async def flaky(x: int) -> dict:
    raise ConnectionError("down")


@op
def note(error: str, last: str = None) -> dict:
    return {"noted": f"{error} / {last}"}


@op
def step(n: int) -> dict:
    return {"n": n + 1, "done": n + 1 >= 3}


@op
def after_loop(n: int) -> dict:
    return {"final": n}


@op
def from_parent_and_scratch(x: int, tag: str = None) -> dict:
    return {"out": f"{x}:{tag}"}


async def test_every_ordered_shape_builds():
    """Soft edges, branch arms, error edges, loops, PARENT and SCRATCH reads."""

    @graph
    def race():
        c, s = cached(), computed()
        r = reply(a=c["answer"], b=s["answer"])
        START >> [c, s]
        c >> ~r
        s >> ~r
        r >> END

    @graph
    def branchy(n):
        c = check(n=n)
        b, s = label(n=n), label(n=n)
        r = reply(a=b["label"], b=s["label"])
        START >> c >> if_(c["big"] == True, b).else_(s)  # noqa: E712
        [b, s] >> r >> END

    @graph
    def handled(x):
        f = flaky(x=x)
        n = note(last=f.get("error"))
        START >> f >> END
        f.on_error(n)
        n >> END

    @graph
    def looped():
        PARENT.declare(n=0, total=[], reducers={"total": operator.add})
        s = step(n=PARENT["n"])
        s["n"] >> PARENT["n"]
        a = after_loop(n=PARENT["n"])
        START >> s >> if_(s["done"] == True, a).else_(s)  # noqa: E712
        a >> END

    @graph
    def inputs(x):
        o = from_parent_and_scratch(x=x, tag=SCRATCH["tag"])
        START >> o >> END

    assert (await Operon(race).run({}))["text"] == "cached"
    assert (await Operon(branchy, params={"n": None}).run({"n": 30}))["text"] == "n=30"
    assert "ConnectionError" in (await Operon(handled, params={"x": None}).run({"x": 1}))["noted"]
    assert (await Operon(looped).run({}))["final"] == 3
    out = await Operon(inputs, params={"x": None}).run({"x": 4}, scratch={"tag": "t"})
    assert out["out"] == "4:t"


def test_an_op_with_no_edges_is_said_to_run():
    """The build said such an op 'will never be executed'; it runs, as an entry."""

    @op
    def lonely() -> dict:
        return {"v": 1}

    @op
    def other() -> dict:
        return {"w": 2}

    @graph
    def g():
        lonely()
        other()

    built = g()
    built.build()
    messages = [i.message for i in built.validate().issues]
    assert any("runs as an entry of the graph" in m for m in messages), messages
    assert not any("never be executed" in m for m in messages)
