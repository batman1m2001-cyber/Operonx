"""Tests for @graph decorator."""

import pytest

from hush.core.engine import Hush
from hush.core.ops.base import END, PARENT, START
from hush.core.ops.graph.graph_op import GraphOp, graph
from hush.core.ops.transform.func_op import op
from hush.core.states.ref import Ref


@op
def double(x: int):
    return {"result": x * 2}


@op
def add(a: int, b: int):
    return {"result": a + b}


class TestSubgraphAutoName:
    """Test auto-naming through @graph decorator."""

    def test_basic_auto_name(self):
        @graph
        def my_flow(val):
            step = double(x=val)
            step["result"] >> PARENT["result"]
            START >> step >> END

        g = my_flow(val=10)
        assert g.name == "g"

    def test_no_params_auto_name(self):
        """Function with no params — caller kwargs are graph inputs only."""

        @graph
        def my_flow():
            step = double(x=PARENT["val"])
            step["result"] >> PARENT["result"]
            START >> step >> END

        g = my_flow(val=10)
        assert g.name == "g"

    def test_explicit_name_overrides(self):
        @graph
        def my_flow(val):
            step = double(x=val)
            step["result"] >> PARENT["result"]
            START >> step >> END

        g = my_flow(val=10, name="custom")
        assert g.name == "custom"

    def test_returns_graph_op(self):
        @graph
        def my_flow(val):
            step = double(x=val)
            step["result"] >> PARENT["result"]
            START >> step >> END

        g = my_flow(val=10)
        assert isinstance(g, GraphOp)


class TestSubgraphParams:
    """Test that function params become PARENT refs."""

    def test_params_injected_as_refs(self):
        received = {}

        @graph
        def my_flow(x, y):
            received["x"] = x
            received["y"] = y
            step = double(x=x)
            step["result"] >> PARENT["result"]
            START >> step >> END

        my_flow(x=10, y=20)
        assert isinstance(received["x"], Ref)
        assert isinstance(received["y"], Ref)
        assert received["x"].var == "x"
        assert received["y"].var == "y"

    def test_graph_has_inputs(self):
        @graph
        def my_flow(x):
            step = double(x=x)
            step["result"] >> PARENT["result"]
            START >> step >> END

        g = my_flow(x=PARENT["val"])
        assert "x" in g.inputs


class TestSubgraphNested:
    """Test @graph inside another graph."""

    def test_subgraph_in_graph(self):
        @graph
        def inner_flow(val):
            step = double(x=val)
            START >> step >> END

        with GraphOp(name="outer") as outer:
            sub = inner_flow(val=PARENT["input"])
            START >> sub >> END

        outer.build()
        assert "sub" in outer._ops
        assert isinstance(outer._ops["sub"], GraphOp)

    @pytest.mark.asyncio
    async def test_nested_execution(self):
        """Subgraph inside a parent graph, both relying on >> END auto-forwarding."""

        @graph
        def inner_flow(val):
            step = double(x=val)
            START >> step >> END

        with GraphOp(name="outer") as outer:
            sub = inner_flow(val=PARENT["input"])
            START >> sub >> END

        engine = Hush(outer)
        result = await engine.run(inputs={"input": 5})
        assert result["result"] == 10


class TestSubgraphExecution:
    """Test full async execution of @graph."""

    @pytest.mark.asyncio
    async def test_execution(self):
        """Pure >> END auto-forwarding, no explicit PARENT mapping."""

        @graph
        def double_flow(val):
            step = double(x=val)
            START >> step >> END

        with GraphOp(name="main") as main:
            d = double_flow(val=PARENT["input"])
            START >> d >> END

        engine = Hush(main)
        result = await engine.run(inputs={"input": 5})
        assert result["result"] == 10

    @pytest.mark.asyncio
    async def test_chained_subgraphs(self):
        @graph
        def double_flow(val):
            step = double(x=val)
            START >> step >> END

        with GraphOp(name="main") as main:
            d1 = double_flow(val=PARENT["input"])
            d2 = double_flow(val=d1["result"])
            START >> d1 >> d2 >> END

        engine = Hush(main)
        result = await engine.run(inputs={"input": 3})
        assert result["result"] == 12  # 3 * 2 * 2

    @pytest.mark.asyncio
    async def test_with_renamed_outputs(self):
        """Explicit output renaming inside graph + explicit in outer graph."""

        @graph
        def double_flow(val):
            step = double(x=val)
            step["result"] >> PARENT["doubled"]
            START >> step >> END

        with GraphOp(name="main") as main:
            d = double_flow(val=PARENT["input"])
            d["doubled"] >> PARENT["answer"]
            START >> d >> END

        engine = Hush(main)
        result = await engine.run(inputs={"input": 7})
        assert result["answer"] == 14
