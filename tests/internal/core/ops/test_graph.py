"""Tests for @graph decorator."""

import pytest

from operonx.core.engine import Operon
from operonx.core.ops.base import END, PARENT, START
from operonx.core.ops.graph.graph_op import GraphOp, graph
from operonx.core.ops.transform.func_op import op
from operonx.core.states.ref import Ref


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
    """Test that function params become PARENT refs or pass through static values."""

    def test_ref_params_injected_as_parent_refs(self):
        received = {}

        @graph
        def my_flow(x, y):
            received["x"] = x
            received["y"] = y
            step = double(x=x)
            step["result"] >> PARENT["result"]
            START >> step >> END

        my_flow(x=PARENT["a"], y=PARENT["b"])
        assert isinstance(received["x"], Ref)
        assert isinstance(received["y"], Ref)
        assert received["x"].var == "x"
        assert received["y"].var == "y"

    def test_static_params_pass_through(self):
        """Static values (non-Ref) should be passed directly to the function."""
        received = {}

        @graph
        def my_flow(x, y):
            received["x"] = x
            received["y"] = y
            step = double(x=PARENT["x"])
            step["result"] >> PARENT["result"]
            START >> step >> END

        my_flow(x=42, y="hello")
        assert received["x"] == 42
        assert received["y"] == "hello"
        assert not isinstance(received["x"], Ref)
        assert not isinstance(received["y"], Ref)

    def test_mixed_static_and_ref_params(self):
        """Mix of static values and Refs — each handled correctly."""
        received = {}

        @graph
        def my_flow(factor, val):
            received["factor"] = factor
            received["val"] = val
            step = double(x=val)
            step["result"] >> PARENT["result"]
            START >> step >> END

        my_flow(factor=3, val=PARENT["input"])
        assert received["factor"] == 3
        assert not isinstance(received["factor"], Ref)
        assert isinstance(received["val"], Ref)
        assert received["val"].var == "val"

    def test_static_dict_param_pass_through(self):
        """Dict values (like templates) should pass through as-is."""
        received = {}

        @graph
        def my_flow(template, query):
            received["template"] = template
            received["query"] = query
            step = double(x=PARENT["query"])
            step["result"] >> PARENT["result"]
            START >> step >> END

        tpl = {"system": "You are helpful.", "user": "{query}"}
        my_flow(template=tpl, query=PARENT["q"])
        assert received["template"] == tpl
        assert not isinstance(received["template"], Ref)
        assert isinstance(received["query"], Ref)

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

        engine = Operon(outer)
        result = await engine.run(inputs={"input": 5})
        assert result["result"] == 10


class TestSubgraphStaticExecution:
    """Test execution with static values passed through @graph."""

    @pytest.mark.asyncio
    async def test_static_value_used_at_build_time(self):
        """Static param used to control graph topology (like ChainOp's extract)."""

        @op
        def identity(x):
            return {"result": x}

        @op
        def negate(x: int):
            return {"result": -x}

        @graph
        def conditional_flow(use_negate, val):
            if use_negate:
                step = negate(x=val)
            else:
                step = identity(x=val)
            START >> step >> END

        # Test with use_negate=True
        with GraphOp(name="main1") as main1:
            f = conditional_flow(use_negate=True, val=PARENT["input"])
            START >> f >> END

        result = await Operon(main1).run(inputs={"input": 5})
        assert result["result"] == -5

        # Test with use_negate=False
        with GraphOp(name="main2") as main2:
            f = conditional_flow(use_negate=False, val=PARENT["input"])
            START >> f >> END

        result = await Operon(main2).run(inputs={"input": 5})
        assert result["result"] == 5

    @pytest.mark.asyncio
    async def test_static_config_with_ref_data(self):
        """Static config (like resource string) + Ref data (like query)."""

        @op
        def multiply(x: int, factor: int):
            return {"result": x * factor}

        @graph
        def scaled_flow(factor, val):
            step = multiply(x=val, factor=factor)
            START >> step >> END

        with GraphOp(name="main") as main:
            s = scaled_flow(factor=10, val=PARENT["input"])
            START >> s >> END

        result = await Operon(main).run(inputs={"input": 3})
        assert result["result"] == 30

    @pytest.mark.asyncio
    async def test_contain_generation_as_init_kwarg(self):
        """contain_generation should pass through as an init kwarg, not an input."""

        @graph
        def gen_flow(val):
            step = double(x=val)
            START >> step >> END

        with GraphOp(name="main") as main:
            g = gen_flow(val=PARENT["input"], contain_generation=True)
            START >> g >> END

        assert g.contain_generation is True


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

        engine = Operon(main)
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

        engine = Operon(main)
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

        engine = Operon(main)
        result = await engine.run(inputs={"input": 7})
        assert result["answer"] == 14
