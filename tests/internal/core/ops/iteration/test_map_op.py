"""Tests for parallel iteration patterns using generator ops.

These tests replace the old MapOp tests. Generator ops + the streaming
scheduler in GraphOp now handle all parallel iteration patterns.
MapOp's parallel execution is the default behavior of the streaming scheduler.
"""

import asyncio

import pytest

from operon.core import END, PARENT, START, GraphOp, op
from operon.core.states import MemoryState, StateSchema

# ============================================================
# Test 1: Simple Iteration
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

    @pytest.mark.asyncio
    async def test_multiple_each_with_broadcast(self):
        """Test multiple zipped lists plus broadcast config."""

        @op
        def zip_items(xs: list, ys: list):
            for x, y in zip(xs, ys):
                yield {"x": x, "y": y}

        @op
        def get_factor():
            return {"factor": 2}

        @op
        def compute(x: int, y: int, factor: int):
            return {"result": (x + y) * factor}

        with GraphOp(name="compute_loop") as g:
            f = get_factor()
            src = zip_items(xs=PARENT["xs"], ys=PARENT["ys"])
            c = compute(x=src["x"], y=src["y"], factor=f["factor"])
            START >> [f, src]
            [f, src] >> c >> END

        g.build()
        schema = StateSchema(g)
        state = schema.create_state(inputs={"xs": [1, 2, 3], "ys": [10, 20, 30]})

        result = {}
        async for _, _frame in g.run(state):
            for _k, _v in _frame.items():
                result.setdefault(_k, []).append(_v)
        assert result["result"] == [22, 44, 66]


# ============================================================
# Test 4: String Processing
# ============================================================


class TestStringProcessing:
    """Test iteration with string data."""

    @pytest.mark.asyncio
    async def test_format_greetings(self):
        """Test formatting greeting strings."""

        @op
        def each_name(names: list):
            for name in names:
                yield {"name": name}

        @op
        def get_greeting():
            return {"greeting": "Hello"}

        @op
        def format_greeting(name: str, greeting: str):
            return {"message": f"{greeting}, {name}!"}

        with GraphOp(name="greeting_loop") as g:
            greet = get_greeting()
            src = each_name(names=PARENT["names"])
            fmt = format_greeting(name=src["name"], greeting=greet["greeting"])
            START >> [greet, src]
            [greet, src] >> fmt >> END

        g.build()
        schema = StateSchema(g)
        state = schema.create_state(inputs={"names": ["Alice", "Bob", "Charlie"]})

        result = {}
        async for _, _frame in g.run(state):
            for _k, _v in _frame.items():
                result.setdefault(_k, []).append(_v)
        expected = ["Hello, Alice!", "Hello, Bob!", "Hello, Charlie!"]
        assert result["message"] == expected


# ============================================================
# Test 5: Chain of Nodes in Loop
# ============================================================


class TestChainOfNodes:
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


# ============================================================
# Test 6: Concurrency with Async Downstream
# ============================================================


class TestConcurrency:
    """Test that streaming scheduler runs downstream ops concurrently."""

    @pytest.mark.asyncio
    async def test_concurrent_processing(self):
        """Test concurrent execution of downstream ops."""

        @op
        def each_item(items: list):
            for item in items:
                yield {"value": item}

        @op
        async def slow_process(value: int):
            await asyncio.sleep(0.01)
            return {"result": value * 10}

        with GraphOp(name="concurrent_loop") as g:
            src = each_item(items=PARENT["items"])
            proc = slow_process(value=src["value"])
            START >> src >> proc >> END

        g.build()
        schema = StateSchema(g)
        state = schema.create_state(inputs={"items": [1, 2, 3, 4, 5]})

        result = {}
        async for _, _frame in g.run(state):
            for _k, _v in _frame.items():
                result.setdefault(_k, []).append(_v)
        assert result["result"] == [10, 20, 30, 40, 50]


# ============================================================
# Test 7: Ref from Previous Node
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
# Test 8: Empty Iteration
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
# Test 9: Ref with Nested Key Access
# ============================================================


class TestRefWithNestedOperations:
    """Test Ref with nested key access."""

    @pytest.mark.asyncio
    async def test_nested_ref_access(self):
        """Test generator with nested Ref like node['a']['b']."""

        @op
        def get_complex_data():
            return {"dataset": {"items": [10, 20, 30], "multiplier": 2}}

        @op
        def each_item(items: list):
            for item in items:
                yield {"item": item}

        @op
        def process_with_factor(item: int, factor: int):
            return {"result": item * factor}

        with GraphOp(name="ref_ops_graph") as graph:
            data_node = get_complex_data()
            src = each_item(items=data_node["dataset"]["items"])
            proc = process_with_factor(item=src["item"], factor=data_node["dataset"]["multiplier"])
            START >> data_node >> src >> proc >> END

        graph.build()
        schema = StateSchema(graph)
        state = schema.create_state()

        result = {}
        async for _, _frame in graph.run(state):
            for _k, _v in _frame.items():
                result.setdefault(_k, []).append(_v)
        assert result["result"] == [20, 40, 60]


# ============================================================
# Test 10: PARENT Broadcast Inside GraphOp
# ============================================================


class TestParentBroadcast:
    """Test PARENT reference as broadcast inside GraphOp."""

    @pytest.mark.asyncio
    async def test_parent_broadcast(self):
        """Test generator with PARENT broadcast inside GraphOp."""

        @op
        def get_items():
            return {"items": [1, 2, 3, 4, 5]}

        @op
        def each_item(items: list):
            for item in items:
                yield {"item": item}

        @op
        def multiply_item(item: int, multiplier: int):
            return {"result": item * multiplier}

        with GraphOp(name="parent_broadcast_graph") as graph:
            items_node = get_items()
            src = each_item(items=items_node["items"])
            proc = multiply_item(item=src["item"], multiplier=PARENT["multiplier"])
            START >> items_node >> src >> proc >> END

        graph.build()
        schema = StateSchema(graph)
        state = schema.create_state(inputs={"multiplier": 10})

        result = {}
        async for _, _frame in graph.run(state):
            for _k, _v in _frame.items():
                result.setdefault(_k, []).append(_v)
        assert result["result"] == [10, 20, 30, 40, 50]


# ============================================================
# Test 11: Nested Iteration
# ============================================================


class TestNestedIteration:
    """Test nested iteration using nested generators."""

    @pytest.mark.asyncio
    async def test_nested_generators(self):
        """Test nested generator ops for nested iteration."""

        @op
        def outer_iter(items: list):
            for item in items:
                yield {"item": item}

        @op
        def inner_halve(value: int):
            """Halve until < 5, yield each step."""
            while value >= 5:
                value = value // 2
                yield {"final_value": value}

        with GraphOp(name="outer_loop") as g:
            src = outer_iter(items=PARENT["items"])
            inner = inner_halve(value=src["item"])
            START >> src >> inner >> END

        g.build()
        schema = StateSchema(g)
        state = schema.create_state(inputs={"items": [10, 20, 30]})

        result = {}
        async for _, _frame in g.run(state):
            for _k, _v in _frame.items():
                result.setdefault(_k, []).append(_v)
        # 10 -> 5 -> 2 (yields 5, 2)
        # 20 -> 10 -> 5 -> 2 (yields 10, 5, 2)
        # 30 -> 15 -> 7 -> 3 (yields 15, 7, 3)
        assert result["final_value"] == [5, 2, 10, 5, 2, 15, 7, 3]


# ============================================================
# Test 12: Dict Items in Stream
# ============================================================


class TestDictItemsInStream:
    """Test streaming dict items."""

    @pytest.mark.asyncio
    async def test_process_user_dicts(self):
        """Test processing user dictionaries via generator."""

        @op
        def each_user(users: list):
            for user in users:
                yield {"user": user}

        @op
        def grade_user(user: dict):
            grade = "A" if user["score"] >= 90 else "B" if user["score"] >= 80 else "C"
            return {"name": user["name"], "grade": grade}

        with GraphOp(name="grade_loop") as g:
            src = each_user(users=PARENT["users"])
            grader = grade_user(user=src["user"])
            START >> src >> grader >> END

        g.build()
        schema = StateSchema(g)
        state = schema.create_state(
            inputs={
                "users": [
                    {"name": "Alice", "score": 85},
                    {"name": "Bob", "score": 92},
                    {"name": "Charlie", "score": 78},
                ]
            }
        )

        result = {}
        async for _, _frame in g.run(state):
            for _k, _v in _frame.items():
                result.setdefault(_k, []).append(_v)
        assert result["name"] == ["Alice", "Bob", "Charlie"]
        assert result["grade"] == ["B", "A", "C"]


# ============================================================
# Test 13: Async Generator
# ============================================================


class TestAsyncGenerator:
    """Test async generator op."""

    @pytest.mark.asyncio
    async def test_async_generator(self):
        """Test async generator yields items with async iteration."""

        @op
        async def async_source(items: list):
            for item in items:
                await asyncio.sleep(0.01)
                yield {"value": item}

        @op
        def double(value: int):
            return {"result": value * 2}

        with GraphOp(name="async_gen_loop") as g:
            src = async_source(items=PARENT["items"])
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
