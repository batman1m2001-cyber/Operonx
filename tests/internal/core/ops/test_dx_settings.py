"""DX_PLAN X3 and X6: a keyword the function takes is its input, settings
that collide go through ``configure``; ``$cells`` has the final cells."""

from __future__ import annotations

import asyncio
import operator

import pytest

from operonx import END, PARENT, START, Operon, graph, op
from operonx.core.policy import Retry

pytestmark = pytest.mark.unit


@op
def label(id: int, name: str) -> dict:  # noqa: A002 — the point of the test
    return {"text": f"{name}#{id}"}


@graph
def g(x):
    lbl = label(id=7, name="order")  # literals: before, both were op settings
    START >> lbl >> END


def test_a_keyword_the_function_takes_reaches_it():
    out = asyncio.run(Operon(g, params={"x": None}).run({"x": 1}))
    assert out["text"] == "order#7"


@graph
def g_configure_gives_a_colliding_setting(x):
    lbl = label.configure(name="labeller", retry=Retry(max_attempts=2))(id=x, name="order")
    START >> lbl >> END


def test_configure_gives_a_colliding_setting():
    engine = Operon(g_configure_gives_a_colliding_setting, params={"x": None})
    assert "labeller" in engine.graph._ops
    assert asyncio.run(engine.run({"x": 3}))["text"] == "order#3"


def test_configure_refuses_what_is_not_a_setting():
    with pytest.raises(TypeError, match="not an op setting"):
        label.configure(colour="red")


@op
def add(n: int) -> dict:
    return {"item": [n]}


@graph
def g_cells_holds_the_final_values(n):
    PARENT.declare(seen=[], reducers={"seen": operator.add})
    a = add(n=n)
    b = add(n=n)
    a["item"] >> PARENT["seen"]
    b["item"] >> PARENT["seen"]
    START >> a >> b >> END


def test_cells_holds_the_final_values():
    out = asyncio.run(Operon(g_cells_holds_the_final_values, params={"n": None}).run({"n": 5}))
    assert out["$cells"] == {"seen": [5, 5]}
