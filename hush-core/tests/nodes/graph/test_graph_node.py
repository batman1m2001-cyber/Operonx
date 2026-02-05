"""Test suite for GraphNode - from simple to complex scenarios.

Only tests GraphNode and CodeNode. Flow nodes (BranchNode, ForLoopNode, etc.)
are tested separately.
"""

import asyncio

import pytest

from hush.core import (
    END,
    PARENT,
    START,
    CodeNode,
    GraphNode,
    StateSchema,
    code_node,
)

# ============================================================
# Test 1: Single Node Graph
# ============================================================


class TestSingleNodeGraph:
    """Test graphs with a single node."""

    @pytest.mark.asyncio
    async def test_single_code_node(self):
        """Single CodeNode that doubles a value."""
        with GraphNode(name="single_node_graph") as graph:
            node = CodeNode(
                name="double",
                code_fn=lambda x: {"result": x * 2},
                inputs={"x": PARENT["x"]},
                outputs={"*": PARENT},
            )
            START >> node >> END

        graph.build()

        schema = StateSchema(graph)
        state = schema.create_state(inputs={"x": 5})
        result = await graph.run(state)

        assert result["result"] == 10

    @pytest.mark.asyncio
    async def test_single_node_with_decorator(self):
        """Single node using @code_node decorator."""

        @code_node
        def triple(x: int):
            return {"result": x * 3}

        with GraphNode(name="decorator_graph") as graph:
            node = triple(inputs={"x": PARENT["x"]}, outputs={"*": PARENT})
            START >> node >> END

        graph.build()

        schema = StateSchema(graph)
        state = schema.create_state(inputs={"x": 4})
        result = await graph.run(state)

        assert result["result"] == 12

    @pytest.mark.asyncio
    async def test_single_node_no_output_mapping(self):
        """Single node without explicit output mapping."""
        with GraphNode(name="no_output_map") as graph:
            node = CodeNode(
                name="compute", code_fn=lambda x: {"result": x + 100}, inputs={"x": PARENT["x"]}
            )
            START >> node >> END

        graph.build()

        schema = StateSchema(graph)
        state = schema.create_state(inputs={"x": 5})
        await graph.run(state)

        # Access result directly from state
        assert state["no_output_map.compute", "result", None] == 105


# ============================================================
# Test 2: Linear Graph (A -> B -> C)
# ============================================================


class TestLinearGraph:
    """Test linear sequential graphs."""

    @pytest.mark.asyncio
    async def test_two_nodes_linear(self):
        """Two nodes in sequence: add then multiply."""
        with GraphNode(name="two_node_linear") as graph:
            node_a = CodeNode(
                name="add_10", code_fn=lambda x: {"result": x + 10}, inputs={"x": PARENT["x"]}
            )
            node_b = CodeNode(
                name="multiply_2",
                code_fn=lambda x: {"result": x * 2},
                inputs={"x": node_a["result"]},
                outputs={"*": PARENT},
            )
            START >> node_a >> node_b >> END

        graph.build()

        schema = StateSchema(graph)
        state = schema.create_state(inputs={"x": 5})
        result = await graph.run(state)

        # (5 + 10) * 2 = 30
        assert result["result"] == 30

    @pytest.mark.asyncio
    async def test_three_nodes_linear(self):
        """Three nodes in sequence: add, multiply, subtract."""
        with GraphNode(name="three_node_linear") as graph:
            node_a = CodeNode(
                name="add_10", code_fn=lambda x: {"result": x + 10}, inputs={"x": PARENT["x"]}
            )
            node_b = CodeNode(
                name="multiply_2",
                code_fn=lambda x: {"result": x * 2},
                inputs={"x": node_a["result"]},
            )
            node_c = CodeNode(
                name="subtract_5",
                code_fn=lambda x: {"result": x - 5},
                inputs={"x": node_b["result"]},
                outputs={"*": PARENT},
            )
            START >> node_a >> node_b >> node_c >> END

        graph.build()

        schema = StateSchema(graph)
        state = schema.create_state(inputs={"x": 5})
        result = await graph.run(state)

        # ((5 + 10) * 2) - 5 = 25
        assert result["result"] == 25

    @pytest.mark.asyncio
    async def test_long_chain(self):
        """Five nodes in a chain."""
        with GraphNode(name="long_chain") as graph:
            n1 = CodeNode(name="n1", code_fn=lambda x: {"v": x + 1}, inputs={"x": PARENT["x"]})
            n2 = CodeNode(name="n2", code_fn=lambda x: {"v": x + 2}, inputs={"x": n1["v"]})
            n3 = CodeNode(name="n3", code_fn=lambda x: {"v": x + 3}, inputs={"x": n2["v"]})
            n4 = CodeNode(name="n4", code_fn=lambda x: {"v": x + 4}, inputs={"x": n3["v"]})
            n5 = CodeNode(
                name="n5",
                code_fn=lambda x: {"v": x + 5},
                inputs={"x": n4["v"]},
                outputs={"*": PARENT},
            )

            START >> n1 >> n2 >> n3 >> n4 >> n5 >> END

        graph.build()

        schema = StateSchema(graph)
        state = schema.create_state(inputs={"x": 0})
        result = await graph.run(state)

        # 0 + 1 + 2 + 3 + 4 + 5 = 15
        assert result["v"] == 15


# ============================================================
# Test 3: Parallel Graph (Fork and Merge)
# ============================================================


class TestParallelGraph:
    """Test graphs with parallel branches."""

    @pytest.mark.asyncio
    async def test_simple_fork_merge(self):
        """Fork into two branches, then merge."""
        with GraphNode(name="fork_merge") as graph:
            start = CodeNode(
                name="start", code_fn=lambda x: {"value": x}, inputs={"x": PARENT["x"]}
            )
            branch_a = CodeNode(
                name="branch_a", code_fn=lambda x: {"result": x * 2}, inputs={"x": start["value"]}
            )
            branch_b = CodeNode(
                name="branch_b", code_fn=lambda x: {"result": x * 3}, inputs={"x": start["value"]}
            )
            merge = CodeNode(
                name="merge",
                code_fn=lambda a, b: {"total": a + b},
                inputs={"a": branch_a["result"], "b": branch_b["result"]},
                outputs={"*": PARENT},
            )

            START >> start >> [branch_a, branch_b] >> merge >> END

        graph.build()

        schema = StateSchema(graph)
        state = schema.create_state(inputs={"x": 10})
        result = await graph.run(state)

        # (10 * 2) + (10 * 3) = 50
        assert result["total"] == 50

    @pytest.mark.asyncio
    async def test_three_way_fork(self):
        """Fork into three branches, then merge."""
        with GraphNode(name="three_way_fork") as graph:
            start = CodeNode(
                name="start", code_fn=lambda x: {"value": x}, inputs={"x": PARENT["x"]}
            )
            b1 = CodeNode(name="b1", code_fn=lambda x: {"r": x * 2}, inputs={"x": start["value"]})
            b2 = CodeNode(name="b2", code_fn=lambda x: {"r": x * 3}, inputs={"x": start["value"]})
            b3 = CodeNode(name="b3", code_fn=lambda x: {"r": x * 4}, inputs={"x": start["value"]})

            merge = CodeNode(
                name="merge",
                code_fn=lambda a, b, c: {"total": a + b + c},
                inputs={"a": b1["r"], "b": b2["r"], "c": b3["r"]},
                outputs={"*": PARENT},
            )

            START >> start >> [b1, b2, b3] >> merge >> END

        graph.build()

        schema = StateSchema(graph)
        state = schema.create_state(inputs={"x": 10})
        result = await graph.run(state)

        # (10*2) + (10*3) + (10*4) = 90
        assert result["total"] == 90

    @pytest.mark.asyncio
    async def test_diamond_pattern(self):
        """Diamond: A -> [B, C] -> D (classic DAG pattern)."""
        with GraphNode(name="diamond") as graph:
            a = CodeNode(name="a", code_fn=lambda x: {"out": x}, inputs={"x": PARENT["x"]})
            b = CodeNode(name="b", code_fn=lambda x: {"out": x + 100}, inputs={"x": a["out"]})
            c = CodeNode(name="c", code_fn=lambda x: {"out": x + 200}, inputs={"x": a["out"]})
            d = CodeNode(
                name="d",
                code_fn=lambda x, y: {"result": x + y},
                inputs={"x": b["out"], "y": c["out"]},
                outputs={"*": PARENT},
            )

            START >> a >> [b, c] >> d >> END

        graph.build()

        schema = StateSchema(graph)
        state = schema.create_state(inputs={"x": 1})
        result = await graph.run(state)

        # (1 + 100) + (1 + 200) = 302
        assert result["result"] == 302

    @pytest.mark.asyncio
    async def test_parallel_independent_branches(self):
        """Multiple independent parallel paths."""
        with GraphNode(name="parallel_independent") as graph:
            # Two independent branches from input
            branch_a = CodeNode(
                name="branch_a", code_fn=lambda x: {"result": x * 10}, inputs={"x": PARENT["x"]}
            )
            branch_b = CodeNode(
                name="branch_b", code_fn=lambda y: {"result": y + 5}, inputs={"y": PARENT["y"]}
            )
            merge = CodeNode(
                name="merge",
                code_fn=lambda a, b: {"sum": a + b},
                inputs={"a": branch_a["result"], "b": branch_b["result"]},
                outputs={"*": PARENT},
            )

            START >> [branch_a, branch_b] >> merge >> END

        graph.build()

        schema = StateSchema(graph)
        state = schema.create_state(inputs={"x": 3, "y": 7})
        result = await graph.run(state)

        # (3 * 10) + (7 + 5) = 42
        assert result["sum"] == 42


# ============================================================
# Test 4: Multiple Inputs/Outputs
# ============================================================


class TestMultipleIO:
    """Test graphs with multiple inputs and outputs."""

    @pytest.mark.asyncio
    async def test_multiple_inputs(self):
        """Graph with multiple input variables."""
        with GraphNode(name="multi_input") as graph:
            node = CodeNode(
                name="add",
                code_fn=lambda a, b, c: {"sum": a + b + c},
                inputs={"a": PARENT["a"], "b": PARENT["b"], "c": PARENT["c"]},
                outputs={"*": PARENT},
            )
            START >> node >> END

        graph.build()

        schema = StateSchema(graph)
        state = schema.create_state(inputs={"a": 1, "b": 2, "c": 3})
        result = await graph.run(state)

        assert result["sum"] == 6

    @pytest.mark.asyncio
    async def test_multiple_outputs(self):
        """Graph with multiple output variables."""
        with GraphNode(name="multi_output") as graph:
            node = CodeNode(
                name="split",
                code_fn=lambda x: {"double": x * 2, "triple": x * 3, "quad": x * 4},
                inputs={"x": PARENT["x"]},
                outputs={"*": PARENT},
            )
            START >> node >> END

        graph.build()

        schema = StateSchema(graph)
        state = schema.create_state(inputs={"x": 5})
        result = await graph.run(state)

        assert result["double"] == 10
        assert result["triple"] == 15
        assert result["quad"] == 20

    @pytest.mark.asyncio
    async def test_selective_output_mapping(self):
        """Map only some outputs to graph output."""
        with GraphNode(name="selective_output") as graph:
            node = CodeNode(
                name="compute",
                code_fn=lambda x: {"a": x + 1, "b": x + 2, "c": x + 3},
                inputs={"x": PARENT["x"]},
                outputs={"a": PARENT["result_a"], "c": PARENT["result_c"]},
            )
            START >> node >> END

        graph.build()

        schema = StateSchema(graph)
        state = schema.create_state(inputs={"x": 10})
        result = await graph.run(state)

        assert result["result_a"] == 11
        assert result["result_c"] == 13
        assert "result_b" not in result  # b was not mapped


# ============================================================
# Test 5: Complex Data Types
# ============================================================


class TestComplexDataTypes:
    """Test graphs with complex data types."""

    @pytest.mark.asyncio
    async def test_dict_processing(self):
        """Process dictionary data."""
        with GraphNode(name="dict_graph") as graph:
            node = CodeNode(
                name="process",
                code_fn=lambda data: {
                    "keys": list(data.keys()),
                    "values": list(data.values()),
                    "count": len(data),
                },
                inputs={"data": PARENT["data"]},
                outputs={"*": PARENT},
            )
            START >> node >> END

        graph.build()

        schema = StateSchema(graph)
        state = schema.create_state(inputs={"data": {"a": 1, "b": 2, "c": 3}})
        result = await graph.run(state)

        assert result["keys"] == ["a", "b", "c"]
        assert result["values"] == [1, 2, 3]
        assert result["count"] == 3

    @pytest.mark.asyncio
    async def test_list_processing(self):
        """Process list data through pipeline."""
        with GraphNode(name="list_graph") as graph:
            double = CodeNode(
                name="double",
                code_fn=lambda items: {"result": [x * 2 for x in items]},
                inputs={"items": PARENT["items"]},
            )
            sum_all = CodeNode(
                name="sum",
                code_fn=lambda items: {"total": sum(items)},
                inputs={"items": double["result"]},
                outputs={"*": PARENT},
            )
            START >> double >> sum_all >> END

        graph.build()

        schema = StateSchema(graph)
        state = schema.create_state(inputs={"items": [1, 2, 3, 4, 5]})
        result = await graph.run(state)

        # sum([2, 4, 6, 8, 10]) = 30
        assert result["total"] == 30

    @pytest.mark.asyncio
    async def test_string_processing(self):
        """Process string data through pipeline."""
        with GraphNode(name="string_graph") as graph:
            upper = CodeNode(
                name="upper",
                code_fn=lambda text: {"result": text.upper()},
                inputs={"text": PARENT["text"]},
            )
            reverse = CodeNode(
                name="reverse",
                code_fn=lambda text: {"result": text[::-1]},
                inputs={"text": upper["result"]},
                outputs={"*": PARENT},
            )
            START >> upper >> reverse >> END

        graph.build()

        schema = StateSchema(graph)
        state = schema.create_state(inputs={"text": "hello"})
        result = await graph.run(state)

        assert result["result"] == "OLLEH"


# ============================================================
# Test 6: Async Operations
# ============================================================


class TestAsyncOperations:
    """Test graphs with async code functions."""

    @pytest.mark.asyncio
    async def test_async_single_node(self):
        """Single async node using return_keys for nested async functions."""

        async def async_double(x: int):
            await asyncio.sleep(0.01)  # Simulate async work
            return {"result": x * 2}

        with GraphNode(name="async_single") as graph:
            # Note: return_keys needed because inspect.getsource() has trouble
            # with functions defined inside other functions
            node = CodeNode(
                name="double",
                code_fn=async_double,
                return_keys=["result"],
                inputs={"x": PARENT["x"]},
                outputs={"*": PARENT},
            )
            START >> node >> END

        graph.build()

        schema = StateSchema(graph)
        state = schema.create_state(inputs={"x": 21})
        result = await graph.run(state)

        assert result["result"] == 42

    @pytest.mark.asyncio
    async def test_async_pipeline(self):
        """Pipeline with async nodes using return_keys."""

        async def async_add(x: int):
            await asyncio.sleep(0.01)
            return {"result": x + 10}

        async def async_multiply(x: int):
            await asyncio.sleep(0.01)
            return {"result": x * 2}

        with GraphNode(name="async_pipeline") as graph:
            add = CodeNode(
                name="add", code_fn=async_add, return_keys=["result"], inputs={"x": PARENT["x"]}
            )
            mult = CodeNode(
                name="multiply",
                code_fn=async_multiply,
                return_keys=["result"],
                inputs={"x": add["result"]},
                outputs={"*": PARENT},
            )
            START >> add >> mult >> END

        graph.build()

        schema = StateSchema(graph)
        state = schema.create_state(inputs={"x": 5})
        result = await graph.run(state)

        # (5 + 10) * 2 = 30
        assert result["result"] == 30

    @pytest.mark.asyncio
    async def test_async_parallel(self):
        """Parallel async nodes execute concurrently."""
        call_order = []

        async def slow_a(x: int):
            call_order.append("a_start")
            await asyncio.sleep(0.05)
            call_order.append("a_end")
            return {"result": x * 2}

        async def slow_b(x: int):
            call_order.append("b_start")
            await asyncio.sleep(0.05)
            call_order.append("b_end")
            return {"result": x * 3}

        with GraphNode(name="async_parallel") as graph:
            start = CodeNode(
                name="start", code_fn=lambda x: {"value": x}, inputs={"x": PARENT["x"]}
            )
            a = CodeNode(
                name="a", code_fn=slow_a, inputs={"x": start["value"]}, outputs={"result": None}
            )
            b = CodeNode(
                name="b", code_fn=slow_b, inputs={"x": start["value"]}, outputs={"result": None}
            )
            merge = CodeNode(
                name="merge",
                code_fn=lambda a, b: {"total": a + b},
                inputs={"a": a["result"], "b": b["result"]},
                outputs={"*": PARENT},
            )

            START >> start >> [a, b] >> merge >> END

        graph.build()

        schema = StateSchema(graph)
        state = schema.create_state(inputs={"x": 10})
        result = await graph.run(state)

        # Both should start before either ends (concurrent execution)
        assert "a_start" in call_order
        assert "b_start" in call_order
        assert result["total"] == 50  # (10*2) + (10*3)


# ============================================================
# Test 7: Error Handling
# ============================================================


class TestErrorHandling:
    """Test error handling in graphs."""

    @pytest.mark.asyncio
    async def test_node_error_captured(self):
        """Errors in nodes are captured in state."""

        def failing_fn(x):
            raise ValueError("Intentional error")

        with GraphNode(name="error_graph") as graph:
            node = CodeNode(
                name="failing", code_fn=failing_fn, inputs={"x": PARENT["x"]}, outputs={"*": PARENT}
            )
            START >> node >> END

        graph.build()

        schema = StateSchema(graph)
        state = schema.create_state(inputs={"x": 5})
        await graph.run(state)

        # Check error is captured
        error = state["error_graph.failing", "error", None]
        assert "Intentional error" in error

    @pytest.mark.asyncio
    async def test_partial_pipeline_error(self):
        """Error in middle of pipeline."""
        with GraphNode(name="partial_error") as graph:
            first = CodeNode(
                name="first", code_fn=lambda x: {"result": x + 10}, inputs={"x": PARENT["x"]}
            )
            failing = CodeNode(
                name="failing",
                code_fn=lambda x: 1 / 0,  # Division by zero
                inputs={"x": first["result"]},
            )
            last = CodeNode(
                name="last",
                code_fn=lambda x: {"result": x * 2},
                inputs={"x": failing["result"]},
                outputs={"*": PARENT},
            )

            START >> first >> failing >> last >> END

        graph.build()

        schema = StateSchema(graph)
        state = schema.create_state(inputs={"x": 5})
        await graph.run(state)

        # First node should succeed
        assert state["partial_error.first", "result", None] == 15

        # Failing node should have error
        error = state["partial_error.failing", "error", None]
        assert "division by zero" in error


# ============================================================
# Test 8: Edge Cases
# ============================================================


class TestEdgeCases:
    """Test edge cases and boundary conditions."""

    @pytest.mark.asyncio
    async def test_empty_input(self):
        """Handle empty string inputs gracefully."""
        with GraphNode(name="empty_input") as graph:
            node = CodeNode(
                name="handle_empty",
                code_fn=lambda x: {"result": x if x else "default"},
                inputs={"x": PARENT["x"]},
                outputs={"*": PARENT},
            )
            START >> node >> END

        graph.build()

        schema = StateSchema(graph)
        state = schema.create_state(inputs={"x": ""})
        result = await graph.run(state)

        assert result["result"] == "default"

    @pytest.mark.asyncio
    async def test_large_data(self):
        """Handle large data volumes."""
        with GraphNode(name="large_data") as graph:
            node = CodeNode(
                name="process",
                code_fn=lambda data: {"count": len(data), "sum": sum(data)},
                inputs={"data": PARENT["data"]},
                outputs={"*": PARENT},
            )
            START >> node >> END

        graph.build()

        large_list = list(range(10000))
        schema = StateSchema(graph)
        state = schema.create_state(inputs={"data": large_list})
        result = await graph.run(state)

        assert result["count"] == 10000
        assert result["sum"] == sum(range(10000))

    @pytest.mark.asyncio
    async def test_unicode_data(self):
        """Handle unicode data correctly."""
        with GraphNode(name="unicode_graph") as graph:
            node = CodeNode(
                name="process",
                code_fn=lambda text: {"result": f"Processed: {text}"},
                inputs={"text": PARENT["text"]},
                outputs={"*": PARENT},
            )
            START >> node >> END

        graph.build()

        schema = StateSchema(graph)
        state = schema.create_state(inputs={"text": "Hello 世界 🌍"})
        result = await graph.run(state)

        assert result["result"] == "Processed: Hello 世界 🌍"

    @pytest.mark.asyncio
    async def test_zero_and_negative(self):
        """Handle zero and negative numbers."""
        with GraphNode(name="zero_negative") as graph:
            node = CodeNode(
                name="compute",
                code_fn=lambda x, y: {"sum": x + y, "product": x * y},
                inputs={"x": PARENT["x"], "y": PARENT["y"]},
                outputs={"*": PARENT},
            )
            START >> node >> END

        graph.build()

        schema = StateSchema(graph)
        state = schema.create_state(inputs={"x": -5, "y": 0})
        result = await graph.run(state)

        assert result["sum"] == -5
        assert result["product"] == 0


# ============================================================
# Test 9: @code_node Decorator
# ============================================================


class TestCodeNodeDecorator:
    """Test the @code_node decorator."""

    @pytest.mark.asyncio
    async def test_decorator_basic(self):
        """Basic decorator usage."""

        @code_node
        def add_one(x: int):
            return {"result": x + 1}

        with GraphNode(name="decorator_basic") as graph:
            node = add_one(inputs={"x": PARENT["x"]}, outputs={"*": PARENT})
            START >> node >> END

        graph.build()

        schema = StateSchema(graph)
        state = schema.create_state(inputs={"x": 10})
        result = await graph.run(state)

        assert result["result"] == 11

    @pytest.mark.asyncio
    async def test_decorator_with_defaults(self):
        """Decorator with default parameter values."""

        @code_node
        def add(x: int, amount: int = 10):
            return {"result": x + amount}

        with GraphNode(name="decorator_defaults") as graph:
            node = add(inputs={"x": PARENT["x"]}, outputs={"*": PARENT})
            START >> node >> END

        graph.build()

        schema = StateSchema(graph)
        state = schema.create_state(inputs={"x": 5})
        result = await graph.run(state)

        # Uses default amount=10
        assert result["result"] == 15

    @pytest.mark.asyncio
    async def test_decorator_pipeline(self):
        """Multiple decorated functions in pipeline."""

        @code_node
        def step1(x: int):
            return {"value": x * 2}

        @code_node
        def step2(x: int):
            return {"value": x + 5}

        @code_node
        def step3(x: int):
            return {"result": x**2}

        with GraphNode(name="decorator_pipeline") as graph:
            n1 = step1(inputs={"x": PARENT["x"]})
            n2 = step2(inputs={"x": n1["value"]})
            n3 = step3(inputs={"x": n2["value"]}, outputs={"*": PARENT})

            START >> n1 >> n2 >> n3 >> END

        graph.build()

        schema = StateSchema(graph)
        state = schema.create_state(inputs={"x": 3})
        result = await graph.run(state)

        # ((3 * 2) + 5) ** 2 = 11 ** 2 = 121
        assert result["result"] == 121


# ============================================================
# Test 10: Soft Edge Behavior
# ============================================================


class TestSoftEdgeBehavior:
    """Test soft edge (>> ~) vs hard edge (>>) behavior.

    Soft edge semantics:
    - Hard edge (>>): Đếm từng cái một vào ready_count
    - Soft edge (>> ~): Nhiều soft edges đến cùng node đếm chung là 1
      (chỉ cần BẤT KỲ một soft predecessor hoàn thành)

    Syntax:
    - a >> b      # hard edge
    - a >> ~b     # soft edge (dùng ~ marker)
    - [a, b] >> ~c  # soft edges từ nhiều nodes

    Ví dụ: A >> D, B >> ~D, C >> ~D
    => ready_count[D] = 2 (1 hard + 1 soft group)
    => D chạy khi A hoàn thành VÀ (B HOẶC C) hoàn thành
    """

    @pytest.mark.asyncio
    async def test_multiple_soft_edges_any_one_triggers(self):
        """Multiple soft edges: D runs when ANY ONE soft predecessor completes.

        Graph: B > D, C > D (both soft)
        D should run when either B or C completes first.
        """
        execution_order = []

        with GraphNode(name="soft_any") as graph:
            b = CodeNode(
                name="b",
                code_fn=lambda: (execution_order.append("b"), {"result": "b"})[1],
                return_keys=["result"],
                inputs={},
            )
            c = CodeNode(
                name="c",
                code_fn=lambda: (execution_order.append("c"), {"result": "c"})[1],
                return_keys=["result"],
                inputs={},
            )
            d = CodeNode(
                name="d",
                code_fn=lambda: (execution_order.append("d"), {"result": "d"})[1],
                return_keys=["result"],
                inputs={},
                outputs={"*": PARENT},
            )

            # Soft edges: B >> ~D, C >> ~D
            START >> [b, c]
            b >> ~d
            c >> ~d
            d >> END

        graph.build()

        # Verify ready_count: D should have ready_count = 1 (soft edges count as 1 group)
        assert graph.ready_count["d"] == 1, (
            f"Expected ready_count[d]=1, got {graph.ready_count['d']}"
        )
        assert "d" in graph.has_soft_preds, "d should be in has_soft_preds"

        schema = StateSchema(graph)
        state = schema.create_state(inputs={})
        result = await graph.run(state)

        # D should execute after either B or C completes
        assert "d" in execution_order
        assert result["result"] == "d"

    @pytest.mark.asyncio
    async def test_hard_and_soft_edges_combined(self):
        """Mixed hard and soft edges: A >> D, B > D, C > D.

        D must wait for:
        - A (hard edge - always required)
        - AND (B OR C) (soft edge group - any one required)
        """
        execution_order = []

        async def track_node(name: str, delay: float = 0):
            if delay > 0:
                await asyncio.sleep(delay)
            execution_order.append(name)
            return {"result": name}

        with GraphNode(name="mixed_edges") as graph:
            a = CodeNode(
                name="a",
                code_fn=lambda: (execution_order.append("a"), {"result": "a"})[1],
                return_keys=["result"],
                inputs={},
            )
            b = CodeNode(
                name="b",
                code_fn=lambda: (execution_order.append("b"), {"result": "b"})[1],
                return_keys=["result"],
                inputs={},
            )
            c = CodeNode(
                name="c",
                code_fn=lambda: (execution_order.append("c"), {"result": "c"})[1],
                return_keys=["result"],
                inputs={},
            )
            d = CodeNode(
                name="d",
                code_fn=lambda: (execution_order.append("d"), {"result": "d"})[1],
                return_keys=["result"],
                inputs={},
                outputs={"*": PARENT},
            )

            # Hard edge: A >> D
            # Soft edges: B >> ~D, C >> ~D
            START >> [a, b, c]
            a >> d
            b >> ~d
            c >> ~d
            d >> END

        graph.build()

        # Verify ready_count: D should have ready_count = 2 (1 hard + 1 soft group)
        assert graph.ready_count["d"] == 2, (
            f"Expected ready_count[d]=2, got {graph.ready_count['d']}"
        )
        assert "d" in graph.has_soft_preds, "d should be in has_soft_preds"

        schema = StateSchema(graph)
        state = schema.create_state(inputs={})
        result = await graph.run(state)

        # D should execute after A and (B or C) complete
        d_index = execution_order.index("d")
        a_index = execution_order.index("a")
        assert d_index > a_index, "D must execute after A (hard edge)"

        # At least one of B or C must complete before D
        b_index = execution_order.index("b") if "b" in execution_order else float("inf")
        c_index = execution_order.index("c") if "c" in execution_order else float("inf")
        assert d_index > min(b_index, c_index), "D must execute after at least one of B or C"

        assert result["result"] == "d"

    @pytest.mark.asyncio
    async def test_soft_edge_only_one_counted(self):
        """Verify that even if multiple soft predecessors complete, only one is counted.

        Graph: B > D, C > D (both soft, both will complete)
        D's ready_count should only decrease by 1 total (not 2).
        """
        with GraphNode(name="soft_count") as graph:
            b = CodeNode(name="b", code_fn=lambda: {"result": "b"}, inputs={})
            c = CodeNode(name="c", code_fn=lambda: {"result": "c"}, inputs={})
            d = CodeNode(
                name="d",
                code_fn=lambda b_done, c_done: {"combined": f"{b_done}+{c_done}"},
                inputs={"b_done": b["result"], "c_done": c["result"]},
                outputs={"*": PARENT},
            )

            # Using [b, c] >> ~d syntax for soft edges from multiple nodes
            START >> [b, c] >> d >> END

        graph.build()

        # D has ready_count = 2 (both hard edges counted)
        assert graph.ready_count["d"] == 2

        schema = StateSchema(graph)
        state = schema.create_state(inputs={})
        result = await graph.run(state)

        # D should run and have access to both B and C results
        assert result["combined"] == "b+c"

    @pytest.mark.asyncio
    async def test_multiple_hard_edges_all_required(self):
        """Multiple hard edges: ALL must complete before D runs.

        Graph: A >> D, B >> D (both hard)
        D must wait for BOTH A and B.
        """
        execution_order = []

        with GraphNode(name="multi_hard") as graph:
            a = CodeNode(
                name="a",
                code_fn=lambda: (execution_order.append("a"), {"result": "a"})[1],
                return_keys=["result"],
                inputs={},
            )
            b = CodeNode(
                name="b",
                code_fn=lambda: (execution_order.append("b"), {"result": "b"})[1],
                return_keys=["result"],
                inputs={},
            )
            d = CodeNode(
                name="d",
                code_fn=lambda: (execution_order.append("d"), {"result": "d"})[1],
                return_keys=["result"],
                inputs={},
                outputs={"*": PARENT},
            )

            START >> [a, b] >> d >> END

        graph.build()

        # D has ready_count = 2 (both hard edges counted)
        assert graph.ready_count["d"] == 2, (
            f"Expected ready_count[d]=2, got {graph.ready_count['d']}"
        )
        assert "d" not in graph.has_soft_preds, "d should NOT be in has_soft_preds (no soft edges)"

        schema = StateSchema(graph)
        state = schema.create_state(inputs={})
        result = await graph.run(state)

        # D must execute after BOTH A and B
        d_index = execution_order.index("d")
        a_index = execution_order.index("a")
        b_index = execution_order.index("b")
        assert d_index > a_index, "D must execute after A"
        assert d_index > b_index, "D must execute after B"

    @pytest.mark.asyncio
    async def test_complex_mixed_topology(self):
        """Complex graph with multiple hard and soft edges.

        Graph topology:
        - START >> [A, B, C]
        - A >> E (hard)
        - B > E (soft)
        - C > E (soft)
        - A >> F (hard)
        - B >> F (hard)
        - E >> END
        - F >> END

        E waits for: A AND (B OR C)
        F waits for: A AND B
        """
        execution_order = []

        with GraphNode(name="complex_mixed") as graph:
            a = CodeNode(
                name="a",
                code_fn=lambda: (execution_order.append("a"), {"v": 1})[1],
                return_keys=["v"],
                inputs={},
            )
            b = CodeNode(
                name="b",
                code_fn=lambda: (execution_order.append("b"), {"v": 2})[1],
                return_keys=["v"],
                inputs={},
            )
            c = CodeNode(
                name="c",
                code_fn=lambda: (execution_order.append("c"), {"v": 3})[1],
                return_keys=["v"],
                inputs={},
            )
            e = CodeNode(
                name="e",
                code_fn=lambda a_v: (execution_order.append("e"), {"result_e": a_v * 10})[1],
                return_keys=["result_e"],
                inputs={"a_v": a["v"]},
            )
            f = CodeNode(
                name="f",
                code_fn=lambda a_v, b_v: (execution_order.append("f"), {"result_f": a_v + b_v})[1],
                return_keys=["result_f"],
                inputs={"a_v": a["v"], "b_v": b["v"]},
            )

            START >> [a, b, c]

            # E: hard from A, soft from B and C using >> ~ syntax
            a >> e
            b >> ~e
            c >> ~e

            # F: hard from both A and B
            a >> f
            b >> f

            [e, f] >> END

        graph.build()

        # Verify ready_counts
        assert graph.ready_count["e"] == 2, (
            f"E should have ready_count=2 (1 hard + 1 soft group), got {graph.ready_count['e']}"
        )
        assert graph.ready_count["f"] == 2, (
            f"F should have ready_count=2 (2 hard), got {graph.ready_count['f']}"
        )
        assert "e" in graph.has_soft_preds, "E should be in has_soft_preds"
        assert "f" not in graph.has_soft_preds, "F should NOT be in has_soft_preds"

        schema = StateSchema(graph)
        state = schema.create_state(inputs={})
        await graph.run(state)

        # E executes after A and (B or C)
        e_index = execution_order.index("e")
        a_index = execution_order.index("a")
        b_index = execution_order.index("b") if "b" in execution_order else float("inf")
        c_index = execution_order.index("c") if "c" in execution_order else float("inf")

        assert e_index > a_index, "E must execute after A"
        assert e_index > min(b_index, c_index), "E must execute after at least one of B or C"

        # F executes after both A and B
        f_index = execution_order.index("f")
        assert f_index > a_index, "F must execute after A"
        assert f_index > b_index, "F must execute after B"

    @pytest.mark.asyncio
    async def test_soft_edge_with_delayed_hard_edge(self):
        """Soft edge completes first, but D must still wait for hard edge.

        Graph: A (slow) >> D, B (fast) > D
        B completes first, but D must wait for A.
        """
        execution_order = []

        async def slow_a():
            await asyncio.sleep(0.05)
            execution_order.append("a")
            return {"result": "a"}

        async def fast_b():
            await asyncio.sleep(0.01)
            execution_order.append("b")
            return {"result": "b"}

        with GraphNode(name="soft_delayed") as graph:
            a = CodeNode(name="a", code_fn=slow_a, return_keys=["result"], inputs={})
            b = CodeNode(name="b", code_fn=fast_b, return_keys=["result"], inputs={})
            d = CodeNode(
                name="d",
                code_fn=lambda: (execution_order.append("d"), {"result": "d"})[1],
                return_keys=["result"],
                inputs={},
                outputs={"*": PARENT},
            )

            START >> [a, b]
            a >> d  # Hard edge
            b > d  # Soft edge
            d >> END

        graph.build()

        assert graph.ready_count["d"] == 2  # 1 hard + 1 soft group

        schema = StateSchema(graph)
        state = schema.create_state(inputs={})
        result = await graph.run(state)

        # B completes before A (fast vs slow)
        b_index = execution_order.index("b")
        a_index = execution_order.index("a")
        assert b_index < a_index, "B should complete before A"

        # But D must still wait for A (hard edge requirement)
        d_index = execution_order.index("d")
        assert d_index > a_index, (
            "D must wait for A (hard edge) even though B (soft) completed first"
        )

        assert result["result"] == "d"


# ============================================================
# Test 11: Output Mapping Syntax (node["key"] >> PARENT["key"])
# ============================================================


class TestOutputMappingSyntax:
    """Test cú pháp output mapping mới với >>.

    Cú pháp:
    - node["key"] >> PARENT["key"]  → map output cụ thể
    """

    @pytest.mark.asyncio
    async def test_specific_output_mapping(self):
        """node["key"] >> PARENT["key"] maps output cụ thể."""
        with GraphNode(name="specific_mapping") as graph:
            node = CodeNode(
                name="compute",
                code_fn=lambda x: {"a": x + 1, "b": x + 2, "c": x + 3},
                inputs={"x": PARENT["x"]},
            )
            # Map chỉ a và c đến PARENT
            node["a"] >> PARENT["result_a"]
            node["c"] >> PARENT["result_c"]

            START >> node >> END

        graph.build()

        schema = StateSchema(graph)
        state = schema.create_state(inputs={"x": 10})
        result = await graph.run(state)

        assert result["result_a"] == 11
        assert result["result_c"] == 13
        assert "result_b" not in result  # b không được map

    @pytest.mark.asyncio
    async def test_mixed_old_and_new_syntax(self):
        """Có thể dùng cả outputs={"*": PARENT} và node["key"] >> PARENT["key"]."""
        with GraphNode(name="mixed_syntax") as graph:
            # Node 1 dùng cú pháp cũ
            node1 = CodeNode(
                name="node1",
                code_fn=lambda x: {"value": x * 2},
                inputs={"x": PARENT["x"]},
                outputs={"*": PARENT},  # Cú pháp cũ
            )
            # Node 2 dùng cú pháp mới
            node2 = CodeNode(
                name="node2", code_fn=lambda v: {"result": v + 100}, inputs={"v": node1["value"]}
            )
            node2["result"] >> PARENT["result"]  # Cú pháp mới

            START >> node1 >> node2 >> END

        graph.build()

        schema = StateSchema(graph)
        state = schema.create_state(inputs={"x": 5})
        result = await graph.run(state)

        assert result["value"] == 10  # Từ node1 (cú pháp cũ)
        assert result["result"] == 110  # Từ node2 (cú pháp mới)


# ============================================================
# Test 12: Node-to-Node Output Mapping (producer["key"] >> consumer["key"])
# ============================================================


class TestNodeToNodeOutputMapping:
    """Test output mapping syntax từ node đến node (không phải PARENT).

    Cú pháp:
    - producer["y"] >> consumer["x"]  → producer's "y" output maps to consumer's "x" input
    - Tương đương với: producer.outputs = {"y": consumer["x"]}
    """

    @pytest.mark.asyncio
    async def test_node_to_node_single_output(self):
        """producer['y'] >> consumer['x'] maps producer's y output to consumer's x input."""
        with GraphNode(name="node_to_node") as graph:
            # node1 produces a value
            node1 = CodeNode(
                name="producer", code_fn=lambda x: {"result": x * 2}, inputs={"x": PARENT["x"]}
            )
            # node2 receives from node1 via >> syntax
            node2 = CodeNode(
                name="consumer",
                code_fn=lambda value: {"final": value + 100},
                inputs={},  # inputs will be set via >> syntax
            )
            # Map node1's "result" to node2's "value" input
            node1["result"] >> node2["value"]

            # node3 outputs to PARENT
            node3 = CodeNode(
                name="final",
                code_fn=lambda v: {"output": v * 3},
                inputs={"v": node2["final"]},
                outputs={"*": PARENT},
            )

            START >> node1 >> node2 >> node3 >> END

        graph.build()

        schema = StateSchema(graph)
        state = schema.create_state(inputs={"x": 5})
        result = await graph.run(state)

        # (5 * 2) = 10 -> (10 + 100) = 110 -> (110 * 3) = 330
        assert result["output"] == 330

    @pytest.mark.asyncio
    async def test_node_to_node_multiple_outputs(self):
        """Multiple node-to-node output mappings."""
        with GraphNode(name="multi_node_mapping") as graph:
            # Producer node creates multiple outputs
            producer = CodeNode(
                name="producer",
                code_fn=lambda x: {"a": x + 1, "b": x + 2},
                inputs={"x": PARENT["x"]},
            )
            # Consumer receives both outputs via >> syntax
            consumer = CodeNode(
                name="consumer",
                code_fn=lambda val_a, val_b: {"sum": val_a + val_b},
                inputs={},
                outputs={"*": PARENT},
            )
            # Map producer's outputs to consumer's inputs
            producer["a"] >> consumer["val_a"]
            producer["b"] >> consumer["val_b"]

            START >> producer >> consumer >> END

        graph.build()

        schema = StateSchema(graph)
        state = schema.create_state(inputs={"x": 10})
        result = await graph.run(state)

        # (10+1) + (10+2) = 11 + 12 = 23
        assert result["sum"] == 23

    @pytest.mark.asyncio
    async def test_node_to_node_chain(self):
        """Chain of node-to-node mappings: A -> B -> C using >> syntax."""
        with GraphNode(name="chain_mapping") as graph:
            node_a = CodeNode(
                name="node_a", code_fn=lambda x: {"value": x + 10}, inputs={"x": PARENT["x"]}
            )
            node_b = CodeNode(name="node_b", code_fn=lambda inp: {"value": inp * 2}, inputs={})
            node_c = CodeNode(
                name="node_c",
                code_fn=lambda inp: {"result": inp - 5},
                inputs={},
                outputs={"*": PARENT},
            )

            # Chain mappings using >> syntax
            node_a["value"] >> node_b["inp"]
            node_b["value"] >> node_c["inp"]

            START >> node_a >> node_b >> node_c >> END

        graph.build()

        schema = StateSchema(graph)
        state = schema.create_state(inputs={"x": 5})
        result = await graph.run(state)

        # (5 + 10) = 15 -> (15 * 2) = 30 -> (30 - 5) = 25
        assert result["result"] == 25

    @pytest.mark.asyncio
    async def test_node_to_node_mixed_with_parent(self):
        """Mix node-to-node and PARENT mappings."""
        with GraphNode(name="mixed_mapping") as graph:
            producer = CodeNode(
                name="producer",
                code_fn=lambda x: {"a": x * 2, "b": x * 3},
                inputs={"x": PARENT["x"]},
            )
            consumer = CodeNode(
                name="consumer", code_fn=lambda val: {"processed": val + 100}, inputs={}
            )
            # Map producer["a"] to consumer input
            producer["a"] >> consumer["val"]
            # Map producer["b"] directly to PARENT
            producer["b"] >> PARENT["direct_b"]
            # Map consumer output to PARENT
            consumer["processed"] >> PARENT["processed"]

            START >> producer >> consumer >> END

        graph.build()

        schema = StateSchema(graph)
        state = schema.create_state(inputs={"x": 5})
        result = await graph.run(state)

        assert result["direct_b"] == 15  # 5 * 3
        assert result["processed"] == 110  # (5 * 2) + 100

    @pytest.mark.asyncio
    async def test_node_to_node_parallel_merge(self):
        """Parallel nodes output to a merge node via >> syntax."""
        with GraphNode(name="parallel_merge_mapping") as graph:
            branch_a = CodeNode(
                name="branch_a", code_fn=lambda x: {"result": x * 2}, inputs={"x": PARENT["x"]}
            )
            branch_b = CodeNode(
                name="branch_b", code_fn=lambda x: {"result": x * 3}, inputs={"x": PARENT["x"]}
            )
            merge = CodeNode(
                name="merge",
                code_fn=lambda a, b: {"total": a + b},
                inputs={},
                outputs={"*": PARENT},
            )

            # Map parallel branches to merge inputs via >> syntax
            branch_a["result"] >> merge["a"]
            branch_b["result"] >> merge["b"]

            START >> [branch_a, branch_b] >> merge >> END

        graph.build()

        schema = StateSchema(graph)
        state = schema.create_state(inputs={"x": 10})
        result = await graph.run(state)

        # (10 * 2) + (10 * 3) = 50
        assert result["total"] == 50


# ============================================================
# Test 13: Complex Graph with All Node Types
# ============================================================


class TestComplexGraphWithAllNodeTypes:
    """Test complex graphs combining BranchNode, ForLoopNode, WhileLoopNode, and CodeNode.

    These tests demonstrate real-world scenarios where multiple node types
    work together in a single workflow using new syntax:
    - Branch("name").if_(condition, target).else_(default)
    - producer["key"] >> consumer["key"] for output mapping
    """

    @pytest.mark.asyncio
    async def test_forloop_with_new_syntax(self):
        """Test ForLoopNode using >> syntax inside and to PARENT."""
        from hush.core.nodes.iteration.base import Each
        from hush.core.nodes.iteration.for_loop_node import ForLoopNode

        @code_node
        def double_number(value: int):
            return {"result": value * 2}

        with GraphNode(name="forloop_graph") as graph:
            with ForLoopNode(name="double_loop", inputs={"value": Each(PARENT["data"])}) as loop:
                node = double_number(inputs={"value": PARENT["value"]})
                node["result"] >> PARENT["result"]
                START >> node >> END

            # Map loop result to graph output
            loop["result"] >> PARENT["final_result"]
            START >> loop >> END

        graph.build()

        schema = StateSchema(graph)
        state = schema.create_state(inputs={"data": [1, 2, 3, 4, 5]})
        result = await graph.run(state)
        state.show()
        assert result["final_result"] == [2, 4, 6, 8, 10]

    @pytest.mark.asyncio
    async def test_while_loop_with_branch_inside(self):
        """WhileLoop with BranchNode inside for conditional processing.

        Loop increments counter. Inside loop, branch decides:
        - If counter is even: add 10
        - If counter is odd: add 5
        Stop when total >= 50
        """
        from hush.core.nodes.flow.branch_node import Branch
        from hush.core.nodes.iteration.while_loop_node import WhileLoopNode

        @code_node
        def increment_counter(counter: int):
            return {"new_counter": counter + 1}

        @code_node
        def add_ten(total: int):
            return {"new_total": total + 10}

        @code_node
        def add_five(total: int):
            return {"new_total": total + 5}

        @code_node
        def merge_totals(even_total=None, odd_total=None):
            # One branch executes, the other returns None
            return {"merged_total": even_total if even_total is not None else odd_total}

        with GraphNode(name="while_branch_graph") as graph:
            with WhileLoopNode(
                name="main_loop",
                inputs={"counter": 0, "total": 0},
                stop_condition="total >= 50",
                max_iterations=20,
            ) as loop:
                # Increment counter first
                inc = increment_counter(inputs={"counter": PARENT["counter"]})
                inc["new_counter"] >> PARENT["counter"]

                # Branch based on counter parity using new fluent syntax
                branch = (
                    Branch("parity_check")
                    .if_(inc["new_counter"].apply(lambda x: x % 2 == 0), "even_node")
                    .else_("odd_node")
                )

                # Even path: add 10
                even_node = add_ten(inputs={"total": PARENT["total"]})

                # Odd path: add 5
                odd_node = add_five(inputs={"total": PARENT["total"]})

                # Merge to update total using new >> syntax
                # Both branches output to separate inputs, merge picks the one that executed
                merge = merge_totals(
                    inputs={
                        "even_total": even_node["new_total"],
                        "odd_total": odd_node["new_total"],
                    }
                )

                merge["merged_total"] >> PARENT["total"]

                # Note: Can't chain soft edges in single line due to Python's
                # comparison chaining (a > b > c becomes (a>b) and (b>c))
                START >> inc >> branch >> [even_node, odd_node] > merge
                merge >> END

            loop["total"] >> PARENT["final_total"]
            loop["counter"] >> PARENT["final_counter"]
            START >> loop >> END

        graph.build()
        schema = StateSchema(graph)
        state = schema.create_state(inputs={})

        result = await graph.run(state)

        # counter: 1(odd,+5), 2(even,+10), 3(odd,+5), 4(even,+10), 5(odd,+5), 6(even,+10), 7(odd,+5)
        # total: 5->15->20->30->35->45->50
        assert result["final_total"] >= 50

    @pytest.mark.asyncio
    async def test_for_loop_with_while_loop_inside(self):
        """ForLoop iterating items, with WhileLoop processing each item.

        ForLoop: iterate over [10, 20, 30]
        WhileLoop: for each item, divide by 2 until < 5
        """
        from hush.core.nodes.iteration.base import Each
        from hush.core.nodes.iteration.for_loop_node import ForLoopNode
        from hush.core.nodes.iteration.while_loop_node import WhileLoopNode

        @code_node
        def halve(value: int):
            return {"new_value": value // 2}

        with GraphNode(name="forloop_whileloop_graph") as graph:
            with ForLoopNode(name="outer_for", inputs={"item": Each([10, 20, 30])}) as for_loop:
                with WhileLoopNode(
                    name="inner_while",
                    inputs={"value": PARENT["item"]},
                    stop_condition="value < 5",
                    max_iterations=20,
                ) as while_loop:
                    halve_node = halve(inputs={"value": PARENT["value"]})
                    halve_node["new_value"] >> PARENT["value"]
                    START >> halve_node >> END

                while_loop["value"] >> PARENT["value"]
                START >> while_loop >> END

            for_loop["value"] >> PARENT["results"]
            START >> for_loop >> END

        graph.build()
        schema = StateSchema(graph)
        state = schema.create_state(inputs={})

        result = await graph.run(state)

        # 10 -> 5 -> 2 (stops at 2)
        # 20 -> 10 -> 5 -> 2 (stops at 2)
        # 30 -> 15 -> 7 -> 3 (stops at 3)
        expected = [2, 2, 3]
        assert result["results"] == expected

    @pytest.mark.asyncio
    async def test_full_pipeline_with_all_node_types(self):
        """Complex pipeline: Input -> Transform -> Branch -> ForLoop/WhileLoop -> Aggregate.

        1. CodeNode: Parse input and determine processing mode
        2. BranchNode: Route based on mode
        3. ForLoopNode: Batch processing path
        4. WhileLoopNode: Iterative processing path
        5. CodeNode: Aggregate results
        """
        from hush.core.nodes.flow.branch_node import Branch
        from hush.core.nodes.iteration.base import Each
        from hush.core.nodes.iteration.for_loop_node import ForLoopNode
        from hush.core.nodes.iteration.while_loop_node import WhileLoopNode

        @code_node
        def parse_input(raw_input: dict):
            return {
                "mode": raw_input.get("mode", "batch"),
                "items": raw_input.get("items", []),
                "target": raw_input.get("target", 100),
            }

        @code_node
        def process_item(item: int, multiplier: int):
            return {"processed": item * multiplier}

        @code_node
        def iterative_accumulate(current: int, step: int):
            return {"new_current": current + step}

        @code_node
        def aggregate_results(batch_result=None, iterative_result=None):
            # One of these will be None depending on branch
            if batch_result is not None:
                return {
                    "final": sum(batch_result) if isinstance(batch_result, list) else batch_result
                }
            return {"final": iterative_result}

        with GraphNode(name="full_pipeline") as graph:
            # Step 1: Parse input
            parser = parse_input(inputs={"raw_input": PARENT["input"]})

            # Step 2: Branch based on mode using new fluent syntax
            router = (
                Branch("mode_router")
                .if_(parser["mode"] == "batch", "batch_process")
                .else_("iterative_process")
            )

            # Step 3a: Batch processing with ForLoop
            with ForLoopNode(
                name="batch_process", inputs={"item": Each(parser["items"]), "multiplier": 2}
            ) as batch_loop:
                batch_node = process_item(
                    inputs={"item": PARENT["item"], "multiplier": PARENT["multiplier"]}
                )
                batch_node["processed"] >> PARENT["processed"]
                START >> batch_node >> END

            # Step 3b: Iterative processing with WhileLoop
            with WhileLoopNode(
                name="iterative_process",
                inputs={"current": 0, "step": 10, "target": parser["target"]},
                stop_condition="current >= target",
                max_iterations=50,
            ) as iter_loop:
                iter_node = iterative_accumulate(
                    inputs={"current": PARENT["current"], "step": PARENT["step"]}
                )
                iter_node["new_current"] >> PARENT["current"]
                START >> iter_node >> END

            # Step 4: Aggregate using new >> syntax
            aggregator = aggregate_results(
                inputs={
                    "batch_result": batch_loop["processed"],
                    "iterative_result": iter_loop["current"],
                }
            )

            aggregator["final"] >> PARENT["final"]

            # Wire up the graph
            START >> parser >> router >> [batch_loop, iter_loop] > aggregator
            aggregator >> END

        graph.build()

        # Test batch mode
        schema = StateSchema(graph)
        state1 = schema.create_state(inputs={"input": {"mode": "batch", "items": [1, 2, 3, 4, 5]}})
        result1 = await graph.run(state1)
        # Batch: [1*2, 2*2, 3*2, 4*2, 5*2] = [2, 4, 6, 8, 10], sum = 30
        assert result1["final"] == 30

        # Test iterative mode
        state2 = schema.create_state(inputs={"input": {"mode": "iterative", "target": 50}})
        result2 = await graph.run(state2)
        # Iterative: 0->10->20->30->40->50, final = 50
        assert result2["final"] == 50

    @pytest.mark.asyncio
    async def test_parallel_branches_with_different_loop_types(self):
        """Parallel branches: one uses ForLoop, other uses WhileLoop, then merge.

        Input: {"numbers": [1,2,3], "target": 20}
        - Branch A (ForLoop): Sum all numbers * 2
        - Branch B (WhileLoop): Count up to target by 5s
        - Merge: Return both results
        """
        from hush.core.nodes.iteration.base import Each
        from hush.core.nodes.iteration.for_loop_node import ForLoopNode
        from hush.core.nodes.iteration.while_loop_node import WhileLoopNode

        @code_node
        def double(value: int):
            return {"doubled": value * 2}

        @code_node
        def count_step(counter: int):
            return {"new_counter": counter + 5}

        @code_node
        def merge_results(for_result, while_result):
            total_doubled = sum(for_result) if for_result else 0
            return {"for_sum": total_doubled, "while_count": while_result}

        with GraphNode(name="parallel_loops") as graph:
            # Parallel ForLoop
            with ForLoopNode(
                name="double_loop", inputs={"value": Each(PARENT["numbers"])}
            ) as for_loop:
                double_node = double(inputs={"value": PARENT["value"]})
                double_node["doubled"] >> PARENT["doubled"]
                START >> double_node >> END

            # Parallel WhileLoop
            with WhileLoopNode(
                name="count_loop",
                inputs={"counter": 0, "target": PARENT["target"]},
                stop_condition="counter >= target",
                max_iterations=20,
            ) as while_loop:
                count_node = count_step(inputs={"counter": PARENT["counter"]})
                count_node["new_counter"] >> PARENT["counter"]
                START >> count_node >> END

            # Merge both results using new >> syntax
            merger = merge_results(inputs={})
            for_loop["doubled"] >> merger["for_result"]
            while_loop["counter"] >> merger["while_result"]
            merger["for_sum"] >> PARENT["for_sum"]
            merger["while_count"] >> PARENT["while_count"]

            START >> [for_loop, while_loop] >> merger >> END

        graph.build()
        schema = StateSchema(graph)
        state = schema.create_state(inputs={"numbers": [1, 2, 3, 4, 5], "target": 20})

        result = await graph.run(state)

        # ForLoop: [2, 4, 6, 8, 10], sum = 30
        assert result["for_sum"] == 30
        # WhileLoop: 0->5->10->15->20, final = 20
        assert result["while_count"] == 20

    @pytest.mark.asyncio
    async def test_nested_graph_with_all_types(self):
        """Nested GraphNode containing Branch, ForLoop, and WhileLoop.

        Outer graph calls inner graph which has all node types.
        """
        from hush.core.nodes.flow.branch_node import Branch
        from hush.core.nodes.iteration.base import Each
        from hush.core.nodes.iteration.for_loop_node import ForLoopNode
        from hush.core.nodes.iteration.while_loop_node import WhileLoopNode

        @code_node
        def prepare_data(x: int):
            return {"should_loop": x > 5, "items": list(range(1, x + 1)), "limit": x * 2}

        @code_node
        def square(value: int):
            return {"squared": value * value}

        @code_node
        def increment(counter: int):
            return {"new_counter": counter + 1}

        # Outer graph containing inner graph with all node types
        with GraphNode(name="outer_graph") as outer_graph:
            # Inner graph with all node types (nested inside outer_graph)
            with GraphNode(
                name="inner_processor", inputs={"x": PARENT["input_value"]}
            ) as inner_graph:
                prep = prepare_data(inputs={"x": PARENT["x"]})

                # Branch using new fluent syntax
                branch = (
                    Branch("process_router")
                    .if_(prep["should_loop"] == True, "for_process")
                    .else_("while_process")
                )

                # ForLoop path
                with ForLoopNode(
                    name="for_process", inputs={"value": Each(prep["items"])}
                ) as for_loop:
                    sq = square(inputs={"value": PARENT["value"]})
                    sq["squared"] >> PARENT["squared"]
                    START >> sq >> END

                # WhileLoop path
                with WhileLoopNode(
                    name="while_process",
                    inputs={"counter": 0, "limit": prep["limit"]},
                    stop_condition="counter >= limit",
                    max_iterations=50,
                ) as while_loop:
                    inc = increment(inputs={"counter": PARENT["counter"]})
                    inc["new_counter"] >> PARENT["counter"]
                    START >> inc >> END

                # Collect results using new >> syntax
                collector = CodeNode(
                    name="collector",
                    code_fn=lambda for_result=None, while_result=None: {
                        "result": sum(for_result) if for_result else while_result
                    },
                    inputs={
                        "for_result": for_loop["squared"],
                        "while_result": while_loop["counter"],
                    },
                )

                collector["result"] >> PARENT["result"]

                START >> prep >> branch >> [for_loop, while_loop] > collector
                collector >> END

            # Wire inner graph to outer graph
            inner_graph["result"] >> PARENT["result"]
            START >> inner_graph >> END

        outer_graph.build()

        # Test with x=10 (should_loop=True, use ForLoop)
        schema = StateSchema(outer_graph)
        state1 = schema.create_state(inputs={"input_value": 10})
        result1 = await outer_graph.run(state1)
        # ForLoop: squares of 1..10 = 1+4+9+16+25+36+49+64+81+100 = 385
        assert result1["result"] == 385

        # Test with x=3 (should_loop=False, use WhileLoop)
        state2 = schema.create_state(inputs={"input_value": 3})
        result2 = await outer_graph.run(state2)
        # WhileLoop: count to limit=6, result = 6
        assert result2["result"] == 6


# ============================================================
# Test: Graph Validation System
# ============================================================


class TestGraphValidation:
    """Test comprehensive graph validation system."""

    # ----------------------------------------------------------
    # Branch Target Validation Tests
    # ----------------------------------------------------------

    @pytest.mark.asyncio
    async def test_invalid_branch_target_raises_error(self):
        """Branch node with non-existent target should raise GraphValidationError."""
        from hush.core.nodes.flow.branch_node import Branch
        from hush.core.nodes.graph.graph_node import GraphValidationError

        with GraphNode(name="invalid_branch_graph") as graph:
            router = (
                Branch("router")
                .if_(PARENT["condition"] == True, "non_existent_target")
                .else_("also_non_existent")
            )

            START >> router >> END

        with pytest.raises(GraphValidationError) as exc_info:
            graph.build()

        error = exc_info.value
        assert error.result.has_errors
        assert len(error.result.errors) >= 1
        # Check validation result contains the invalid target name
        assert any("non_existent_target" in issue.message for issue in error.result.errors)

    @pytest.mark.asyncio
    async def test_valid_branch_targets_pass(self):
        """Branch node with valid targets should pass validation."""
        from hush.core.nodes.flow.branch_node import Branch

        @code_node
        def process_a(x: int):
            return {"result": x * 2}

        @code_node
        def process_b(x: int):
            return {"result": x + 10}

        with GraphNode(name="valid_branch_graph") as graph:
            router = (
                Branch("router").if_(PARENT["condition"] == True, "process_a").else_("process_b")
            )

            node_a = process_a(name="process_a", inputs={"x": PARENT["x"]}, outputs={"*": PARENT})
            node_b = process_b(name="process_b", inputs={"x": PARENT["x"]}, outputs={"*": PARENT})

            START >> router >> [node_a, node_b] >> END

        # Should not raise
        graph.build()

        schema = StateSchema(graph)
        state = schema.create_state(inputs={"x": 5, "condition": True})
        result = await graph.run(state)
        assert result["result"] == 10  # process_a: 5 * 2

    @pytest.mark.asyncio
    async def test_branch_with_node_reference_instead_of_string(self):
        """Branch can use node reference directly instead of string name."""
        from hush.core.nodes.flow.branch_node import Branch

        @code_node
        def handler_a(x: int):
            return {"result": "path_a"}

        @code_node
        def handler_b(x: int):
            return {"result": "path_b"}

        with GraphNode(name="node_ref_branch") as graph:
            node_a = handler_a(name="handler_a", inputs={"x": PARENT["x"]})
            node_b = handler_b(name="handler_b", inputs={"x": PARENT["x"]})

            # Using node references directly (recommended approach)
            router = (
                Branch("router")
                .if_(PARENT["choice"] == "a", node_a)  # node reference
                .else_(node_b)
            )

            node_a["result"] >> PARENT["result"]
            node_b["result"] >> PARENT["result"]

            START >> router >> [node_a, node_b] >> END

        graph.build()  # Should not raise

        schema = StateSchema(graph)
        state = schema.create_state(inputs={"x": 1, "choice": "a"})
        result = await graph.run(state)
        assert result["result"] == "path_a"

    # ----------------------------------------------------------
    # Cycle Detection Tests
    # ----------------------------------------------------------

    @pytest.mark.asyncio
    async def test_cycle_detection_produces_warning(self):
        """Cycle in graph should produce warning but not error."""

        with GraphNode(name="cycle_graph") as graph:
            node_a = CodeNode(
                name="node_a",
                code_fn=lambda x: {"result": x + 1},
                inputs={"x": PARENT["x"]},
            )
            node_b = CodeNode(
                name="node_b",
                code_fn=lambda x: {"result": x * 2},
                inputs={"x": node_a["result"]},
            )

            # Create intentional cycle for testing
            START >> node_a >> node_b >> END
            graph.add_edge("node_b", "node_a")  # Creates cycle: a -> b -> a

        # Validate without building
        result = graph.validate()

        # Should have warning about cycle but no errors
        cycle_warnings = [
            w for w in result.warnings if "Cycle" in w.category or "cycle" in w.message.lower()
        ]
        assert len(cycle_warnings) >= 1
        # Build should still work (cycles are warnings, not errors)
        graph.build()

    @pytest.mark.asyncio
    async def test_no_cycle_no_warning(self):
        """Linear graph should have no cycle warnings."""
        with GraphNode(name="linear_graph") as graph:
            node_a = CodeNode(
                name="a",
                code_fn=lambda x: {"y": x + 1},
                inputs={"x": PARENT["x"]},
            )
            node_b = CodeNode(
                name="b",
                code_fn=lambda y: {"z": y * 2},
                inputs={"y": node_a["y"]},
            )
            node_c = CodeNode(
                name="c",
                code_fn=lambda z: {"result": z - 1},
                inputs={"z": node_b["z"]},
                outputs={"*": PARENT},
            )

            START >> node_a >> node_b >> node_c >> END

        result = graph.validate()
        cycle_warnings = [w for w in result.warnings if "Cycle" in w.category]
        assert len(cycle_warnings) == 0

    # ----------------------------------------------------------
    # Reachability Validation Tests
    # ----------------------------------------------------------

    @pytest.mark.asyncio
    async def test_unreachable_node_warning(self):
        """Node not connected to START should produce warning."""
        with GraphNode(name="unreachable_graph") as graph:
            connected = CodeNode(
                name="connected", code_fn=lambda: {"result": 1}, outputs={"*": PARENT}
            )
            disconnected = CodeNode(
                name="disconnected",
                code_fn=lambda: {"other": 2},
            )

            START >> connected >> END
            # disconnected is added to graph but not connected to START

        result = graph.validate()

        unreachable_warnings = [
            w
            for w in result.warnings
            if "Unreachable" in w.category or "unreachable" in w.message.lower()
        ]
        assert len(unreachable_warnings) >= 1
        assert "disconnected" in str(unreachable_warnings[0])

    @pytest.mark.asyncio
    async def test_dead_end_node_warning(self):
        """Node that cannot reach END should produce warning."""
        with GraphNode(name="dead_end_graph") as graph:
            node_a = CodeNode(
                name="a",
                code_fn=lambda: {"x": 1},
            )
            node_b = CodeNode(name="b", code_fn=lambda: {"y": 2}, outputs={"*": PARENT})
            dead_end = CodeNode(
                name="dead_end",
                code_fn=lambda: {"z": 3},
            )

            START >> node_a >> [node_b, dead_end]
            node_b >> END
            # dead_end has no path to END

        result = graph.validate()

        dead_end_warnings = [
            w
            for w in result.warnings
            if "Dead-end" in w.category or "dead-end" in w.message.lower()
        ]
        assert len(dead_end_warnings) >= 1
        assert "dead_end" in str(dead_end_warnings[0])

    @pytest.mark.asyncio
    async def test_fully_connected_graph_no_reachability_warnings(self):
        """Fully connected graph should have no reachability warnings."""
        with GraphNode(name="connected_graph") as graph:
            a = CodeNode(name="a", code_fn=lambda: {"x": 1})
            b = CodeNode(name="b", code_fn=lambda x: {"y": x * 2}, inputs={"x": a["x"]})
            c = CodeNode(
                name="c",
                code_fn=lambda y: {"result": y + 1},
                inputs={"y": b["y"]},
                outputs={"*": PARENT},
            )

            START >> a >> b >> c >> END

        result = graph.validate()

        reachability_warnings = [
            w for w in result.warnings if "Unreachable" in w.category or "Dead-end" in w.category
        ]
        assert len(reachability_warnings) == 0

    # ----------------------------------------------------------
    # Ref Validation Tests
    # ----------------------------------------------------------

    @pytest.mark.asyncio
    async def test_invalid_ref_raises_error(self):
        """Ref to non-existent node should raise GraphValidationError."""
        from hush.core.nodes.graph.graph_node import GraphValidationError
        from hush.core.states.ref import Ref

        # Create a fake node that doesn't exist in graph
        class FakeNode:
            name = "non_existent_node"

        fake = FakeNode()

        with GraphNode(name="invalid_ref_graph") as graph:
            node = CodeNode(
                name="consumer",
                code_fn=lambda x: {"result": x},
                inputs={"x": Ref(fake, "output")},  # Ref to non-existent node
                outputs={"*": PARENT},
            )

            START >> node >> END

        with pytest.raises(GraphValidationError) as exc_info:
            graph.build()

        error = exc_info.value
        assert error.result.has_errors
        assert any("non_existent_node" in issue.message for issue in error.result.errors)

    @pytest.mark.asyncio
    async def test_parent_ref_is_valid(self):
        """Ref to PARENT should always be valid."""
        with GraphNode(name="parent_ref_graph") as graph:
            node = CodeNode(
                name="processor",
                code_fn=lambda x: {"result": x * 2},
                inputs={"x": PARENT["input_value"]},
                outputs={"*": PARENT},
            )

            START >> node >> END

        # Should not raise
        graph.build()

        schema = StateSchema(graph)
        state = schema.create_state(inputs={"input_value": 5})
        result = await graph.run(state)
        assert result["result"] == 10

    @pytest.mark.asyncio
    async def test_valid_node_ref(self):
        """Ref to existing node should be valid."""
        with GraphNode(name="valid_ref_graph") as graph:
            producer = CodeNode(
                name="producer",
                code_fn=lambda: {"data": 42},
            )
            consumer = CodeNode(
                name="consumer",
                code_fn=lambda x: {"result": x * 2},
                inputs={"x": producer["data"]},
                outputs={"*": PARENT},
            )

            START >> producer >> consumer >> END

        graph.build()

        schema = StateSchema(graph)
        state = schema.create_state()
        result = await graph.run(state)
        assert result["result"] == 84

    # ----------------------------------------------------------
    # Auto-Build Tests
    # ----------------------------------------------------------

    @pytest.mark.asyncio
    async def test_auto_build_on_call(self):
        """__call__ should auto-build unbuilt GraphNode."""
        with GraphNode(name="auto_build_graph") as graph:
            node = CodeNode(
                name="doubler",
                code_fn=lambda x: {"result": x * 2},
                inputs={"x": PARENT["x"]},
                outputs={"*": PARENT},
            )
            START >> node >> END

        # Don't call build() explicitly
        assert graph._is_building == True

        # __call__ should auto-build
        result = graph(x=5)

        assert graph._is_building == False
        assert result["result"] == 10

    @pytest.mark.asyncio
    async def test_auto_build_catches_validation_errors(self):
        """Auto-build should catch validation errors."""
        from hush.core.nodes.flow.branch_node import Branch
        from hush.core.nodes.graph.graph_node import GraphValidationError

        with GraphNode(name="invalid_auto_build") as graph:
            router = Branch("bad_router").if_(PARENT["x"] > 0, "missing_node").else_("also_missing")

            START >> router >> END

        # __call__ will auto-build and should catch the error
        with pytest.raises(GraphValidationError):
            graph(x=5)

    # ----------------------------------------------------------
    # ValidationResult Tests
    # ----------------------------------------------------------

    def test_validation_result_properties(self):
        """Test ValidationResult properties work correctly."""
        from hush.core.nodes.graph.graph_node import (
            ValidationIssue,
            ValidationLevel,
            ValidationResult,
        )

        result = ValidationResult(graph_name="test_graph")

        # Empty result
        assert not result.has_errors
        assert not result.has_warnings
        assert result.errors == []
        assert result.warnings == []

        # Add warning
        result.issues.append(
            ValidationIssue(level=ValidationLevel.WARNING, category="Test", message="Test warning")
        )
        assert not result.has_errors
        assert result.has_warnings
        assert len(result.warnings) == 1

        # Add error
        result.issues.append(
            ValidationIssue(level=ValidationLevel.ERROR, category="Test", message="Test error")
        )
        assert result.has_errors
        assert result.has_warnings
        assert len(result.errors) == 1
        assert len(result.warnings) == 1

    def test_validation_issue_str_format(self):
        """Test ValidationIssue string formatting."""
        from hush.core.nodes.graph.graph_node import ValidationIssue, ValidationLevel

        issue = ValidationIssue(
            level=ValidationLevel.ERROR,
            category="Invalid branch target",
            message="Branch 'router' references non-existent target 'missing'",
            node_name="router",
            target_name="missing",
            available_nodes=["node_a", "node_b", "node_c"],
            suggestions=["Check node name spelling", "Use node reference"],
        )

        issue_str = str(issue)

        assert "[ERROR]" in issue_str
        assert "Invalid branch target" in issue_str
        assert "router" in issue_str
        assert "missing" in issue_str
        assert "Available nodes" in issue_str
        assert "How to fix" in issue_str

    def test_raise_if_errors(self):
        """Test raise_if_errors method."""
        from hush.core.nodes.graph.graph_node import (
            GraphValidationError,
            ValidationIssue,
            ValidationLevel,
            ValidationResult,
        )

        result = ValidationResult(graph_name="test")

        # No errors - should not raise
        result.raise_if_errors()

        # Add warning only - should not raise
        result.issues.append(
            ValidationIssue(level=ValidationLevel.WARNING, category="Test", message="Warning")
        )
        result.raise_if_errors()

        # Add error - should raise
        result.issues.append(
            ValidationIssue(level=ValidationLevel.ERROR, category="Test", message="Error")
        )

        with pytest.raises(GraphValidationError):
            result.raise_if_errors()

    # ----------------------------------------------------------
    # Combined Validation Tests
    # ----------------------------------------------------------

    @pytest.mark.asyncio
    async def test_multiple_validation_issues(self):
        """Graph with multiple issues should report all of them."""
        from hush.core.nodes.flow.branch_node import Branch
        from hush.core.nodes.graph.graph_node import GraphValidationError

        with GraphNode(name="multi_issue_graph") as graph:
            # Issue 1: Branch with invalid target
            router = Branch("router").if_(PARENT["x"] > 0, "missing_target").else_("valid_node")

            # Issue 2: Valid node but will also be checked
            valid_node = CodeNode(
                name="valid_node", code_fn=lambda: {"result": 1}, outputs={"*": PARENT}
            )

            START >> router >> [valid_node] >> END

        with pytest.raises(GraphValidationError) as exc_info:
            graph.build()

        error = exc_info.value
        # Should have at least the branch target error
        assert len(error.result.errors) >= 1
        assert any("missing_target" in issue.message for issue in error.result.errors)


# ============================================================
# Run tests with pytest
# ============================================================

if __name__ == "__main__":
    pytest.main([__file__, "-v"])
