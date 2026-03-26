"""Test suite for GraphOp - from simple to complex scenarios.

Only tests GraphOp and FuncOp. Flow nodes (BranchOp, etc.)
are tested separately.
"""

import asyncio

import pytest

from hush.core import (
    END,
    PARENT,
    START,
    FuncOp,
    GraphOp,
    StateSchema,
    op,
)

# ============================================================
# Test 1: Single Node Graph
# ============================================================


class TestSingleNodeGraph:
    """Test graphs with a single node."""

    @pytest.mark.asyncio
    async def test_single_func_op(self):
        """Single FuncOp that doubles a value."""
        with GraphOp(name="single_node_graph") as graph:
            node = FuncOp(
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
        """Single node using @op decorator."""

        @op
        def triple(x: int):
            return {"result": x * 3}

        with GraphOp(name="decorator_graph") as graph:
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
        with GraphOp(name="no_output_map") as graph:
            node = FuncOp(
                name="compute", code_fn=lambda x: {"result": x + 100}, inputs={"x": PARENT["x"]}
            )
            START >> node >> END

        graph.build()

        schema = StateSchema(graph)
        state = schema.create_state(inputs={"x": 5})
        await graph.run(state)

        # Access result directly from state
        assert state["no_output_map.compute", "result"] == 105


# ============================================================
# Test 2: Linear Graph (A -> B -> C)
# ============================================================


class TestLinearGraph:
    """Test linear sequential graphs."""

    @pytest.mark.asyncio
    async def test_two_nodes_linear(self):
        """Two nodes in sequence: add then multiply."""
        with GraphOp(name="two_node_linear") as graph:
            node_a = FuncOp(
                name="add_10", code_fn=lambda x: {"result": x + 10}, inputs={"x": PARENT["x"]}
            )
            node_b = FuncOp(
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
        with GraphOp(name="three_node_linear") as graph:
            node_a = FuncOp(
                name="add_10", code_fn=lambda x: {"result": x + 10}, inputs={"x": PARENT["x"]}
            )
            node_b = FuncOp(
                name="multiply_2",
                code_fn=lambda x: {"result": x * 2},
                inputs={"x": node_a["result"]},
            )
            node_c = FuncOp(
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
        with GraphOp(name="long_chain") as graph:
            n1 = FuncOp(name="n1", code_fn=lambda x: {"v": x + 1}, inputs={"x": PARENT["x"]})
            n2 = FuncOp(name="n2", code_fn=lambda x: {"v": x + 2}, inputs={"x": n1["v"]})
            n3 = FuncOp(name="n3", code_fn=lambda x: {"v": x + 3}, inputs={"x": n2["v"]})
            n4 = FuncOp(name="n4", code_fn=lambda x: {"v": x + 4}, inputs={"x": n3["v"]})
            n5 = FuncOp(
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
        with GraphOp(name="fork_merge") as graph:
            start = FuncOp(name="start", code_fn=lambda x: {"value": x}, inputs={"x": PARENT["x"]})
            branch_a = FuncOp(
                name="branch_a", code_fn=lambda x: {"result": x * 2}, inputs={"x": start["value"]}
            )
            branch_b = FuncOp(
                name="branch_b", code_fn=lambda x: {"result": x * 3}, inputs={"x": start["value"]}
            )
            merge = FuncOp(
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
        with GraphOp(name="three_way_fork") as graph:
            start = FuncOp(name="start", code_fn=lambda x: {"value": x}, inputs={"x": PARENT["x"]})
            b1 = FuncOp(name="b1", code_fn=lambda x: {"r": x * 2}, inputs={"x": start["value"]})
            b2 = FuncOp(name="b2", code_fn=lambda x: {"r": x * 3}, inputs={"x": start["value"]})
            b3 = FuncOp(name="b3", code_fn=lambda x: {"r": x * 4}, inputs={"x": start["value"]})

            merge = FuncOp(
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
        with GraphOp(name="diamond") as graph:
            a = FuncOp(name="a", code_fn=lambda x: {"out": x}, inputs={"x": PARENT["x"]})
            b = FuncOp(name="b", code_fn=lambda x: {"out": x + 100}, inputs={"x": a["out"]})
            c = FuncOp(name="c", code_fn=lambda x: {"out": x + 200}, inputs={"x": a["out"]})
            d = FuncOp(
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
        with GraphOp(name="parallel_independent") as graph:
            # Two independent branches from input
            branch_a = FuncOp(
                name="branch_a", code_fn=lambda x: {"result": x * 10}, inputs={"x": PARENT["x"]}
            )
            branch_b = FuncOp(
                name="branch_b", code_fn=lambda y: {"result": y + 5}, inputs={"y": PARENT["y"]}
            )
            merge = FuncOp(
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
        with GraphOp(name="multi_input") as graph:
            node = FuncOp(
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
        with GraphOp(name="multi_output") as graph:
            node = FuncOp(
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
        with GraphOp(name="selective_output") as graph:
            node = FuncOp(
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
        with GraphOp(name="dict_graph") as graph:
            node = FuncOp(
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
        with GraphOp(name="list_graph") as graph:
            double = FuncOp(
                name="double",
                code_fn=lambda items: {"result": [x * 2 for x in items]},
                inputs={"items": PARENT["items"]},
            )
            sum_all = FuncOp(
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
        with GraphOp(name="string_graph") as graph:
            upper = FuncOp(
                name="upper",
                code_fn=lambda text: {"result": text.upper()},
                inputs={"text": PARENT["text"]},
            )
            reverse = FuncOp(
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

        with GraphOp(name="async_single") as graph:
            # Note: return_keys needed because inspect.getsource() has trouble
            # with functions defined inside other functions
            node = FuncOp(
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

        with GraphOp(name="async_pipeline") as graph:
            add = FuncOp(
                name="add", code_fn=async_add, return_keys=["result"], inputs={"x": PARENT["x"]}
            )
            mult = FuncOp(
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

        with GraphOp(name="async_parallel") as graph:
            start = FuncOp(name="start", code_fn=lambda x: {"value": x}, inputs={"x": PARENT["x"]})
            a = FuncOp(
                name="a", code_fn=slow_a, inputs={"x": start["value"]}, outputs={"result": None}
            )
            b = FuncOp(
                name="b", code_fn=slow_b, inputs={"x": start["value"]}, outputs={"result": None}
            )
            merge = FuncOp(
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

        with GraphOp(name="error_graph") as graph:
            node = FuncOp(
                name="failing", code_fn=failing_fn, inputs={"x": PARENT["x"]}, outputs={"*": PARENT}
            )
            START >> node >> END

        graph.build()

        schema = StateSchema(graph)
        state = schema.create_state(inputs={"x": 5})
        await graph.run(state)

        # Check error is captured
        error = state["error_graph.failing", "error"]
        assert "Intentional error" in error

    @pytest.mark.asyncio
    async def test_partial_pipeline_error(self):
        """Error in middle of pipeline."""
        with GraphOp(name="partial_error") as graph:
            first = FuncOp(
                name="first", code_fn=lambda x: {"result": x + 10}, inputs={"x": PARENT["x"]}
            )
            failing = FuncOp(
                name="failing",
                code_fn=lambda x: 1 / 0,  # Division by zero
                inputs={"x": first["result"]},
            )
            last = FuncOp(
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
        assert state["partial_error.first", "result"] == 15

        # Failing node should have error
        error = state["partial_error.failing", "error"]
        assert "division by zero" in error


# ============================================================
# Test 8: Edge Cases
# ============================================================


class TestEdgeCases:
    """Test edge cases and boundary conditions."""

    @pytest.mark.asyncio
    async def test_empty_input(self):
        """Handle empty string inputs gracefully."""
        with GraphOp(name="empty_input") as graph:
            node = FuncOp(
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
        with GraphOp(name="large_data") as graph:
            node = FuncOp(
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
        with GraphOp(name="unicode_graph") as graph:
            node = FuncOp(
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
        with GraphOp(name="zero_negative") as graph:
            node = FuncOp(
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
# Test 9: @op Decorator
# ============================================================


class TestFuncOpDecorator:
    """Test the @op decorator."""

    @pytest.mark.asyncio
    async def test_decorator_basic(self):
        """Basic decorator usage."""

        @op
        def add_one(x: int):
            return {"result": x + 1}

        with GraphOp(name="decorator_basic") as graph:
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

        @op
        def add(x: int, amount: int = 10):
            return {"result": x + amount}

        with GraphOp(name="decorator_defaults") as graph:
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

        @op
        def step1(x: int):
            return {"value": x * 2}

        @op
        def step2(x: int):
            return {"value": x + 5}

        @op
        def step3(x: int):
            return {"result": x**2}

        with GraphOp(name="decorator_pipeline") as graph:
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

        with GraphOp(name="soft_any") as graph:
            b = FuncOp(
                name="b",
                code_fn=lambda: (execution_order.append("b"), {"result": "b"})[1],
                return_keys=["result"],
                inputs={},
            )
            c = FuncOp(
                name="c",
                code_fn=lambda: (execution_order.append("c"), {"result": "c"})[1],
                return_keys=["result"],
                inputs={},
            )
            d = FuncOp(
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
        assert graph.initial_ready_count["d"] == 1, (
            f"Expected ready_count[d]=1, got {graph.initial_ready_count['d']}"
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

        with GraphOp(name="mixed_edges") as graph:
            a = FuncOp(
                name="a",
                code_fn=lambda: (execution_order.append("a"), {"result": "a"})[1],
                return_keys=["result"],
                inputs={},
            )
            b = FuncOp(
                name="b",
                code_fn=lambda: (execution_order.append("b"), {"result": "b"})[1],
                return_keys=["result"],
                inputs={},
            )
            c = FuncOp(
                name="c",
                code_fn=lambda: (execution_order.append("c"), {"result": "c"})[1],
                return_keys=["result"],
                inputs={},
            )
            d = FuncOp(
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
        assert graph.initial_ready_count["d"] == 2, (
            f"Expected ready_count[d]=2, got {graph.initial_ready_count['d']}"
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
        with GraphOp(name="soft_count") as graph:
            b = FuncOp(name="b", code_fn=lambda: {"result": "b"}, inputs={})
            c = FuncOp(name="c", code_fn=lambda: {"result": "c"}, inputs={})
            d = FuncOp(
                name="d",
                code_fn=lambda b_done, c_done: {"combined": f"{b_done}+{c_done}"},
                inputs={"b_done": b["result"], "c_done": c["result"]},
                outputs={"*": PARENT},
            )

            # Using [b, c] >> ~d syntax for soft edges from multiple nodes
            START >> [b, c] >> d >> END

        graph.build()

        # D has ready_count = 2 (both hard edges counted)
        assert graph.initial_ready_count["d"] == 2

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

        with GraphOp(name="multi_hard") as graph:
            a = FuncOp(
                name="a",
                code_fn=lambda: (execution_order.append("a"), {"result": "a"})[1],
                return_keys=["result"],
                inputs={},
            )
            b = FuncOp(
                name="b",
                code_fn=lambda: (execution_order.append("b"), {"result": "b"})[1],
                return_keys=["result"],
                inputs={},
            )
            d = FuncOp(
                name="d",
                code_fn=lambda: (execution_order.append("d"), {"result": "d"})[1],
                return_keys=["result"],
                inputs={},
                outputs={"*": PARENT},
            )

            START >> [a, b] >> d >> END

        graph.build()

        # D has ready_count = 2 (both hard edges counted)
        assert graph.initial_ready_count["d"] == 2, (
            f"Expected ready_count[d]=2, got {graph.initial_ready_count['d']}"
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

        with GraphOp(name="complex_mixed") as graph:
            a = FuncOp(
                name="a",
                code_fn=lambda: (execution_order.append("a"), {"v": 1})[1],
                return_keys=["v"],
                inputs={},
            )
            b = FuncOp(
                name="b",
                code_fn=lambda: (execution_order.append("b"), {"v": 2})[1],
                return_keys=["v"],
                inputs={},
            )
            c = FuncOp(
                name="c",
                code_fn=lambda: (execution_order.append("c"), {"v": 3})[1],
                return_keys=["v"],
                inputs={},
            )
            e = FuncOp(
                name="e",
                code_fn=lambda a_v: (execution_order.append("e"), {"result_e": a_v * 10})[1],
                return_keys=["result_e"],
                inputs={"a_v": a["v"]},
            )
            f = FuncOp(
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
        assert graph.initial_ready_count["e"] == 2, (
            f"E should have ready_count=2 (1 hard + 1 soft group), got {graph.initial_ready_count['e']}"
        )
        assert graph.initial_ready_count["f"] == 2, (
            f"F should have ready_count=2 (2 hard), got {graph.initial_ready_count['f']}"
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

        with GraphOp(name="soft_delayed") as graph:
            a = FuncOp(name="a", code_fn=slow_a, return_keys=["result"], inputs={})
            b = FuncOp(name="b", code_fn=fast_b, return_keys=["result"], inputs={})
            d = FuncOp(
                name="d",
                code_fn=lambda: (execution_order.append("d"), {"result": "d"})[1],
                return_keys=["result"],
                inputs={},
                outputs={"*": PARENT},
            )

            START >> [a, b]
            a >> d  # Hard edge
            b >> ~d  # Soft edge
            d >> END

        graph.build()

        assert graph.initial_ready_count["d"] == 2  # 1 hard + 1 soft group

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
        with GraphOp(name="specific_mapping") as graph:
            node = FuncOp(
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
        with GraphOp(name="mixed_syntax") as graph:
            # Node 1 dùng cú pháp cũ
            node1 = FuncOp(
                name="node1",
                code_fn=lambda x: {"value": x * 2},
                inputs={"x": PARENT["x"]},
                outputs={"*": PARENT},  # Cú pháp cũ
            )
            # Node 2 dùng cú pháp mới
            node2 = FuncOp(
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
        with GraphOp(name="node_to_node") as graph:
            # node1 produces a value
            node1 = FuncOp(
                name="producer", code_fn=lambda x: {"result": x * 2}, inputs={"x": PARENT["x"]}
            )
            # node2 receives from node1 via >> syntax
            node2 = FuncOp(
                name="consumer",
                code_fn=lambda value: {"final": value + 100},
                inputs={},  # inputs will be set via >> syntax
            )
            # Map node1's "result" to node2's "value" input
            node1["result"] >> node2["value"]

            # node3 outputs to PARENT
            node3 = FuncOp(
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
        with GraphOp(name="multi_node_mapping") as graph:
            # Producer node creates multiple outputs
            producer = FuncOp(
                name="producer",
                code_fn=lambda x: {"a": x + 1, "b": x + 2},
                inputs={"x": PARENT["x"]},
            )
            # Consumer receives both outputs via >> syntax
            consumer = FuncOp(
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
        with GraphOp(name="chain_mapping") as graph:
            node_a = FuncOp(
                name="node_a", code_fn=lambda x: {"value": x + 10}, inputs={"x": PARENT["x"]}
            )
            node_b = FuncOp(name="node_b", code_fn=lambda inp: {"value": inp * 2}, inputs={})
            node_c = FuncOp(
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
        with GraphOp(name="mixed_mapping") as graph:
            producer = FuncOp(
                name="producer",
                code_fn=lambda x: {"a": x * 2, "b": x * 3},
                inputs={"x": PARENT["x"]},
            )
            consumer = FuncOp(
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
        with GraphOp(name="parallel_merge_mapping") as graph:
            branch_a = FuncOp(
                name="branch_a", code_fn=lambda x: {"result": x * 2}, inputs={"x": PARENT["x"]}
            )
            branch_b = FuncOp(
                name="branch_b", code_fn=lambda x: {"result": x * 3}, inputs={"x": PARENT["x"]}
            )
            merge = FuncOp(
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


class TestComplexGraphWithAllOpTypes:
    """Test complex graphs combining BranchOp and generator ops.

    These tests demonstrate real-world scenarios where multiple node types
    work together using generator-based iteration (replaces ForOp/WhileOp).
    """

    @pytest.mark.asyncio
    async def test_generator_iteration_with_new_syntax(self):
        """Test generator op with >> syntax for iteration."""

        @op
        def each_item(data: list):
            for item in data:
                yield {"value": item}

        @op
        def double_number(value: int):
            return {"result": value * 2}

        with GraphOp(name="gen_iter_graph") as graph:
            src = each_item(data=PARENT["data"])
            node = double_number(value=src["value"])
            START >> src >> node >> END

        graph.build()

        schema = StateSchema(graph)
        state = schema.create_state(inputs={"data": [1, 2, 3, 4, 5]})
        result = await graph.run(state)
        assert result["result"] == [2, 4, 6, 8, 10]

    @pytest.mark.asyncio
    async def test_nested_generators(self):
        """Nested generators: outer yields items, inner halves each until < 5."""

        @op
        def each_item(items: list):
            for item in items:
                yield {"item": item}

        @op
        def halve_until_small(value: int):
            while value >= 5:
                value = value // 2
            yield {"final_value": value}

        with GraphOp(name="nested_gen_graph") as graph:
            outer = each_item(items=PARENT["items"])
            inner = halve_until_small(value=outer["item"])
            START >> outer >> inner >> END

        graph.build()
        schema = StateSchema(graph)
        state = schema.create_state(inputs={"items": [10, 20, 30]})

        result = await graph.run(state)

        # 10 -> 5 -> 2 (final 2)
        # 20 -> 10 -> 5 -> 2 (final 2)
        # 30 -> 15 -> 7 -> 3 (final 3)
        assert result["final_value"] == [2, 2, 3]

    @pytest.mark.asyncio
    async def test_generator_with_conditional_processing(self):
        """Generator with conditional logic inside the generator itself.

        Generator yields numbers with even/odd conditional processing.
        """

        @op
        def process_items(limit: int):
            for i in range(1, limit + 1):
                if i % 2 == 0:
                    yield {"result": i + 10}
                else:
                    yield {"result": i + 5}

        with GraphOp(name="gen_cond_graph") as graph:
            src = process_items(limit=PARENT["limit"])
            START >> src >> END

        graph.build()
        schema = StateSchema(graph)
        state = schema.create_state(inputs={"limit": 4})

        result = await graph.run(state)
        # 1(odd,+5=6), 2(even,+10=12), 3(odd,+5=8), 4(even,+10=14)
        assert result["result"] == [6, 12, 8, 14]

    @pytest.mark.asyncio
    async def test_generator_with_downstream_chain(self):
        """Generator with chain of downstream ops per yield.

        Generator yields items, each goes through add >> multiply chain.
        """

        @op
        def each_number(numbers: list):
            for n in numbers:
                yield {"value": n}

        @op
        def add_ten(value: int):
            return {"added": value + 10}

        @op
        def double(added: int):
            return {"result": added * 2}

        with GraphOp(name="gen_chain") as graph:
            src = each_number(numbers=PARENT["numbers"])
            a = add_ten(value=src["value"])
            d = double(added=a["added"])
            START >> src >> a >> d >> END

        graph.build()
        schema = StateSchema(graph)
        state = schema.create_state(inputs={"numbers": [1, 2, 3, 4, 5]})

        result = await graph.run(state)

        # (1+10)*2=22, (2+10)*2=24, (3+10)*2=26, (4+10)*2=28, (5+10)*2=30
        assert result["result"] == [22, 24, 26, 28, 30]

    @pytest.mark.asyncio
    async def test_nested_graph_with_generator(self):
        """Nested GraphOp containing generator ops.

        Outer graph calls inner graph which uses generators for iteration.
        """

        @op
        def prepare_data(x: int):
            return {"items": list(range(1, x + 1))}

        @op
        def square_items(items: list):
            for item in items:
                yield {"squared": item * item}

        with GraphOp(name="outer_graph") as outer_graph:
            with GraphOp(
                name="inner_processor", inputs={"x": PARENT["input_value"]}
            ) as inner_graph:
                prep = prepare_data(inputs={"x": PARENT["x"]})
                gen = square_items(items=prep["items"])
                START >> prep >> gen >> END

            START >> inner_graph >> END

        outer_graph.build()

        # Test with x=5
        schema = StateSchema(outer_graph)
        state = schema.create_state(inputs={"input_value": 5})
        result = await outer_graph.run(state)
        # squares of 1..5 = [1, 4, 9, 16, 25]
        assert result["squared"] == [1, 4, 9, 16, 25]


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
        from hush.core.ops.flow.branch_op import Branch
        from hush.core.ops.graph.graph_op import GraphValidationError

        with GraphOp(name="invalid_branch_graph") as graph:
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
        from hush.core.ops.flow.branch_op import Branch

        @op
        def process_a(x: int):
            return {"result": x * 2}

        @op
        def process_b(x: int):
            return {"result": x + 10}

        with GraphOp(name="valid_branch_graph") as graph:
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
        from hush.core.ops.flow.branch_op import Branch

        @op
        def handler_a(x: int):
            return {"result": "path_a"}

        @op
        def handler_b(x: int):
            return {"result": "path_b"}

        with GraphOp(name="node_ref_branch") as graph:
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

        with GraphOp(name="cycle_graph") as graph:
            node_a = FuncOp(
                name="node_a",
                code_fn=lambda x: {"result": x + 1},
                inputs={"x": PARENT["x"]},
            )
            node_b = FuncOp(
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
        with GraphOp(name="linear_graph") as graph:
            node_a = FuncOp(
                name="a",
                code_fn=lambda x: {"y": x + 1},
                inputs={"x": PARENT["x"]},
            )
            node_b = FuncOp(
                name="b",
                code_fn=lambda y: {"z": y * 2},
                inputs={"y": node_a["y"]},
            )
            node_c = FuncOp(
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
        with GraphOp(name="unreachable_graph") as graph:
            connected = FuncOp(
                name="connected", code_fn=lambda: {"result": 1}, outputs={"*": PARENT}
            )
            disconnected = FuncOp(
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
        with GraphOp(name="dead_end_graph") as graph:
            node_a = FuncOp(
                name="a",
                code_fn=lambda: {"x": 1},
            )
            node_b = FuncOp(name="b", code_fn=lambda: {"y": 2}, outputs={"*": PARENT})
            dead_end = FuncOp(
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
        with GraphOp(name="connected_graph") as graph:
            a = FuncOp(name="a", code_fn=lambda: {"x": 1})
            b = FuncOp(name="b", code_fn=lambda x: {"y": x * 2}, inputs={"x": a["x"]})
            c = FuncOp(
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
        """Ref to non-existent node should raise ValueError at graph build time."""
        from hush.core.states.ref import Ref

        # Create a fake node that doesn't exist in graph
        class FakeNode:
            name = "non_existent_node"

        fake = FakeNode()

        with pytest.raises(ValueError, match="outside this graph's scope"):
            with GraphOp(name="invalid_ref_graph") as graph:
                node = FuncOp(
                    name="consumer",
                    code_fn=lambda x: {"result": x},
                    inputs={"x": Ref(fake, "output")},  # Ref to non-existent node
                    outputs={"*": PARENT},
                )

                START >> node >> END

    @pytest.mark.asyncio
    async def test_parent_ref_is_valid(self):
        """Ref to PARENT should always be valid."""
        with GraphOp(name="parent_ref_graph") as graph:
            node = FuncOp(
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
        with GraphOp(name="valid_ref_graph") as graph:
            producer = FuncOp(
                name="producer",
                code_fn=lambda: {"data": 42},
            )
            consumer = FuncOp(
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
        """__call__ should auto-build unbuilt GraphOp."""
        with GraphOp(name="auto_build_graph") as graph:
            node = FuncOp(
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
        from hush.core.ops.flow.branch_op import Branch
        from hush.core.ops.graph.graph_op import GraphValidationError

        with GraphOp(name="invalid_auto_build") as graph:
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
        from hush.core.ops.graph.graph_op import (
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
        from hush.core.ops.graph.graph_op import ValidationIssue, ValidationLevel

        issue = ValidationIssue(
            level=ValidationLevel.ERROR,
            category="Invalid branch target",
            message="Branch 'router' references non-existent target 'missing'",
            op_name="router",
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
        from hush.core.ops.graph.graph_op import (
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
        from hush.core.ops.flow.branch_op import Branch
        from hush.core.ops.graph.graph_op import GraphValidationError

        with GraphOp(name="multi_issue_graph") as graph:
            # Issue 1: Branch with invalid target
            router = Branch("router").if_(PARENT["x"] > 0, "missing_target").else_("valid_node")

            # Issue 2: Valid node but will also be checked
            valid_node = FuncOp(
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
