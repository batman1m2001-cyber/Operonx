"""Error edges: ``op.on_error(handler)``.

R1 in docs/roadmap/ROADMAP.md, row 7 of track2_langgraph_gap.md. Before, an
op that raised was recorded and everything after it silently never ran;
there was nowhere to route the failure to.
"""

import asyncio

import pytest

from operonx import END, START, Operon, Retry, graph, op
from operonx.core.ops import if_

HANDLED = []


@op(retry=Retry(max_attempts=3, initial=0.01, jitter=False))
async def lookup(order: int) -> dict:
    if order < 0:
        raise ConnectionError("crm down")
    return {"status": f"order {order} shipped"}


@op
def apologise(error: str, op: str, inputs: dict) -> dict:
    HANDLED.append({"error": error, "op": op, "inputs": inputs})
    return {"status": "sorry, try later"}


@graph
def answer(order):
    look = lookup(order=order)
    sorry = apologise()
    START >> look >> END
    look.on_error(sorry)
    sorry >> END


async def test_error_edge_handler_runs_once_with_error_text():
    HANDLED.clear()
    engine = Operon(answer, params={"order": None})
    out = await engine.run({"order": -1})

    assert out["status"] == "sorry, try later"
    assert HANDLED == [
        {"error": "ConnectionError: crm down", "op": f"{engine.name}.look", "inputs": {"order": -1}}
    ]  # once, after the last of 3 attempts
    # Handled is not hidden: the failure is still on the record.
    assert list(out["$errors"]) == [f"{engine.name}.look"]


async def test_error_edge_not_taken_on_success():
    HANDLED.clear()
    out = await Operon(answer, params={"order": None}).run({"order": 7})
    assert out["status"] == "order 7 shipped"
    assert HANDLED == [] and "$errors" not in out


@op
def reply(status: str) -> dict:
    return {"text": status.upper()}


@graph
def answer_then_reply(order):
    look = lookup(order=order)
    sorry = apologise()
    r = reply(status=look["status"])
    look.on_error(sorry)
    sorry["status"] >> r["status"]
    START >> look >> r >> END
    sorry >> r  # an op and its handler are exclusive: r merges them by itself


async def test_error_edge_merge_auto_softens():
    engine = Operon(answer_then_reply, params={"order": None})
    ok = await asyncio.wait_for(engine.run({"order": 2}), 2)
    failed = await asyncio.wait_for(engine.run({"order": -2}), 2)
    assert ok["text"] == "ORDER 2 SHIPPED"
    assert failed["text"] == "SORRY, TRY LATER"


@op
def note(error: str) -> dict:
    return {"noted": error}


@op
def sync_bad(x: int) -> dict:
    raise KeyError(x)


@graph
def g(x):
    b = sync_bad(x=x)
    n = note()
    START >> b >> END
    b.on_error(n)
    n >> END


async def test_error_edge_from_an_inline_op():
    out = await Operon(g, params={"x": None}).run({"x": 4})
    assert out["noted"] == "KeyError: 4"


@op
def items(n: int):
    for i in range(n):
        yield {"i": i}


@op
async def odd_fails(i: int) -> dict:
    if i % 2:
        raise ValueError(f"odd {i}")
    return {"ok": i}


@graph
def per_item_handler(n):
    it = items(n=n)
    w = odd_fails(i=it["i"])
    n_ = note()
    START >> it >> w >> END
    w.on_error(n_)
    n_ >> END


async def test_error_edge_per_item():
    out = await Operon(per_item_handler, params={"n": None}).run({"n": 4})
    assert out["ok"] == [0, 2]
    assert out["noted"] == ["ValueError: odd 1", "ValueError: odd 3"]


async def test_errors_raise_ignores_handled_error():
    out = await Operon(answer, params={"order": None}, errors="raise").run({"order": -1})
    assert out["status"] == "sorry, try later"


@op
async def boom(x: int) -> dict:
    raise RuntimeError("inner failure")


@graph
def failing_sub(x):
    b = boom(x=x)
    START >> b >> END


@graph
def subgraph_handler(x):
    sub = failing_sub(x=x)
    n = note()
    START >> sub >> END
    sub.on_error(n)
    n >> END


async def test_error_edge_on_a_subgraph():
    out = await Operon(subgraph_handler, params={"x": None}).run({"x": 1})
    assert out["noted"].startswith("SubgraphError")


@graph
def other(x):
    n = note()
    START >> n >> END


@graph
def handler_on_start(x):
    n = note()
    START.on_error(n)


@graph
def handler_on_branch(x):
    b_ = boom(x=x)
    n = note()
    route = if_(b_["y"] == 1, n).else_(n)
    START >> b_ >> route
    route.on_error(n)


def test_on_error_wiring_is_checked():
    with pytest.raises(TypeError, match="sentinel"):
        handler_on_start(x=1)

    with pytest.raises(TypeError, match="branch"):
        handler_on_branch(x=1)

    with pytest.raises(ValueError, match="same graph"):

        @graph
        def c(x):
            b_ = boom(x=x)
            sub = other(x=x)
            START >> b_ >> sub >> END
            b_.on_error(sub._ops["n"])

        c(x=1)


def test_error_edge_is_serialized():
    g = answer(order=None)
    g.build()
    edges = {(e["from"], e["to"]): e for e in g.serialize()["edges"]}
    assert edges[("look", "sorry")].get("kind") == "error"
    # Only an error edge says so: every other edge, and every fingerprint
    # hashed from one, is unchanged.
    assert all("kind" not in e for key, e in edges.items() if key != ("look", "sorry"))
