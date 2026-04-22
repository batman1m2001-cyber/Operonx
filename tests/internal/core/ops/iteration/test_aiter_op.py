"""Tests for async streaming iteration patterns using generator ops.

These tests replace the old AIterOp tests. Async generator ops + the streaming
scheduler in GraphOp now handle all async streaming patterns.
"""

import asyncio

import pytest

from operon.core import END, PARENT, START, GraphOp, op
from operon.core.states import StateSchema

# ============================================================
# Test 1: Simple Async Streaming
# ============================================================


class TestSimpleStreaming:
    """Test basic async generator iteration."""

    @pytest.mark.asyncio
    async def test_double_values(self):
        """Test simple doubling of async-streamed values."""

        @op
        async def async_source(count: int):
            for i in range(count):
                await asyncio.sleep(0.01)
                yield {"value": i}

        @op
        def double_value(value: int):
            return {"result": value * 2}

        with GraphOp(name="double_stream") as g:
            src = async_source(count=PARENT["count"])
            d = double_value(value=src["value"])
            START >> src >> d >> END

        g.build()
        schema = StateSchema(g)
        state = schema.create_state(inputs={"count": 10})

        result = {}
        async for _, _frame in g.run(state):
            for _k, _v in _frame.items():
                result.setdefault(_k, []).append(_v)
        assert result["result"] == [0, 2, 4, 6, 8, 10, 12, 14, 16, 18]


# ============================================================
# Test 2: Streaming with Broadcast
# ============================================================


class TestStreamingWithBroadcast:
    """Test async generator iteration with broadcast values."""

    @pytest.mark.asyncio
    async def test_multiply_with_broadcast(self):
        """Test multiplication with broadcast multiplier."""

        @op
        async def number_source(count: int):
            for i in range(count):
                await asyncio.sleep(0.01)
                yield {"value": i + 1}

        @op
        def get_multiplier():
            return {"multiplier": 10}

        @op
        def multiply(value: int, multiplier: int):
            return {"result": value * multiplier}

        with GraphOp(name="multiply_stream") as g:
            cfg = get_multiplier()
            src = number_source(count=PARENT["count"])
            m = multiply(value=src["value"], multiplier=cfg["multiplier"])
            START >> [cfg, src]
            [cfg, src] >> m >> END

        g.build()
        schema = StateSchema(g)
        state = schema.create_state(inputs={"count": 5})

        result = {}
        async for _, _frame in g.run(state):
            for _k, _v in _frame.items():
                result.setdefault(_k, []).append(_v)
        assert result["result"] == [10, 20, 30, 40, 50]


# ============================================================
# Test 3: Streaming with Ref from Upstream
# ============================================================


class TestStreamingWithRef:
    """Test async generator with Ref from upstream node."""

    @pytest.mark.asyncio
    async def test_dynamic_config(self):
        """Test streaming with dynamic config from upstream node."""

        @op
        def get_config():
            return {"factor": 5, "offset": 100}

        @op
        async def ref_source(count: int):
            for i in range(count):
                await asyncio.sleep(0.01)
                yield {"value": i + 1}

        @op
        def compute(value: int, factor: int, offset: int):
            return {"result": value * factor + offset}

        with GraphOp(name="ref_test") as graph:
            config_node = get_config()
            src = ref_source(count=PARENT["count"])
            proc = compute(
                value=src["value"],
                factor=config_node["factor"],
                offset=config_node["offset"],
            )
            START >> [config_node, src]
            [config_node, src] >> proc >> END

        graph.build()
        schema = StateSchema(graph)
        state = schema.create_state(inputs={"count": 4})

        result = {}
        async for _, _frame in graph.run(state):
            for _k, _v in _frame.items():
                result.setdefault(_k, []).append(_v)
        # [1*5+100, 2*5+100, 3*5+100, 4*5+100] = [105, 110, 115, 120]
        assert result["result"] == [105, 110, 115, 120]


# ============================================================
# Test 4: Dict Items in Stream
# ============================================================


class TestDictItemsInStream:
    """Test streaming dict items via async generator."""

    @pytest.mark.asyncio
    async def test_process_user_dicts(self):
        """Test streaming and processing user dictionaries."""

        @op
        async def dict_source(users: list):
            for user in users:
                await asyncio.sleep(0.01)
                yield {"user": user}

        @op
        def grade_user(user: dict):
            grade = "A" if user["score"] >= 90 else "B" if user["score"] >= 80 else "C"
            return {"name": user["name"], "grade": grade}

        with GraphOp(name="grade_stream") as g:
            src = dict_source(users=PARENT["users"])
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
# Test 5: Concurrency with Async Downstream
# ============================================================


class TestConcurrencyLimit:
    """Test concurrent execution with async generator."""

    @pytest.mark.asyncio
    async def test_concurrent_processing(self):
        """Test streaming with concurrent downstream processing."""

        @op
        async def slow_source(count: int):
            for i in range(count):
                await asyncio.sleep(0.01)
                yield {"value": i}

        @op
        async def slow_process(value: int):
            await asyncio.sleep(0.05)
            return {"result": value * 10}

        with GraphOp(name="concurrent_stream") as g:
            src = slow_source(count=PARENT["count"])
            proc = slow_process(value=src["value"])
            START >> src >> proc >> END

        g.build()
        schema = StateSchema(g)
        state = schema.create_state(inputs={"count": 6})

        result = {}
        async for _, _frame in g.run(state):
            for _k, _v in _frame.items():
                result.setdefault(_k, []).append(_v)
        assert result["result"] == [0, 10, 20, 30, 40, 50]


# ============================================================
# Test 6: Stream from Upstream Node Output
# ============================================================


class TestStreamFromUpstreamNode:
    """Test async generator receiving data from upstream node."""

    @pytest.mark.asyncio
    async def test_dynamic_stream_source(self):
        """Test stream source data created by upstream node."""

        @op
        def create_items(begin: int, end: int):
            return {"items": [{"id": i, "value": i * 10} for i in range(begin, end)]}

        @op
        async def stream_items(items: list):
            for item in items:
                await asyncio.sleep(0.01)
                yield {"item": item}

        @op
        def process_item(item: dict, prefix: str):
            return {"processed_id": f"{prefix}_{item['id']}", "doubled_value": item["value"] * 2}

        with GraphOp(name="dynamic_stream_graph") as graph:
            source_creator = create_items(begin=PARENT["begin"], end=PARENT["end"])
            src = stream_items(items=source_creator["items"])
            proc = process_item(item=src["item"], prefix=PARENT["prefix"])
            START >> source_creator >> src >> proc >> END

        graph.build()
        schema = StateSchema(graph)
        state = schema.create_state(inputs={"begin": 0, "end": 5, "prefix": "MSG"})

        result = {}
        async for _, _frame in graph.run(state):
            for _k, _v in _frame.items():
                result.setdefault(_k, []).append(_v)
        assert result["processed_id"] == ["MSG_0", "MSG_1", "MSG_2", "MSG_3", "MSG_4"]
        assert result["doubled_value"] == [0, 20, 40, 60, 80]


# ============================================================
# Test 7: Empty Async Stream
# ============================================================


class TestEmptyStream:
    """Test empty async generator."""

    @pytest.mark.asyncio
    async def test_empty_async_stream(self):
        """Test async generator that yields nothing."""

        @op
        async def empty_source(items: list):
            for item in items:
                await asyncio.sleep(0.01)
                yield {"value": item}

        @op
        def double(value: int):
            return {"result": value * 2}

        with GraphOp(name="empty_stream") as g:
            src = empty_source(items=PARENT["items"])
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
# Test 8: Chain After Async Generator
# ============================================================


class TestChainAfterAsyncGenerator:
    """Test chain of ops after async generator."""

    @pytest.mark.asyncio
    async def test_async_gen_chain(self):
        """Test async generator >> add >> multiply >> END."""

        @op
        async def async_source(items: list):
            for item in items:
                await asyncio.sleep(0.01)
                yield {"x": item}

        @op
        def add_one(x: int):
            return {"y": x + 1}

        @op
        def multiply_two(y: int):
            return {"z": y * 2}

        with GraphOp(name="async_chain") as g:
            src = async_source(items=PARENT["items"])
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
