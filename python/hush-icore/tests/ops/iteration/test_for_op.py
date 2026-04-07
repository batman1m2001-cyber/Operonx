"""Tests for sequential iteration patterns using generator ops.

These tests replace the old ForOp tests. Generator ops + the streaming
scheduler in GraphOp now handle all iteration patterns.
"""

import pytest

from hush.core import END, PARENT, START, GraphOp, op
from hush.core.states import MemoryState, StateSchema

# ============================================================
# Test 1: Simple Sequential Iteration
# ============================================================


class TestSimpleIteration:
    """Test basic iteration with generator op."""

    @pytest.mark.asyncio
    async def test_double_values(self):
        """Test simple doubling of values via generator."""

        @op
        def each_item(items: list):
            for item in items:
                yield {"value": item}

        @op
        def double(value: int):
            return {"result": value * 2}

        with GraphOp(name="double_loop") as g:
            src = each_item(items=PARENT["items"])
            d = double(value=src["value"])
            START >> src >> d >> END

        g.build()
        schema = StateSchema(g)
        state = schema.create_state(inputs={"items": [1, 2, 3, 4, 5]})

        result = {}
        async for _, _frame in g.run(state):
            for _k, _v in _frame.items():
                result.setdefault(_k, []).append(_v)
        assert result["result"] == [2, 4, 6, 8, 10]


# ============================================================
# Test 2: Iteration with Broadcast
# ============================================================


class TestBroadcastIteration:
    """Test iteration with broadcast values from batch op."""

    @pytest.mark.asyncio
    async def test_multiply_with_broadcast(self):
        """Test multiplication with broadcast multiplier."""

        @op
        def each_item(items: list):
            for item in items:
                yield {"value": item}

        @op
        def get_config():
            return {"multiplier": 10}

        @op
        def multiply(value: int, multiplier: int):
            return {"result": value * multiplier}

        with GraphOp(name="multiply_loop") as g:
            cfg = get_config()
            src = each_item(items=PARENT["items"])
            m = multiply(value=src["value"], multiplier=cfg["multiplier"])
            START >> [cfg, src]
            [cfg, src] >> m >> END

        g.build()
        schema = StateSchema(g)
        state = schema.create_state(inputs={"items": [1, 2, 3]})

        result = {}
        async for _, _frame in g.run(state):
            for _k, _v in _frame.items():
                result.setdefault(_k, []).append(_v)
        assert result["result"] == [10, 20, 30]


# ============================================================
# Test 3: Multiple Lists (Zip)
# ============================================================


class TestMultipleListsZip:
    """Test zipping two lists inside a generator."""

    @pytest.mark.asyncio
    async def test_zip_two_lists(self):
        """Test zipping two lists together via generator."""

        @op
        def zip_items(xs: list, ys: list):
            for x, y in zip(xs, ys):
                yield {"x": x, "y": y}

        @op
        def add(x: int, y: int):
            return {"sum": x + y}

        with GraphOp(name="add_loop") as g:
            src = zip_items(xs=PARENT["xs"], ys=PARENT["ys"])
            a = add(x=src["x"], y=src["y"])
            START >> src >> a >> END

        g.build()
        schema = StateSchema(g)
        state = schema.create_state(inputs={"xs": [1, 2, 3], "ys": [10, 20, 30]})

        result = {}
        async for _, _frame in g.run(state):
            for _k, _v in _frame.items():
                result.setdefault(_k, []).append(_v)
        assert result["sum"] == [11, 22, 33]


# ============================================================
# Test 4: Nested Iteration (Generator inside Generator)
# ============================================================


class TestNestedIteration:
    """Test nested iteration using nested graphs with generators."""

    @pytest.mark.asyncio
    async def test_nested_loop_with_outer_variable(self):
        """Test nested iteration where inner loop depends on outer variable."""

        @op
        def outer_iter(xs: list):
            for x in xs:
                yield {"x": x}

        @op
        def inner_iter(ys: list, x: int):
            for y in ys:
                yield {"result": x * y}

        # Inner graph: iterates ys for a given x
        @op
        def multiply(x: int, y: int):
            return {"result": x * y}

        with GraphOp(name="outer_loop") as g:
            src = outer_iter(xs=PARENT["xs"])

            # For each x, create an inner graph that iterates ys
            inner = inner_iter(ys=PARENT["ys"], x=src["x"])
            START >> src >> inner >> END

        g.build()
        schema = StateSchema(g)
        state = schema.create_state(inputs={"xs": [1, 2, 3], "ys": [10, 20]})

        result = {}
        async for _, _frame in g.run(state):
            for _k, _v in _frame.items():
                result.setdefault(_k, []).append(_v)
        # Outer yields 3 items, inner yields 2 per outer → 6 total (nested streaming)
        assert result["result"] == [10, 20, 20, 40, 30, 60]


# ============================================================
# Test 5: Empty Iteration
# ============================================================


class TestEmptyIteration:
    """Test behavior with empty iteration data."""

    @pytest.mark.asyncio
    async def test_empty_list(self):
        """Test iteration over empty list."""

        @op
        def each_item(items: list):
            for item in items:
                yield {"value": item}

        @op
        def double(value: int):
            return {"result": value * 2}

        with GraphOp(name="empty_loop") as g:
            src = each_item(items=PARENT["items"])
            d = double(value=src["value"])
            START >> src >> d >> END

        g.build()
        schema = StateSchema(g)
        state = schema.create_state(inputs={"items": []})

        result = {}
        async for _, _frame in g.run(state):
            for _k, _v in _frame.items():
                result.setdefault(_k, []).append(_v)
        assert result.get("result", []) == []


# ============================================================
# Test 6: Ref from Previous Node
# ============================================================


class TestRefFromPreviousNode:
    """Test iteration using data from previous node."""

    @pytest.mark.asyncio
    async def test_each_from_ref(self):
        """Test generator receiving list from upstream node."""

        @op
        def generate_data():
            return {"numbers": [10, 20, 30], "factor": 5}

        @op
        def each_item(items: list):
            for item in items:
                yield {"item": item}

        @op
        def process_item(item: int, factor: int):
            return {"result": item * factor}

        with GraphOp(name="ref_test_graph") as graph:
            gen_node = generate_data()
            src = each_item(items=gen_node["numbers"])
            proc = process_item(item=src["item"], factor=gen_node["factor"])
            START >> gen_node >> src >> proc >> END

        graph.build()
        schema = StateSchema(graph)
        state = schema.create_state()

        result = {}
        async for _, _frame in graph.run(state):
            for _k, _v in _frame.items():
                result.setdefault(_k, []).append(_v)
        assert result["result"] == [50, 100, 150]


# ============================================================
# Test 7: Accumulation Pattern (Sequential via Generator)
# ============================================================


class TestAccumulationPattern:
    """Test accumulating values — generator naturally preserves order."""

    @pytest.mark.asyncio
    async def test_running_total(self):
        """Test accumulating values across iterations.

        Generator yields are sequential, so closures work for accumulation.
        """
        totals = []

        @op
        def accumulate_items(items: list):
            for item in items:
                prev_total = totals[-1] if totals else 0
                new_total = prev_total + item
                totals.append(new_total)
                yield {"running_total": new_total}

        with GraphOp(name="accumulate_loop") as g:
            src = accumulate_items(items=PARENT["items"])
            START >> src >> END

        g.build()
        schema = StateSchema(g)
        state = schema.create_state(inputs={"items": [1, 2, 3, 4, 5]})

        result = {}
        async for _, _frame in g.run(state):
            for _k, _v in _frame.items():
                result.setdefault(_k, []).append(_v)
        assert result["running_total"] == [1, 3, 6, 10, 15]


# ============================================================
# Test 8: Chain of Downstream Ops
# ============================================================


class TestChainOfDownstreamOps:
    """Test multiple ops chained after a generator."""

    @pytest.mark.asyncio
    async def test_add_then_multiply(self):
        """Test chain: generator >> add_one >> multiply_two >> END."""

        @op
        def each_item(items: list):
            for item in items:
                yield {"x": item}

        @op
        def add_one(x: int):
            return {"y": x + 1}

        @op
        def multiply_two(y: int):
            return {"z": y * 2}

        with GraphOp(name="chain_loop") as g:
            src = each_item(items=PARENT["items"])
            n1 = add_one(x=src["x"])
            n2 = multiply_two(y=n1["y"])
            START >> src >> n1 >> n2 >> END

        g.build()
        schema = StateSchema(g)
        state = schema.create_state(inputs={"items": [1, 2, 3]})

        result = {}
        async for _, _frame in g.run(state):
            for _k, _v in _frame.items():
                result.setdefault(_k, []).append(_v)
        assert result["z"] == [4, 6, 8]
