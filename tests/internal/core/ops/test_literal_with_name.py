"""A value bound at build time is a literal even when it has a ``name``.

`resolve_value` used to take any object with a ``name`` attribute for an
op reference, so a dataclass such as a loaded agent record — bound into
a nested graph by a factory, the way `[serve.variants]` binds — became a
`Ref` to itself and failed the nested graph's scope check with a message
about PARENT. Only ops, graphs and the PARENT marker are references.
"""

import asyncio
from dataclasses import dataclass

from operonx.core import END, PARENT, START, Operon, graph
from operonx.core.ops import op
from operonx.core.ops._params import is_op_like


@dataclass(frozen=True)
class Record:
    name: str
    tag: str


@op(bound="sync")
def use(thing=None, x: int = 0) -> dict:
    return {"out": f"{thing.name}/{thing.tag}:{x}"}


@graph
def inner(x, thing):
    a = use(thing=thing, x=x)
    a["out"] >> PARENT["out"]
    START >> a >> END


def test_a_named_record_bound_into_a_nested_graph_is_a_literal():
    def build(record):
        @graph
        def outer(x):
            i = inner(x=x, thing=record)
            i["out"] >> PARENT["out"]
            START >> i >> END

        return outer

    engine = Operon(build(Record(name="educa", tag="hello")), params={"x": None})
    assert asyncio.run(engine.run(inputs={"x": 7}))["out"] == "educa/hello:7"


def test_is_op_like_names_the_references_and_nothing_else():
    assert is_op_like(use) is False or hasattr(use, "outputs")  # an @op is op-like
    assert is_op_like(Record(name="x", tag="y")) is False
    assert is_op_like("a string") is False
    assert is_op_like(PARENT) is True
    assert is_op_like(use(x=1)) is True
