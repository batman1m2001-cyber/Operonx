"""Tests for MapNode - parallel iteration node."""

import pytest
import asyncio
from hush.core.nodes.iteration.map_node import MapNode, map_
from hush.core.nodes.iteration.base import Each
from hush.core.nodes.transform.code_node import code_node
from hush.core.nodes.graph.graph_node import GraphNode
from hush.core.nodes.base import START, END, PARENT
from hush.core.states import StateSchema, MemoryState


# ============================================================
# Test 1: Simple Iteration with Each()
# ============================================================

class TestSimpleIteration:
    """Test basic iteration with Each() wrapper."""

    @pytest.mark.asyncio
    async def test_double_values(self):
        """Test simple doubling of values."""
        @code_node
        def double(value: int):
            return {"result": value * 2}

        with MapNode(
            name="double_loop",
            inputs={"value": Each([1, 2, 3, 4, 5])}
        ) as loop:
            node = double(inputs={"value": PARENT["value"]}, outputs={"*": PARENT})
            START >> node >> END

        loop.build()
        schema = StateSchema(loop)
        state = MemoryState(schema)

        result = await loop.run(state)
        assert result['result'] == [2, 4, 6, 8, 10]


# ============================================================
# Test 2: Iteration with Broadcast
# ============================================================

class TestBroadcastIteration:
    """Test iteration with broadcast values."""

    @pytest.mark.asyncio
    async def test_multiply_with_broadcast(self):
        """Test multiplication with broadcast multiplier."""
        @code_node
        def multiply(value: int, multiplier: int):
            return {"result": value * multiplier}

        with MapNode(
            name="multiply_loop",
            inputs={
                "value": Each([1, 2, 3]),
                "multiplier": 10  # broadcast
            }
        ) as loop:
            node = multiply(
                inputs={"value": PARENT["value"], "multiplier": PARENT["multiplier"]},
                outputs={"*": PARENT}
            )
            START >> node >> END

        loop.build()
        schema = StateSchema(loop)
        state = MemoryState(schema)

        result = await loop.run(state)
        assert result['result'] == [10, 20, 30]


# ============================================================
# Test 3: Multiple Each() Variables (Zip)
# ============================================================

class TestMultipleEachVariables:
    """Test iteration with multiple Each() variables (zipped)."""

    @pytest.mark.asyncio
    async def test_zip_two_lists(self):
        """Test zipping two lists together."""
        @code_node
        def add(x: int, y: int):
            return {"sum": x + y}

        with MapNode(
            name="add_loop",
            inputs={
                "x": Each([1, 2, 3]),
                "y": Each([10, 20, 30])
            }
        ) as loop:
            node = add(inputs={"x": PARENT["x"], "y": PARENT["y"]}, outputs={"*": PARENT})
            START >> node >> END

        loop.build()
        schema = StateSchema(loop)
        state = MemoryState(schema)

        result = await loop.run(state)
        assert result['sum'] == [11, 22, 33]

    @pytest.mark.asyncio
    async def test_multiple_each_with_broadcast(self):
        """Test multiple Each() variables plus broadcast."""
        @code_node
        def compute(x: int, y: int, factor: int):
            return {"result": (x + y) * factor}

        with MapNode(
            name="compute_loop",
            inputs={
                "x": Each([1, 2, 3]),
                "y": Each([10, 20, 30]),
                "factor": 2  # broadcast
            }
        ) as loop:
            node = compute(
                inputs={"x": PARENT["x"], "y": PARENT["y"], "factor": PARENT["factor"]},
                outputs={"*": PARENT}
            )
            START >> node >> END

        loop.build()
        schema = StateSchema(loop)
        state = MemoryState(schema)

        result = await loop.run(state)
        assert result['result'] == [22, 44, 66]  # (1+10)*2, (2+20)*2, (3+30)*2


# ============================================================
# Test 4: String Processing
# ============================================================

class TestStringProcessing:
    """Test iteration with string data."""

    @pytest.mark.asyncio
    async def test_format_greetings(self):
        """Test formatting greeting strings."""
        @code_node
        def format_greeting(name: str, greeting: str):
            return {"message": f"{greeting}, {name}!"}

        with MapNode(
            name="greeting_loop",
            inputs={
                "name": Each(["Alice", "Bob", "Charlie"]),
                "greeting": "Hello"  # broadcast
            }
        ) as loop:
            node = format_greeting(
                inputs={"name": PARENT["name"], "greeting": PARENT["greeting"]},
                outputs={"*": PARENT}
            )
            START >> node >> END

        loop.build()
        schema = StateSchema(loop)
        state = MemoryState(schema)

        result = await loop.run(state)
        expected = ["Hello, Alice!", "Hello, Bob!", "Hello, Charlie!"]
        assert result['message'] == expected


# ============================================================
# Test 5: Chain of Nodes in Loop
# ============================================================

class TestChainOfNodes:
    """Test multiple nodes chained inside a loop."""

    @pytest.mark.asyncio
    async def test_add_then_multiply(self):
        """Test chain: add_one -> multiply_two."""
        @code_node
        def add_one(x: int):
            return {"y": x + 1}

        @code_node
        def multiply_two(y: int):
            return {"z": y * 2}

        with MapNode(
            name="chain_loop",
            inputs={"x": Each([1, 2, 3])}
        ) as loop:
            n1 = add_one(inputs={"x": PARENT["x"]})
            n2 = multiply_two(inputs={"y": n1["y"]}, outputs={"*": PARENT})
            START >> n1 >> n2 >> END

        loop.build()
        schema = StateSchema(loop)
        state = MemoryState(schema)

        result = await loop.run(state)
        assert result['z'] == [4, 6, 8]  # (1+1)*2, (2+1)*2, (3+1)*2


# ============================================================
# Test 6: Concurrency Limit
# ============================================================

class TestConcurrencyLimit:
    """Test max_concurrency parameter."""

    @pytest.mark.asyncio
    async def test_limited_concurrency(self):
        """Test with max_concurrency=2."""
        @code_node
        async def slow_process(value: int):
            await asyncio.sleep(0.01)
            return {"result": value * 10}

        with MapNode(
            name="concurrent_loop",
            inputs={"value": Each([1, 2, 3, 4, 5])},
            max_concurrency=2
        ) as loop:
            node = slow_process(inputs={"value": PARENT["value"]}, outputs={"*": PARENT})
            START >> node >> END

        loop.build()
        schema = StateSchema(loop)
        state = MemoryState(schema)

        result = await loop.run(state)
        assert result['result'] == [10, 20, 30, 40, 50]


# ============================================================
# Test 7: Ref from Previous Node
# ============================================================

class TestRefFromPreviousNode:
    """Test iteration using Ref to data from previous node."""

    @pytest.mark.asyncio
    async def test_each_from_ref(self):
        """Test Each() with Ref to another node's output."""
        @code_node
        def generate_data():
            return {"numbers": [10, 20, 30], "factor": 5}

        @code_node
        def process_item(item: int, factor: int):
            return {"result": item * factor}

        with GraphNode(name="ref_test_graph") as graph:
            gen_node = generate_data()

            with MapNode(
                name="ref_loop",
                inputs={
                    "item": Each(gen_node["numbers"]),
                    "factor": gen_node["factor"]  # broadcast Ref
                },
                outputs={"*": PARENT}
            ) as loop:
                proc_node = process_item(
                    inputs={"item": PARENT["item"], "factor": PARENT["factor"]},
                    outputs={"*": PARENT}
                )
                START >> proc_node >> END

            START >> gen_node >> loop >> END

        graph.build()
        schema = StateSchema(graph)
        state = MemoryState(schema)

        result = await graph.run(state)
        assert result["result"] == [50, 100, 150]  # 10*5, 20*5, 30*5


# ============================================================
# Test 8: Empty Iteration
# ============================================================

class TestEmptyIteration:
    """Test behavior with empty iteration data."""

    @pytest.mark.asyncio
    async def test_empty_list(self):
        """Test iteration over empty list."""
        @code_node
        def double(value: int):
            return {"result": value * 2}

        with MapNode(
            name="empty_loop",
            inputs={"value": Each([])}
        ) as loop:
            node = double(inputs={"value": PARENT["value"]}, outputs={"*": PARENT})
            START >> node >> END

        loop.build()
        schema = StateSchema(loop)
        state = MemoryState(schema)

        result = await loop.run(state)
        assert result['result'] == []
        assert result['iteration_metrics']['total_iterations'] == 0


# ============================================================
# Test 9: Mismatched Lengths
# ============================================================

class TestMismatchedLengths:
    """Test error handling for mismatched list lengths."""

    @pytest.mark.asyncio
    async def test_raises_on_length_mismatch(self):
        """Test that mismatched Each() lengths are captured as error."""
        @code_node
        def dummy(x: int, y: int):
            return {"result": x + y}

        with MapNode(
            name="mismatch_loop",
            inputs={
                "x": Each([1, 2, 3]),
                "y": Each([10, 20])  # Different length!
            }
        ) as loop:
            node = dummy(inputs={"x": PARENT["x"], "y": PARENT["y"]}, outputs={"*": PARENT})
            START >> node >> END

        loop.build()
        schema = StateSchema(loop)
        state = MemoryState(schema)

        await loop.run(state)
        # Error is captured in state, not raised
        error = state["mismatch_loop", "error", None]
        assert error is not None
        assert "same length" in error


# ============================================================
# Test 10: Ref with Nested Operations
# ============================================================

class TestRefWithNestedOperations:
    """Test Ref with nested key access."""

    @pytest.mark.asyncio
    async def test_nested_ref_access(self):
        """Test Each() with nested Ref like node['a']['b']."""
        @code_node
        def get_complex_data():
            return {
                "dataset": {
                    "items": [10, 20, 30],
                    "multiplier": 2
                }
            }

        @code_node
        def process_with_factor(item: int, factor: int):
            return {"result": item * factor}

        with GraphNode(name="ref_ops_graph") as graph:
            data_node = get_complex_data()

            with MapNode(
                name="ref_ops_loop",
                inputs={
                    "item": Each(data_node["dataset"]["items"]),
                    "factor": data_node["dataset"]["multiplier"]
                },
                outputs={"*": PARENT}
            ) as loop:
                proc = process_with_factor(
                    inputs={"item": PARENT["item"], "factor": PARENT["factor"]},
                    outputs={"*": PARENT}
                )
                START >> proc >> END

            START >> data_node >> loop >> END

        graph.build()
        schema = StateSchema(graph)
        state = MemoryState(schema)

        result = await graph.run(state)
        assert result["result"] == [20, 40, 60]  # [10*2, 20*2, 30*2]


# ============================================================
# Test 11: PARENT Broadcast Inside GraphNode
# ============================================================

class TestParentBroadcast:
    """Test PARENT reference as broadcast inside GraphNode."""

    @pytest.mark.asyncio
    async def test_parent_broadcast(self):
        """Test MapNode with PARENT broadcast inside GraphNode."""
        @code_node
        def get_items():
            return {"items": [1, 2, 3, 4, 5]}

        @code_node
        def multiply_item(item: int, multiplier: int):
            return {"result": item * multiplier}

        with GraphNode(name="parent_broadcast_graph") as graph:
            items_node = get_items()

            with MapNode(
                name="multiply_loop",
                inputs={
                    "item": Each(items_node["items"]),
                    "multiplier": PARENT["multiplier"]  # PARENT = graph
                },
                outputs={"*": PARENT}
            ) as loop:
                proc = multiply_item(
                    inputs={"item": PARENT["item"], "multiplier": PARENT["multiplier"]},
                    outputs={"*": PARENT}
                )
                START >> proc >> END

            START >> items_node >> loop >> END

        graph.build()
        schema = StateSchema(graph)
        state = MemoryState(schema, inputs={"multiplier": 10})

        result = await graph.run(state)
        assert result["result"] == [10, 20, 30, 40, 50]


# ============================================================
# Test 12: Iteration Metrics
# ============================================================

class TestIterationMetrics:
    """Test iteration metrics are collected."""

    @pytest.mark.asyncio
    async def test_metrics_collected(self):
        """Test that iteration metrics are returned."""
        @code_node
        def double(value: int):
            return {"result": value * 2}

        with MapNode(
            name="metrics_loop",
            inputs={"value": Each([1, 2, 3])}
        ) as loop:
            node = double(inputs={"value": PARENT["value"]}, outputs={"*": PARENT})
            START >> node >> END

        loop.build()
        schema = StateSchema(loop)
        state = MemoryState(schema)

        result = await loop.run(state)

        assert "iteration_metrics" in result
        metrics = result["iteration_metrics"]
        assert metrics["total_iterations"] == 3
        assert metrics["success_count"] == 3
        assert metrics["error_count"] == 0


# ============================================================
# Test 13: Nested Iteration (MapNode + WhileLoop)
# ============================================================

class TestNestedIteration:
    """Test nested iteration patterns.

    Note: Nested MapNode-in-MapNode with outer loop variables passed to inner
    loop is NOT currently supported due to parallel execution - all outer
    iterations run concurrently and share state, causing variable conflicts.

    The supported pattern is MapNode with WhileLoop inside, where WhileLoop
    runs sequentially within each MapNode iteration.
    """

    @pytest.mark.asyncio
    async def test_mapnode_with_whileloop_inside(self):
        """Test MapNode containing WhileLoop - the supported nested pattern."""
        from hush.core.nodes.iteration.while_loop_node import WhileLoopNode

        @code_node
        def halve(value: int):
            return {"new_value": value // 2}

        with MapNode(
            name="outer_map",
            inputs={"item": Each([10, 20, 30])}
        ) as map_node:
            with WhileLoopNode(
                name="inner_while",
                inputs={"value": PARENT["item"]},
                stop_condition="value < 5",
                max_iterations=10
            ) as while_loop:
                node = halve(inputs={"value": PARENT["value"]})
                node["new_value"] >> PARENT["value"]
                START >> node >> END

            while_loop["value"] >> PARENT["final_value"]
            START >> while_loop >> END

        map_node.build()
        schema = StateSchema(map_node)
        state = MemoryState(schema)

        result = await map_node.run(state)

        # 10 -> 5 -> 2 (stops at 2)
        # 20 -> 10 -> 5 -> 2 (stops at 2)
        # 30 -> 15 -> 7 -> 3 (stops at 3)
        assert result["final_value"] == [2, 2, 3]

    @pytest.mark.asyncio
    async def test_mapnode_independent_inner_loop(self):
        """Test MapNode with inner MapNode that doesn't depend on outer vars."""
        @code_node
        def sum_list(items: list):
            return {"total": sum(items)}

        with MapNode(
            name="outer_map",
            inputs={"multiplier": Each([1, 2, 3])}
        ) as outer:
            # Inner map iterates over a fixed list, not dependent on outer
            with MapNode(
                name="inner_map",
                inputs={"value": Each([10, 20, 30])}
            ) as inner:
                @code_node
                def double(value: int):
                    return {"result": value * 2}

                node = double(inputs={"value": PARENT["value"]})
                node["result"] >> PARENT["result"]
                START >> node >> END

            # Sum the inner results
            sum_node = sum_list(inputs={"items": inner["result"]})
            sum_node["total"] >> PARENT["total"]
            START >> inner >> sum_node >> END

        outer.build()
        schema = StateSchema(outer)
        state = MemoryState(schema)

        result = await outer.run(state)

        # Inner map always produces [20, 40, 60], sum = 120
        # This runs 3 times (once per outer iteration)
        assert result["total"] == [120, 120, 120]


# ============================================================
# Test: map_() Shorthand
# ============================================================

class TestMapShorthand:
    """Test map_() shorthand function."""

    @pytest.mark.asyncio
    async def test_map_shorthand_basic(self):
        """Test basic map_() with Each()."""
        @code_node
        def double(value: int):
            return {"result": value * 2}

        with map_(value=Each([1, 2, 3])) as loop:
            node = double(inputs={"value": PARENT["value"]}, outputs={"*": PARENT})
            START >> node >> END

        loop.build()
        schema = StateSchema(loop)
        state = MemoryState(schema)

        result = await loop.run(state)
        assert sorted(result["result"]) == [2, 4, 6]

    @pytest.mark.asyncio
    async def test_map_shorthand_concurrency(self):
        """Test map_() with max_concurrency."""
        @code_node
        def double(value: int):
            return {"result": value * 2}

        with map_(value=Each([1, 2, 3, 4]), max_concurrency=2) as loop:
            node = double(inputs={"value": PARENT["value"]}, outputs={"*": PARENT})
            START >> node >> END

        loop.build()
        schema = StateSchema(loop)
        state = MemoryState(schema)

        result = await loop.run(state)
        assert sorted(result["result"]) == [2, 4, 6, 8]

    def test_map_shorthand_auto_name(self):
        """Test that map_() auto-names from variable assignment."""
        my_map = map_(value=Each([1, 2, 3]))
        assert my_map.name == "my_map"

    def test_map_shorthand_is_mapnode(self):
        """Test that map_() returns a MapNode instance."""
        loop = map_(value=Each([1, 2, 3]))
        assert isinstance(loop, MapNode)
