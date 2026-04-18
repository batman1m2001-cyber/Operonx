"""Tests: Ref scope validation in @graph.

Verifies that Refs pointing to ops outside a subgraph are caught at build time
with a clear error message suggesting PARENT.
"""

import pytest

from operon.core import END, PARENT, START, graph
from operon.core.ops import op


@op
def outer_op(text: str) -> dict:
    return {"result": text.upper()}


@op
def inner_op(data: str) -> dict:
    return {"output": data}


class TestRefScopeValidation:
    def test_ref_to_parent_op_raises(self):
        """Ref to an op in parent graph should raise ValueError at build time."""
        outer = outer_op(text="hello")

        with pytest.raises(ValueError, match="outside this graph's scope"):

            @graph
            def bad_graph(text):
                # inner_op references outer's output — outer is NOT in this graph
                inner = inner_op(data=outer["result"])
                START >> inner >> END

            bad_graph(text="test")

    def test_ref_to_parent_via_parent_ok(self):
        """Ref via PARENT is valid — no error."""

        @graph
        def good_graph(data):
            inner = inner_op(data=data)
            START >> inner >> END

        # Should not raise
        g = good_graph(data="hello")
        assert g is not None

    def test_ref_to_sibling_op_ok(self):
        """Ref to another op inside the same graph is valid."""

        @op
        def step1(text: str) -> dict:
            return {"mid": text}

        @op
        def step2(mid: str) -> dict:
            return {"out": mid}

        @graph
        def wf(text):
            s1 = step1(text=text)
            s2 = step2(mid=s1["mid"])
            START >> s1 >> s2 >> END

        g = wf(text="test")
        assert g is not None

    def test_nested_graph_ref_scope(self):
        """Ref from nested subgraph to parent graph op should raise."""
        parent_op = outer_op(text="parent_data")

        with pytest.raises(ValueError, match="outside this graph's scope"):

            @graph
            def sub(val):
                # References parent_op which is outside sub's scope
                inner = inner_op(data=parent_op["result"])
                START >> inner >> END

            sub(val="test")

    def test_error_message_suggests_parent(self):
        """Error message should suggest using PARENT."""
        outer = outer_op(text="hello")

        with pytest.raises(ValueError, match="Pass the value through PARENT"):

            @graph
            def bad(text):
                inner = inner_op(data=outer["result"])
                START >> inner >> END

            bad(text="test")
