"""Two unordered writers of one declared cell without a reducer fail the build.

R1 in docs/roadmap/ROADMAP.md, row 2 of track2_langgraph_gap.md. Probe P2:
the same graph read ``slow`` in one run and ``fast`` in the next, silently —
the last write by wall clock won.
"""

import asyncio
import operator

import pytest

from operonx import END, PARENT, START, Operon, graph, op
from operonx.core.ops import if_
from operonx.core.ops.graph.validation import GraphValidationError


@op
async def w_fast(d: float) -> dict:
    await asyncio.sleep(d)
    return {"v": "fast", "acc": ["fast"]}


@op
async def w_slow(d: float) -> dict:
    await asyncio.sleep(d)
    return {"v": "slow", "acc": ["slow"]}


@op
def read(v: str = None, acc: list = None) -> dict:
    return {"final_v": v, "final_acc": acc}


@graph
def g_par(d1, d2, allow_race=False):
    PARENT.declare(v=None, acc=[], reducers={"acc": operator.add}, allow_race=allow_race)
    a, b = w_fast(d=d1), w_slow(d=d2)
    a["v"] >> PARENT["v"]
    b["v"] >> PARENT["v"]
    a["acc"] >> PARENT["acc"]
    b["acc"] >> PARENT["acc"]
    r = read(v=PARENT["v"], acc=PARENT["acc"])
    START >> [a, b]
    a >> r
    b >> r
    r >> END


def test_build_rejects_concurrent_writers():
    with pytest.raises(GraphValidationError) as caught:
        Operon(g_par, params={"d1": None, "d2": None})
    text = str(caught.value)
    assert "cell 'v'" in text and "'w_fast'" in text and "'w_slow'" in text
    assert "allow_race=True" in text and "reducers=" in text
    assert "cell 'acc'" not in text  # it has a reducer


async def test_allow_race_opts_out():
    engine = Operon(g_par(d1=None, d2=None, allow_race=True))
    out = await engine.run({"d1": 0.0, "d2": 0.01})
    assert out["final_v"] == "slow"
    Operon(g_par(d1=None, d2=None, allow_race=["v"]))


def test_allow_race_names_are_checked():
    with pytest.raises(ValueError, match="undeclared"):
        Operon(g_par(d1=None, d2=None, allow_race=["nope"]))


@graph
def g(d):
    PARENT.declare(v=None)
    a, b = w_fast(d=d), w_slow(d=d)
    a["v"] >> PARENT["v"]
    b["v"] >> PARENT["v"]
    START >> a >> b >> END


def test_ordered_writers_build():
    Operon(g, params={"d": None})


@op
def check(n: int) -> dict:
    return {"big": n > 10}


@op
def big(n: int) -> dict:
    return {"label": "big"}


@op
def small(n: int) -> dict:
    return {"label": "small"}


@graph
def branch_arms(n):
    PARENT.declare(label=None)
    c = check(n=n)
    b, s = big(n=n), small(n=n)
    b["label"] >> PARENT["label"]
    s["label"] >> PARENT["label"]
    START >> c >> if_(c["big"] == True, b).else_(s)  # noqa: E712
    [b, s] >> END


async def test_branch_arms_are_not_concurrent():
    engine = Operon(branch_arms, params={"n": None})
    assert (await engine.run({"n": 30}))["label"] == "big"


@op
async def may_fail(x: int) -> dict:
    raise ConnectionError("down")


@op
def fallback(error: str) -> dict:
    return {"v": "fallback"}


@graph
def op_and_handler(x):
    PARENT.declare(v=None)
    m = may_fail(x=x)
    f = fallback()
    m["v"] >> PARENT["v"]
    f["v"] >> PARENT["v"]
    START >> m >> END
    m.on_error(f)
    f >> END


def test_an_op_and_its_error_handler_are_not_concurrent():
    Operon(op_and_handler, params={"x": None})


@op
def step(n: int) -> dict:
    return {"n": n + 1, "done": n + 1 >= 3}


@op
def side(x: int) -> dict:
    return {"n": 100}


@graph
def loop_beside_sibling(x):
    PARENT.declare(n=0)
    s = step(n=PARENT["n"])
    o = side(x=x)
    s["n"] >> PARENT["n"]
    o["n"] >> PARENT["n"]
    START >> [s, o]
    s >> if_(s["done"] == True, END).else_(s)  # noqa: E712
    o >> END


def test_a_loop_racing_a_sibling_is_reported():
    with pytest.raises(GraphValidationError, match=r"'s' \(in a loop\)"):
        Operon(loop_beside_sibling, params={"x": None})
