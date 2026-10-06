"""Return annotations written as strings — ``from __future__ import annotations``.

Under PEP 563 ``-> dict`` reaches ``inspect.signature`` as the string
``"dict"``. It was read as "not a mapping", so an op returning a helper's
dict got one scalar output, ``value``: graph validation refused every
reader of its real keys, and at run time the op failed with
``KeyError: '(flow.s, a) not found in schema'``.
"""

from __future__ import annotations

from typing import Dict

from operonx import END, START, Operon, graph, op
from operonx.core.ops.base import SCALAR_OUTPUT


def _reply() -> dict:
    return {"a": 1}


@op
def built_elsewhere(x: int = 0) -> dict:
    return _reply()


@op
def typed_mapping(x: int = 0) -> Dict[str, int]:
    return _reply()


@op
def plus(a: int) -> dict:
    return {"b": a + 1}


@op
def is_small(n: int = 0) -> bool:
    return n < 3


@op
def forward_ref(x: int = 0) -> NotDefinedAnywhere:  # noqa: F821
    return _reply()


def test_a_string_dict_annotation_is_a_mapping():
    for factory in (built_elsewhere, typed_mapping, forward_ref):
        assert SCALAR_OUTPUT not in factory().outputs, factory


def test_a_string_scalar_annotation_still_names_one_output():
    assert set(is_small().outputs) == {SCALAR_OUTPUT}


@graph
def flow(x):
    s = built_elsewhere(x=x)
    p = plus(a=s["a"])
    START >> s >> p >> END


async def test_an_op_returning_a_helpers_dict_runs_in_a_graph():
    out = await Operon(flow, params={"x": None}).run({"x": 1})
    assert out["b"] == 2 and "$errors" not in out
