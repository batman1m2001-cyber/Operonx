"""Tests for ForLoopNode - sequential iteration node."""

import pytest

from hush.core.nodes.base import END, PARENT, START
from hush.core.nodes.graph.graph_node import GraphNode
from hush.core.nodes.iteration.base import Each
from hush.core.nodes.iteration.for_loop_node import ForLoopNode, for_
from hush.core.nodes.iteration.while_loop_node import WhileLoopNode
from hush.core.nodes.transform.code_node import code_node
from hush.core.states import MemoryState, StateSchema

# ============================================================
# Test 1: Simple Sequential Iteration
# ============================================================


class TestSimpleIteration:
    """Test basic sequential iteration with Each() wrapper."""

    @pytest.mark.asyncio
    async def test_double_values(self):
        """Test simple doubling of values sequentially."""

        @code_node
        def double(value: int):
            return {"result": value * 2}

        with ForLoopNode(name="double_loop", inputs={"value": Each([1, 2, 3, 4, 5])}) as loop:
            node = double(inputs={"value": PARENT["value"]}, outputs={"*": PARENT})
            START >> node >> END

        loop.build()
        schema = StateSchema(loop)
        state = MemoryState(schema)

        result = await loop.run(state)
        assert result["result"] == [2, 4, 6, 8, 10]


# ============================================================
# Test 2: Sequential Iteration with Broadcast
# ============================================================


class TestBroadcastIteration:
    """Test sequential iteration with broadcast values."""

    @pytest.mark.asyncio
    async def test_multiply_with_broadcast(self):
        """Test multiplication with broadcast multiplier."""

        @code_node
        def multiply(value: int, multiplier: int):
            return {"result": value * multiplier}

        with ForLoopNode(
            name="multiply_loop",
            inputs={
                "value": Each([1, 2, 3]),
                "multiplier": 10,  # broadcast
            },
        ) as loop:
            node = multiply(
                inputs={"value": PARENT["value"], "multiplier": PARENT["multiplier"]},
                outputs={"*": PARENT},
            )
            START >> node >> END

        loop.build()
        schema = StateSchema(loop)
        state = MemoryState(schema)

        result = await loop.run(state)
        assert result["result"] == [10, 20, 30]


# ============================================================
# Test 3: Multiple Each() Variables (Zip)
# ============================================================


class TestMultipleEachVariables:
    """Test sequential iteration with multiple Each() variables (zipped)."""

    @pytest.mark.asyncio
    async def test_zip_two_lists(self):
        """Test zipping two lists together sequentially."""

        @code_node
        def add(x: int, y: int):
            return {"sum": x + y}

        with ForLoopNode(
            name="add_loop", inputs={"x": Each([1, 2, 3]), "y": Each([10, 20, 30])}
        ) as loop:
            node = add(inputs={"x": PARENT["x"], "y": PARENT["y"]}, outputs={"*": PARENT})
            START >> node >> END

        loop.build()
        schema = StateSchema(loop)
        state = MemoryState(schema)

        result = await loop.run(state)
        assert result["sum"] == [11, 22, 33]


# ============================================================
# Test 4: Nested ForLoopNode (Sequential Nested Loops)
# ============================================================


class TestNestedForLoop:
    """Test nested ForLoopNode - sequential iteration supports nested loops."""

    @pytest.mark.asyncio
    async def test_nested_forloop_with_outer_variable(self):
        """Test nested ForLoopNode where inner loop depends on outer variable.

        This is the key advantage of sequential ForLoopNode over parallel MapNode.
        """

        @code_node
        def multiply(x: int, y: int):
            return {"result": x * y}

        with ForLoopNode(name="outer_loop", inputs={"x": Each([1, 2, 3])}) as outer:
            with ForLoopNode(
                name="inner_loop",
                inputs={
                    "y": Each([10, 20]),
                    "x": PARENT["x"],  # Pass outer variable to inner loop
                },
            ) as inner:
                node = multiply(inputs={"x": PARENT["x"], "y": PARENT["y"]}, outputs={"*": PARENT})
                START >> node >> END

            inner["result"] >> PARENT["results"]
            START >> inner >> END

        outer.build()
        schema = StateSchema(outer)
        state = MemoryState(schema)

        result = await outer.run(state)

        # Outer x=1: inner produces [1*10, 1*20] = [10, 20]
        # Outer x=2: inner produces [2*10, 2*20] = [20, 40]
        # Outer x=3: inner produces [3*10, 3*20] = [30, 60]
        assert result["results"] == [[10, 20], [20, 40], [30, 60]]

    @pytest.mark.asyncio
    async def test_three_level_nested_forloop(self):
        """Test 3-level nested ForLoopNode."""

        @code_node
        def combine(a: int, b: int, c: int):
            return {"value": a * 100 + b * 10 + c}

        with ForLoopNode(name="level1", inputs={"a": Each([1, 2])}) as l1:
            with ForLoopNode(name="level2", inputs={"b": Each([3, 4]), "a": PARENT["a"]}) as l2:
                with ForLoopNode(
                    name="level3", inputs={"c": Each([5, 6]), "a": PARENT["a"], "b": PARENT["b"]}
                ) as l3:
                    node = combine(
                        inputs={"a": PARENT["a"], "b": PARENT["b"], "c": PARENT["c"]},
                        outputs={"*": PARENT},
                    )
                    START >> node >> END

                l3["value"] >> PARENT["values"]
                START >> l3 >> END

            l2["values"] >> PARENT["level2_results"]
            START >> l2 >> END

        l1.build()
        schema = StateSchema(l1)
        state = MemoryState(schema)

        result = await l1.run(state)

        # a=1: b=3: [135, 136], b=4: [145, 146]
        # a=2: b=3: [235, 236], b=4: [245, 246]
        expected = [[[135, 136], [145, 146]], [[235, 236], [245, 246]]]
        assert result["level2_results"] == expected


# ============================================================
# Test 5: ForLoopNode with WhileLoopNode Inside
# ============================================================


class TestForLoopWithWhileLoop:
    """Test ForLoopNode containing WhileLoopNode."""

    @pytest.mark.asyncio
    async def test_forloop_with_whileloop_inside(self):
        """Test sequential ForLoop containing WhileLoop."""

        @code_node
        def halve(value: int):
            return {"new_value": value // 2}

        with ForLoopNode(name="outer_for", inputs={"item": Each([10, 20, 30])}) as for_loop:
            with WhileLoopNode(
                name="inner_while",
                inputs={"value": PARENT["item"]},
                stop_condition="value < 5",
                max_iterations=10,
            ) as while_loop:
                node = halve(inputs={"value": PARENT["value"]})
                node["new_value"] >> PARENT["value"]
                START >> node >> END

            while_loop["value"] >> PARENT["final_value"]
            START >> while_loop >> END

        for_loop.build()
        schema = StateSchema(for_loop)
        state = MemoryState(schema)

        result = await for_loop.run(state)

        # 10 -> 5 -> 2 (stops at 2)
        # 20 -> 10 -> 5 -> 2 (stops at 2)
        # 30 -> 15 -> 7 -> 3 (stops at 3)
        assert result["final_value"] == [2, 2, 3]


# ============================================================
# Test 6: Empty Iteration
# ============================================================


class TestEmptyIteration:
    """Test behavior with empty iteration data."""

    @pytest.mark.asyncio
    async def test_empty_list(self):
        """Test sequential iteration over empty list."""

        @code_node
        def double(value: int):
            return {"result": value * 2}

        with ForLoopNode(name="empty_loop", inputs={"value": Each([])}) as loop:
            node = double(inputs={"value": PARENT["value"]}, outputs={"*": PARENT})
            START >> node >> END

        loop.build()
        schema = StateSchema(loop)
        state = MemoryState(schema)

        result = await loop.run(state)
        assert result["result"] == []
        assert result["iteration_metrics"]["total_iterations"] == 0


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

            with ForLoopNode(
                name="ref_loop",
                inputs={
                    "item": Each(gen_node["numbers"]),
                    "factor": gen_node["factor"],  # broadcast Ref
                },
                outputs={"*": PARENT},
            ) as loop:
                proc_node = process_item(
                    inputs={"item": PARENT["item"], "factor": PARENT["factor"]},
                    outputs={"*": PARENT},
                )
                START >> proc_node >> END

            START >> gen_node >> loop >> END

        graph.build()
        schema = StateSchema(graph)
        state = MemoryState(schema)

        result = await graph.run(state)
        assert result["result"] == [50, 100, 150]  # 10*5, 20*5, 30*5


# ============================================================
# Test 8: Iteration Metrics
# ============================================================


class TestIterationMetrics:
    """Test iteration metrics are collected."""

    @pytest.mark.asyncio
    async def test_metrics_collected(self):
        """Test that iteration metrics are returned."""

        @code_node
        def double(value: int):
            return {"result": value * 2}

        with ForLoopNode(name="metrics_loop", inputs={"value": Each([1, 2, 3])}) as loop:
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

        with ForLoopNode(
            name="mismatch_loop",
            inputs={
                "x": Each([1, 2, 3]),
                "y": Each([10, 20]),  # Different length!
            },
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
# Test 10: Accumulation Pattern (Sequential Dependency)
# ============================================================


class TestAccumulationPattern:
    """Test patterns that require sequential execution."""

    @pytest.mark.asyncio
    async def test_running_total(self):
        """Test accumulating values across iterations.

        This pattern requires sequential execution because each iteration
        depends on the result of the previous one.
        """
        # Use a closure to maintain state across iterations
        totals = []

        @code_node
        def accumulate(value: int):
            # Get previous total or start at 0
            prev_total = totals[-1] if totals else 0
            new_total = prev_total + value
            totals.append(new_total)
            return {"running_total": new_total}

        with ForLoopNode(name="accumulate_loop", inputs={"value": Each([1, 2, 3, 4, 5])}) as loop:
            node = accumulate(inputs={"value": PARENT["value"]}, outputs={"*": PARENT})
            START >> node >> END

        loop.build()
        schema = StateSchema(loop)
        state = MemoryState(schema)

        result = await loop.run(state)

        # Sequential: 1, 1+2=3, 3+3=6, 6+4=10, 10+5=15
        assert result["running_total"] == [1, 3, 6, 10, 15]


# ============================================================
# Test: for_() Shorthand
# ============================================================


class TestForShorthand:
    """Test for_() shorthand function."""

    @pytest.mark.asyncio
    async def test_for_shorthand_basic(self):
        """Test basic for_() with Each()."""

        @code_node
        def double(value: int):
            return {"result": value * 2}

        with for_(value=Each([1, 2, 3])) as loop:
            node = double(inputs={"value": PARENT["value"]}, outputs={"*": PARENT})
            START >> node >> END

        loop.build()
        schema = StateSchema(loop)
        state = MemoryState(schema)

        result = await loop.run(state)
        assert result["result"] == [2, 4, 6]

    @pytest.mark.asyncio
    async def test_for_shorthand_broadcast(self):
        """Test for_() with Each() and broadcast values."""

        @code_node
        def multiply(value: int, multiplier: int):
            return {"result": value * multiplier}

        with for_(value=Each([1, 2, 3]), multiplier=10) as loop:
            node = multiply(
                inputs={"value": PARENT["value"], "multiplier": PARENT["multiplier"]},
                outputs={"*": PARENT},
            )
            START >> node >> END

        loop.build()
        schema = StateSchema(loop)
        state = MemoryState(schema)

        result = await loop.run(state)
        assert result["result"] == [10, 20, 30]

    def test_for_shorthand_auto_name(self):
        """Test that for_() auto-names from variable assignment."""
        my_loop = for_(value=Each([1, 2, 3]))
        assert my_loop.name == "my_loop"

    def test_for_shorthand_is_forloopnode(self):
        """Test that for_() returns a ForLoopNode instance."""
        loop = for_(value=Each([1, 2, 3]))
        assert isinstance(loop, ForLoopNode)
