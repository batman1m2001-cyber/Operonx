"""An op failing inside a subgraph stops the ops after it (roadmap C2).

Flat, a failing op emits nothing, so the op after it never runs. Nested,
the subgraph finished its run, collected its outputs — all ``None``,
because the op that writes them raised — and yielded them as an ordinary
result. The op after it ran on ``None``, and over HTTP a request whose
subgraph raised answered ``200 null``.

The subgraph now yields nothing when an op under it raised and none of
its outputs were written, so its successors are skipped exactly as in the
flat case, and ``$errors`` has an entry for the subgraph that names the
op that raised.
"""

from __future__ import annotations

import pytest

from operonx import END, START, Operon, graph, op


@op
def boom(x: int) -> dict:
    raise ValueError("boom")
    return {"y": x}


@op
def after(y: int = None) -> dict:
    return {"z": f"after ran with y={y!r}"}


@graph
def inner(x):
    b = boom(x=x)
    START >> b >> END


@graph
def nested(x):
    s = inner(x=x)
    a = after(y=s["y"])
    START >> s >> a >> END


@graph
def flat(x):
    b = boom(x=x)
    a = after(y=b["y"])
    START >> b >> a >> END


async def _run(g) -> tuple:
    engine = Operon(g, params={"x": None})
    return engine.name, await engine.run(inputs={"x": 1})


async def test_subgraph_failure_stops_successors():
    """`evidence/repros/subgraph_failure_leaks.py`: same answer flat and nested."""
    _, out_flat = await _run(flat)
    name, out_nested = await _run(nested)

    assert "z" not in out_flat
    assert "z" not in out_nested


async def test_errors_name_the_subgraph_and_the_op_that_raised():
    name, out = await _run(nested)
    errors = out["$errors"]

    assert "ValueError: boom" in errors[f"{name}.s.b"]
    # The subgraph's own entry points at the op that raised, not at a
    # second copy of its traceback.
    assert f"{name}.s.b" in errors[f"{name}.s"]
    assert "Traceback" not in errors[f"{name}.s"]


@graph
def inner_twice(x):
    s = inner(x=x)
    START >> s >> END


@graph
def nested_twice(x):
    s = inner_twice(x=x)
    a = after(y=s["y"])
    START >> s >> a >> END


async def test_failure_two_levels_down_stops_successors():
    name, out = await _run(nested_twice)

    assert "z" not in out
    assert f"{name}.s.s.b" in out["$errors"][f"{name}.s"]


@op
def each(n: int):
    for i in range(n):
        yield {"x": i}


@graph
def per_item(n):
    e = each(n=n)
    s = inner(x=e["x"])
    a = after(y=s["y"])
    START >> e >> s >> a >> END


async def test_every_failing_item_is_stopped_not_only_the_first():
    """`$errors` keeps the first failure of each op; the check must not
    lean on it, or only the first item would be stopped."""
    engine = Operon(per_item, params={"n": None})
    out = await engine.run(inputs={"n": 3})

    assert "z" not in out


@op
def nothing(x: int) -> dict:
    return {"y": None}


@graph
def inner_none(x):
    n = nothing(x=x)
    START >> n >> END


@graph
def nested_none(x):
    s = inner_none(x=x)
    a = after(y=s["y"])
    START >> s >> a >> END


async def test_a_subgraph_that_answers_none_without_failing_still_flows():
    """The skip is for failure only: a None answer is still an answer."""
    _, out = await _run(nested_none)

    assert out["z"] == "after ran with y=None"
    assert "$errors" not in out


starlette = pytest.importorskip("starlette")


def test_http_door_answers_500_not_200_null():
    from starlette.testclient import TestClient

    from operonx.app.manifest import ServeSpec
    from operonx.app.serve import egress, ingress
    from operonx.app.serve.app import build_app

    @op(bound="sync")
    def explode(item=None) -> dict:
        raise RuntimeError("upstream rejected the request")
        return {"answer": item}

    @graph
    def stage(item):
        x = explode(item=item)
        START >> x >> END

    @graph
    def door():
        src = ingress()
        s = stage(item=src["item"])
        out = egress(item=s["answer"])
        START >> src >> s >> out >> END

    spec = ServeSpec(name="d", kind="http", graph="x:y", path="/go", method="POST")
    app = build_app((spec,), engines={"d": Operon(door)})
    with TestClient(app) as client:
        response = client.post("/go", json="hello")

    assert response.status_code == 500, response.text
    assert response.json() == {"error": "the graph produced no output", "endpoint": "d"}
