"""A Ref in ``validators=`` is refused when the op is built.

``validators=`` is a build-time value; nothing resolves a Ref inside it.
What happened instead depended on where the Ref sat, and none of it was
an error:

* ``validators=PARENT["allowed"]`` — a Ref is callable (it records a
  ``call`` transform), so it was taken for a predicate. Calling it
  returned another Ref, which is truthy: **every** answer passed.
* ``validators={"intent": PARENT["allowed"]}`` — iterating the Ref walks
  ``ref[0], ref[1], …`` forever (each ``__getitem__`` is a new Ref), so
  the op hung the event loop.
* ``validators={"intent": ["a", PARENT["b"]]}`` — ``value == Ref`` is a
  Ref, truthy, so every value counted as allowed.
"""

from __future__ import annotations

import pytest

from operonx import END, START, Operon, graph
from operonx.core import PARENT
from operonx.providers.ops import LLMOp

pytestmark = pytest.mark.unit


def _build(validators):
    return LLMOp.of(
        resource="r", prompt="Classify: {q}", q="x", fields=["intent: str"], validators=validators
    )


class TestRefsAreRefused:
    @pytest.mark.parametrize(
        "validators",
        [
            pytest.param(PARENT["allowed"], id="whole"),
            pytest.param({"intent": PARENT["allowed"]}, id="allow-list"),
            pytest.param({"intent": ["a", PARENT["b"]]}, id="one-entry"),
        ],
    )
    def test_at_construction(self, validators):
        with pytest.raises(TypeError, match=r"built.*op after the LLM"):
            _build(validators)

    def test_a_graph_parameter_is_a_ref_too(self):
        @graph
        def classify(q, allowed):
            llm = LLMOp.of(
                resource="r", prompt="{q}", q=q, fields=["intent: str"], validators=allowed
            )
            START >> llm >> END

        with pytest.raises(TypeError, match="validators"):
            Operon(classify, params={"q": None, "allowed": None})

    def test_a_ref_without_fields_gets_the_ref_message(self):
        with pytest.raises(TypeError, match="Ref"):
            LLMOp.of(resource="r", prompt="hi", validators=PARENT["allowed"])


class TestBuildTimeValuesStillWork:
    def test_an_allow_list(self):
        op = _build({"intent": ["a", "b", "@a"]})
        assert op.validators == {"intent": ["a", "b", "@a"]}

    def test_a_callable(self):
        def ok(parsed):
            return True

        assert _build(ok).validators is ok

    def test_a_wrong_type_is_refused_at_construction_too(self):
        """It used to become a runtime ``error`` string on every call."""
        with pytest.raises(TypeError, match="dict or a callable"):
            _build(["a", "b"])
