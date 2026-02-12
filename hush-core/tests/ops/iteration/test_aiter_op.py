"""Tests for AIterOp - async streaming iteration node."""

import asyncio

import pytest

from hush.core.ops.base import END, PARENT, START
from hush.core.ops.graph.graph_op import GraphOp
from hush.core.ops.iteration.aiter_op import AIterOp, batch_by_size
from hush.core.ops.iteration.base import Each
from hush.core.ops.transform.func_op import op
from hush.core.states import MemoryState, StateSchema

# ============================================================
# Test 1: Simple Streaming
# ============================================================


class TestSimpleStreaming:
    """Test basic async iteration."""

    @pytest.mark.asyncio
    async def test_double_values(self):
        """Test simple doubling of streamed values."""

        async def simple_source():
            for i in range(10):
                yield i
                await asyncio.sleep(0.01)

        results = []

        async def collect_results(data):
            results.append(data)

        @op
        def double_value(value: int):
            return {"result": value * 2}

        with AIterOp(
            name="double_stream", inputs={"value": Each(simple_source())}, callback=collect_results
        ) as stream_node:
            processor = double_value(inputs={"value": PARENT["value"]}, outputs={"*": PARENT})
            START >> processor >> END

        stream_node.build()
        schema = StateSchema(stream_node)
        state = MemoryState(schema)

        output = await stream_node.run(state)

        assert output["result"] == [0, 2, 4, 6, 8, 10, 12, 14, 16, 18]
        assert len(results) == 10


# ============================================================
# Test 2: Streaming with Broadcast
# ============================================================


class TestStreamingWithBroadcast:
    """Test async iteration with broadcast values."""

    @pytest.mark.asyncio
    async def test_multiply_with_broadcast(self):
        """Test multiplication with broadcast multiplier."""

        async def number_source():
            for i in range(5):
                yield i + 1
                await asyncio.sleep(0.01)

        @op
        def multiply(value: int, multiplier: int):
            return {"result": value * multiplier}

        with AIterOp(
            name="multiply_stream",
            inputs={
                "value": Each(number_source()),
                "multiplier": 10,  # broadcast
            },
        ) as stream_node:
            processor = multiply(
                inputs={"value": PARENT["value"], "multiplier": PARENT["multiplier"]},
                outputs={"*": PARENT},
            )
            START >> processor >> END

        stream_node.build()
        schema = StateSchema(stream_node)
        state = MemoryState(schema)

        output = await stream_node.run(state)

        assert output["result"] == [10, 20, 30, 40, 50]


# ============================================================
# Test 3: Streaming with Batching
# ============================================================


class TestStreamingWithBatching:
    """Test async iteration with batching."""

    @pytest.mark.asyncio
    async def test_batch_processing(self):
        """Test batching items before processing."""

        async def batch_source():
            for i in range(12):
                yield i
                await asyncio.sleep(0.005)

        @op
        def sum_batch(batch: list, batch_size: int):
            return {"total": sum(batch), "size": batch_size}

        with AIterOp(
            name="batch_stream", inputs={"item": Each(batch_source())}, batch_fn=batch_by_size(4)
        ) as stream_node:
            processor = sum_batch(
                inputs={"batch": PARENT["batch"], "batch_size": PARENT["batch_size"]},
                outputs={"*": PARENT},
            )
            START >> processor >> END

        stream_node.build()
        schema = StateSchema(stream_node)
        state = MemoryState(schema)

        output = await stream_node.run(state)

        # [0+1+2+3, 4+5+6+7, 8+9+10+11] = [6, 22, 38]
        assert output["total"] == [6, 22, 38]
        assert output["size"] == [4, 4, 4]


# ============================================================
# Test 4: Streaming with Ref from Upstream
# ============================================================


class TestStreamingWithRef:
    """Test async iteration with Ref from upstream node."""

    @pytest.mark.asyncio
    async def test_dynamic_config(self):
        """Test streaming with dynamic config from upstream node."""

        @op
        def get_config():
            return {"factor": 5, "offset": 100}

        async def ref_source():
            for i in range(4):
                yield i + 1
                await asyncio.sleep(0.01)

        @op
        def compute(value: int, factor: int, offset: int):
            return {"result": value * factor + offset}

        with GraphOp(name="ref_test") as graph:
            config_node = get_config()

            with AIterOp(
                name="compute_stream",
                inputs={
                    "value": Each(ref_source()),
                    "factor": config_node["factor"],  # broadcast Ref
                    "offset": config_node["offset"],  # broadcast Ref
                },
                outputs={"*": PARENT},
            ) as stream_node:
                processor = compute(
                    inputs={
                        "value": PARENT["value"],
                        "factor": PARENT["factor"],
                        "offset": PARENT["offset"],
                    },
                    outputs={"*": PARENT},
                )
                START >> processor >> END

            START >> config_node >> stream_node >> END

        graph.build()
        schema = StateSchema(graph)
        state = MemoryState(schema)

        output = await graph.run(state)

        # [1*5+100, 2*5+100, 3*5+100, 4*5+100] = [105, 110, 115, 120]
        assert output["result"] == [105, 110, 115, 120]


# ============================================================
# Test 5: Dict Items in Stream
# ============================================================


class TestDictItemsInStream:
    """Test streaming dict items."""

    @pytest.mark.asyncio
    async def test_process_user_dicts(self):
        """Test streaming and processing user dictionaries."""

        async def dict_source():
            users = [
                {"name": "Alice", "score": 85},
                {"name": "Bob", "score": 92},
                {"name": "Charlie", "score": 78},
            ]
            for user in users:
                yield user
                await asyncio.sleep(0.01)

        @op
        def grade_user(user: dict):
            grade = "A" if user["score"] >= 90 else "B" if user["score"] >= 80 else "C"
            return {"name": user["name"], "grade": grade}

        with AIterOp(name="grade_stream", inputs={"user": Each(dict_source())}) as stream_node:
            processor = grade_user(inputs={"user": PARENT["user"]}, outputs={"*": PARENT})
            START >> processor >> END

        stream_node.build()
        schema = StateSchema(stream_node)
        state = MemoryState(schema)

        output = await stream_node.run(state)

        assert output["name"] == ["Alice", "Bob", "Charlie"]
        assert output["grade"] == ["B", "A", "C"]


# ============================================================
# Test 6: Concurrency Limit
# ============================================================


class TestConcurrencyLimit:
    """Test max_concurrency parameter."""

    @pytest.mark.asyncio
    async def test_limited_concurrency(self):
        """Test streaming with limited concurrency."""

        async def slow_source():
            for i in range(6):
                yield i
                await asyncio.sleep(0.01)

        @op
        async def slow_process(value: int):
            await asyncio.sleep(0.05)
            return {"result": value * 10}

        with AIterOp(
            name="concurrent_stream", inputs={"value": Each(slow_source())}, max_concurrency=2
        ) as stream_node:
            processor = slow_process(inputs={"value": PARENT["value"]}, outputs={"*": PARENT})
            START >> processor >> END

        stream_node.build()
        schema = StateSchema(stream_node)
        state = MemoryState(schema)

        output = await stream_node.run(state)

        assert output["result"] == [0, 10, 20, 30, 40, 50]


# ============================================================
# Test 7: Stream Source from Upstream Node Output
# ============================================================


class TestStreamFromUpstreamNode:
    """Test AIterOp receiving stream source from upstream node."""

    @pytest.mark.asyncio
    async def test_dynamic_stream_source(self):
        """Test stream source created by upstream node."""

        @op
        def create_stream_source(start: int, end: int):
            """Node that produces an async iterable as output."""

            async def generated_stream():
                for i in range(start, end):
                    yield {"id": i, "value": i * 10}
                    await asyncio.sleep(0.01)

            return {"stream": generated_stream(), "metadata": {"count": end - start}}

        @op
        def process_item(item: dict, prefix: str):
            """Process each streamed item."""
            return {"processed_id": f"{prefix}_{item['id']}", "doubled_value": item["value"] * 2}

        with GraphOp(name="dynamic_stream_graph") as graph:
            source_creator = create_stream_source(
                inputs={"start": PARENT["start"], "end": PARENT["end"]}
            )

            with AIterOp(
                name="dynamic_processor",
                inputs={"item": Each(source_creator["stream"]), "prefix": PARENT["prefix"]},
                outputs={"*": PARENT},
            ) as stream_node:
                processor = process_item(
                    inputs={"item": PARENT["item"], "prefix": PARENT["prefix"]},
                    outputs={"*": PARENT},
                )
                START >> processor >> END

            START >> source_creator >> stream_node >> END

        graph.build()
        schema = StateSchema(graph)
        state = MemoryState(schema, inputs={"start": 0, "end": 5, "prefix": "MSG"})

        output = await graph.run(state)

        assert output["processed_id"] == ["MSG_0", "MSG_1", "MSG_2", "MSG_3", "MSG_4"]
        assert output["doubled_value"] == [0, 20, 40, 60, 80]


# ============================================================
# Test 8: Validation
# ============================================================


class TestValidation:
    """Test input validation."""

    def test_requires_exactly_one_each(self):
        """Test that AIterOp requires exactly one Each() source."""

        async def source1():
            yield 1

        async def source2():
            yield 2

        with pytest.raises(ValueError, match="exactly one Each"):
            AIterOp(
                name="invalid_stream",
                inputs={
                    "a": Each(source1()),
                    "b": Each(source2()),  # Two Each() sources - invalid
                },
            )

    def test_requires_at_least_one_each(self):
        """Test that AIterOp requires at least one Each() source."""
        with pytest.raises(ValueError, match="exactly one Each"):
            AIterOp(
                name="invalid_stream",
                inputs={
                    "a": 10,
                    "b": 20,  # No Each() source - invalid
                },
            )


# ============================================================
# Test 9: AIterOp.of() Shorthand
# ============================================================


class TestAiterShorthand:
    """Test AIterOp.of() shorthand classmethod."""

    @pytest.mark.asyncio
    async def test_aiter_shorthand_basic(self):
        """Test basic AIterOp.of() with Each()."""

        async def simple_source():
            for i in range(5):
                yield i
                await asyncio.sleep(0.01)

        @op
        def double_value(value: int):
            return {"result": value * 2}

        with AIterOp.of(value=Each(simple_source())) as stream:
            processor = double_value(inputs={"value": PARENT["value"]}, outputs={"*": PARENT})
            START >> processor >> END

        stream.build()
        schema = StateSchema(stream)
        state = MemoryState(schema)

        result = await stream.run(state)
        assert result["result"] == [0, 2, 4, 6, 8]

    @pytest.mark.asyncio
    async def test_aiter_shorthand_with_broadcast(self):
        """Test AIterOp.of() with Each() and broadcast values."""

        async def number_source():
            for i in range(4):
                yield i + 1
                await asyncio.sleep(0.01)

        @op
        def multiply(value: int, multiplier: int):
            return {"result": value * multiplier}

        with AIterOp.of(value=Each(number_source()), multiplier=10) as stream:
            processor = multiply(
                inputs={"value": PARENT["value"], "multiplier": PARENT["multiplier"]},
                outputs={"*": PARENT},
            )
            START >> processor >> END

        stream.build()
        schema = StateSchema(stream)
        state = MemoryState(schema)

        result = await stream.run(state)
        assert result["result"] == [10, 20, 30, 40]

    @pytest.mark.asyncio
    async def test_aiter_shorthand_with_callback(self):
        """Test AIterOp.of() with callback passthrough."""

        async def simple_source():
            for i in range(3):
                yield i
                await asyncio.sleep(0.01)

        results = []

        async def collect(data):
            results.append(data)

        @op
        def double_value(value: int):
            return {"result": value * 2}

        with AIterOp.of(value=Each(simple_source()), callback=collect) as stream:
            processor = double_value(inputs={"value": PARENT["value"]}, outputs={"*": PARENT})
            START >> processor >> END

        stream.build()
        schema = StateSchema(stream)
        state = MemoryState(schema)

        await stream.run(state)
        assert len(results) == 3

    def test_aiter_shorthand_auto_name(self):
        """Test that AIterOp.of() auto-names from variable assignment."""

        async def src():
            yield 1

        my_stream = AIterOp.of(value=Each(src()))
        assert my_stream.name == "my_stream"

    def test_aiter_shorthand_is_asynciternode(self):
        """Test that AIterOp.of() returns an AIterOp instance."""

        async def src():
            yield 1

        node = AIterOp.of(value=Each(src()))
        assert isinstance(node, AIterOp)
