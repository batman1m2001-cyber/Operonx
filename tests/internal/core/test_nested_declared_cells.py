"""A declared cell in a nested graph holds the same value as in a standalone run.

A graph that declares ``PARENT.declare(xs=[...], reducers=...)`` and also
takes ``xs`` as its input has one cell in both roles. Run on its own, the
inputs are written into it first. Nested inside another graph, two things
went wrong, both silently:

- the parent's value never entered the cell (a shared cell is never
  pulled), so the graph ran without its input;
- when the graph finished, its output — the cell's own accumulated value —
  was stored back into the cell, and the reducer appended it again: every
  item came out twice.

An agent's conversation is exactly such a cell, so an agent used as a node
lost the question and doubled every message.
"""

from __future__ import annotations

import pytest

from operonx import END, PARENT, START, Operon, graph, op

pytestmark = pytest.mark.unit


def append(old, new):
    return (old or []) + (new or [])


@op
def step(xs: list = None) -> dict:
    return {"xs": [f"step saw {len(xs or [])}"]}


@graph
def collector(xs=None):
    PARENT.declare(xs=[], reducers={"xs": append})
    s = step(xs=PARENT["xs"])
    s["xs"] >> PARENT["xs"]
    START >> s >> END


@op
def seed(word: str) -> dict:
    return {"xs": [word]}


@op
def after(xs: list = None) -> dict:
    return {"seen": xs}


@graph
def outer(word):
    s = seed(word=word)
    c = collector(xs=s["xs"])
    a = after(xs=c["xs"])
    START >> s >> c >> a >> END


async def test_standalone_reference():
    engine = Operon(collector, params={"xs": None})
    out = await engine.run(inputs={"xs": ["hello"]})
    assert out["$state"][engine.name, "xs"] == ["hello", "step saw 1"]


async def test_nested_cell_starts_from_the_parents_value_and_is_not_doubled():
    engine = Operon(outer, params={"word": None})
    out = await engine.run(inputs={"word": "hello"})
    assert "$errors" not in out
    # the next op reads exactly what the standalone run holds
    assert out["seen"] == ["hello", "step saw 1"]
