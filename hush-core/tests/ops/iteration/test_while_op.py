"""Tests for WhileOp - conditional iteration node."""

import pytest

from hush.core.ops.base import END, PARENT, START
from hush.core.ops.graph.graph_op import GraphOp
from hush.core.ops.iteration.while_op import WhileOp
from hush.core.ops.transform.func_op import op
from hush.core.states import MemoryState, StateSchema

# ============================================================
# Test 1: Simple Counter Loop
# ============================================================


class TestSimpleCounterLoop:
    """Test basic counter loop that stops at a threshold."""

    @pytest.mark.asyncio
    async def test_count_to_five(self):
        """Test counting loop that stops when counter >= 5."""

        @op
        def increment(counter: int):
            new_counter = counter + 1
            return {"new_counter": new_counter}

        with WhileOp(
            name="counter_loop",
            inputs={"counter": 0},
            stop_condition="counter >= 5",
            max_iterations=10,
        ) as loop:
            node = increment(
                inputs={"counter": PARENT["counter"]}, outputs={"new_counter": PARENT["counter"]}
            )
            START >> node >> END

        loop.build()
        schema = StateSchema(loop)
        state = MemoryState(schema)

        result = await loop.run(state)

        assert result.get("iteration_metrics", {}).get("total_iterations") == 5
        assert result.get("iteration_metrics", {}).get("stopped_by_condition") is True


# ============================================================
# Test 2: Accumulator Loop
# ============================================================


class TestAccumulatorLoop:
    """Test accumulator loop that sums until threshold."""

    @pytest.mark.asyncio
    async def test_sum_until_100(self):
        """Test accumulator loop that stops when total >= 100."""

        @op
        def accumulate(total: int, step: int):
            new_total = total + step
            return {"new_total": new_total}

        with WhileOp(
            name="accumulator_loop",
            inputs={"total": 0, "step": 15},
            max_iterations=10,
            stop_condition="total >= 100",
        ) as loop:
            node = accumulate(
                inputs={"total": PARENT["total"], "step": PARENT["step"]},
                outputs={"new_total": PARENT["total"]},
            )
            START >> node >> END

        loop.build()
        schema = StateSchema(loop)
        state = MemoryState(schema)

        result = await loop.run(state)

        # 0+15*7=105 >= 100, so 7 iterations
        assert result.get("iteration_metrics", {}).get("total_iterations") == 7


# ============================================================
# Test 3: Max Iterations Safety
# ============================================================


class TestMaxIterationsSafety:
    """Test that max_iterations prevents infinite loops."""

    @pytest.mark.asyncio
    async def test_max_iterations_reached(self):
        """Test that loop stops at max_iterations when no stop_condition."""

        @op
        def infinite_loop(value: int):
            new_value = value + 1
            return {"new_value": new_value}

        with WhileOp(name="safe_loop", max_iterations=5, inputs={"value": 0}) as loop:
            node = infinite_loop(
                inputs={"value": PARENT["value"]}, outputs={"new_value": PARENT["value"]}
            )
            START >> node >> END

        loop.build()
        schema = StateSchema(loop)
        state = MemoryState(schema)

        result = await loop.run(state)

        assert result.get("iteration_metrics", {}).get("max_iterations_reached") is True
        assert result.get("iteration_metrics", {}).get("total_iterations") == 5


# ============================================================
# Test 4: Complex Condition
# ============================================================


class TestComplexCondition:
    """Test complex stop conditions with multiple variables."""

    @pytest.mark.asyncio
    async def test_or_condition(self):
        """Test stop condition with OR: x >= 10 or done == True."""

        @op
        def complex_step(x: int, done: bool):
            new_x = x + 3
            new_done = new_x > 8
            return {"new_x": new_x, "new_done": new_done}

        with WhileOp(
            name="complex_loop", inputs={"x": 0, "done": False}, stop_condition="x >= 10 or done"
        ) as loop:
            node = complex_step(
                inputs={"x": PARENT["x"], "done": PARENT["done"]},
                outputs={"new_x": PARENT["x"], "new_done": PARENT["done"]},
            )
            START >> node >> END

        loop.build()
        schema = StateSchema(loop)
        state = MemoryState(schema)

        result = await loop.run(state)

        # 0->3->6->9 (done=True at 9>8), so 3 iterations
        assert result.get("iteration_metrics", {}).get("total_iterations") == 3


# ============================================================
# Test 5: WhileLoop in GraphOp with Ref
# ============================================================


class TestWhileLoopWithRef:
    """Test WhileOp inside GraphOp with Ref inputs."""

    @pytest.mark.asyncio
    async def test_ref_inputs(self):
        """Test WhileLoop receiving inputs via Ref from upstream node."""

        @op
        def get_config():
            return {"config": {"settings": {"start_value": 0, "increment": 3, "limit": 10}}}

        @op
        def increment_by(counter: int, step: int):
            new_counter = counter + step
            return {"new_counter": new_counter}

        with GraphOp(name="ref_while_graph") as graph:
            config_node = get_config()

            with WhileOp(
                name="ref_loop",
                inputs={
                    "counter": config_node["config"]["settings"]["start_value"],
                    "step": config_node["config"]["settings"]["increment"],
                },
                stop_condition="counter >= 10",
            ) as loop:
                node = increment_by(
                    inputs={"counter": PARENT["counter"], "step": PARENT["step"]},
                    outputs={"new_counter": PARENT["counter"]},
                )
                START >> node >> END

            START >> config_node >> loop >> END

        graph.build()
        schema = StateSchema(graph)
        state = MemoryState(schema)

        await graph.run(state)

        loop_result = state[loop.full_name, "iteration_metrics", None]
        iterations = loop_result.get("total_iterations", 0) if loop_result else 0
        # 0->3->6->9->12, 4 iterations to reach counter >= 10
        assert iterations == 4


# ============================================================
# Test 6: Schema Extraction
# ============================================================


class TestSchemaExtraction:
    """Test that WhileOp extracts inputs/outputs schema correctly."""

    def test_inputs_include_condition_vars(self):
        """Test that inputs include variables from stop_condition."""

        @op
        def increment(counter: int):
            return {"new_counter": counter + 1}

        with WhileOp(
            name="schema_test_loop",
            inputs={"counter": 0},
            stop_condition="counter >= 5",
            max_iterations=10,
        ) as loop:
            node = increment(
                inputs={"counter": PARENT["counter"]}, outputs={"new_counter": PARENT["counter"]}
            )
            START >> node >> END

        loop.build()

        assert "counter" in loop.inputs
        assert "iteration_metrics" in loop.outputs


# ============================================================
# Test 7: New Output Mapping Syntax with >>
# ============================================================


class TestWhileLoopNewOutputSyntax:
    """Test WhileOp with node["key"] >> PARENT["key"] syntax."""

    @pytest.mark.asyncio
    async def test_specific_key_mapping(self):
        """Test node["key"] >> PARENT["key"] for specific mapping in WhileLoop.

        Maps node output "new_a" to parent output "a" (for loop variable update),
        and also creates explicit parent outputs "result_a" and "result_b".
        """

        @op
        def fibonacci_step(a: int, b: int):
            new_a = b
            new_b = a + b
            return {"new_a": new_a, "new_b": new_b}

        with WhileOp(
            name="fibonacci_while_loop",
            inputs={"a": 0, "b": 1},
            stop_condition="b >= 21",
            max_iterations=20,
        ) as loop:
            node = fibonacci_step(inputs={"a": PARENT["a"], "b": PARENT["b"]})
            # Map to loop iteration variables
            node["new_a"] >> PARENT["a"]
            node["new_b"] >> PARENT["b"]
            START >> node >> END

        loop.build()
        schema = StateSchema(loop)
        state = MemoryState(schema)

        result = await loop.run(state)

        # Fibonacci: 0,1 -> 1,1 -> 1,2 -> 2,3 -> 3,5 -> 5,8 -> 8,13 -> 13,21
        # Stop when b >= 21, so 7 iterations
        assert result.get("iteration_metrics", {}).get("total_iterations") == 7
        # The mapped output keys are "a" and "b" (the PARENT keys)
        assert result.get("a") == 13
        assert result.get("b") == 21

    @pytest.mark.asyncio
    async def test_new_syntax_replaces_old_outputs(self):
        """Test new >> syntax completely replaces old outputs= syntax."""

        @op
        def double_step(value: int):
            doubled = value * 2
            return {"doubled": doubled}

        with WhileOp(
            name="new_syntax_loop",
            inputs={"value": 1},
            stop_condition="value >= 16",
            max_iterations=10,
        ) as loop:
            node = double_step(inputs={"value": PARENT["value"]})
            # New syntax only: map doubled output to loop's value variable
            node["doubled"] >> PARENT["value"]
            START >> node >> END

        loop.build()
        schema = StateSchema(loop)
        state = MemoryState(schema)

        result = await loop.run(state)

        # Value: 1->2->4->8->16, stops at 4 iterations
        assert result.get("iteration_metrics", {}).get("total_iterations") == 4
        assert result.get("value") == 16


# ============================================================
# Test 8: WhileLoop with Initial Value from Upstream Node
# ============================================================


class TestWhileLoopInitialFromUpstream:
    """Test WhileOp receiving initial condition variable from upstream node."""

    @pytest.mark.asyncio
    async def test_counter_from_upstream_node(self):
        """Test WhileLoop's counter starts from value computed by upstream node.

        This tests the scenario where the initial value of a condition variable
        (like 'counter') comes from another node's output rather than a literal.
        """

        @op
        def compute_start():
            """Compute initial counter value."""
            return {"start_value": 3}

        @op
        def increment(counter: int):
            """Increment counter by 1."""
            return {"new_counter": counter + 1}

        with GraphOp(name="upstream_while_graph") as graph:
            start_node = compute_start()

            with WhileOp(
                name="counter_loop",
                inputs={"counter": start_node["start_value"]},
                stop_condition="counter >= 7",
                max_iterations=10,
            ) as loop:
                inc_node = increment(inputs={"counter": PARENT["counter"]})
                inc_node["new_counter"] >> PARENT["counter"]
                START >> inc_node >> END

            START >> start_node >> loop >> END

        graph.build()
        schema = StateSchema(graph)
        state = MemoryState(schema)

        await graph.run(state)

        # Counter starts at 3 (from upstream), then: 3->4->5->6->7
        # Stops when counter >= 7, so 4 iterations
        loop_result = state[loop.full_name, "iteration_metrics", None]
        assert loop_result.get("total_iterations") == 4
        assert loop_result.get("stopped_by_condition") is True

        # Final counter value should be 7
        final_counter = state[loop.full_name, "counter", None]
        assert final_counter == 7

    @pytest.mark.asyncio
    async def test_multiple_vars_from_upstream(self):
        """Test WhileLoop receiving multiple condition variables from upstream.

        Both 'counter' and 'limit' come from an upstream node's output.
        """

        @op
        def compute_config():
            """Compute initial values and limit."""
            return {"initial_counter": 0, "step_size": 5, "target_limit": 20}

        @op
        def increment_by(counter: int, step: int):
            """Increment counter by step."""
            return {"new_counter": counter + step}

        with GraphOp(name="multi_var_graph") as graph:
            config = compute_config()

            with WhileOp(
                name="configured_loop",
                inputs={
                    "counter": config["initial_counter"],
                    "step": config["step_size"],
                    "limit": config["target_limit"],
                },
                stop_condition="counter >= limit",
                max_iterations=20,
            ) as loop:
                inc = increment_by(inputs={"counter": PARENT["counter"], "step": PARENT["step"]})
                inc["new_counter"] >> PARENT["counter"]
                START >> inc >> END

            START >> config >> loop >> END

        graph.build()
        schema = StateSchema(graph)
        state = MemoryState(schema)

        await graph.run(state)

        # Counter: 0->5->10->15->20, stops at counter >= 20
        # 4 iterations
        loop_result = state[loop.full_name, "iteration_metrics", None]
        assert loop_result.get("total_iterations") == 4

        final_counter = state[loop.full_name, "counter", None]
        assert final_counter == 20

    @pytest.mark.asyncio
    async def test_dynamic_stop_threshold(self):
        """Test WhileLoop where stop threshold is computed dynamically.

        The 'threshold' variable is computed by an upstream node and used
        in the stop_condition.
        """

        @op
        def calculate_threshold(base: int):
            """Calculate dynamic threshold."""
            return {"threshold": base * 3}

        @op
        def increment(value: int):
            """Increment value."""
            return {"new_value": value + 2}

        with GraphOp(name="dynamic_threshold_graph", inputs={"base": 5}) as graph:
            calc = calculate_threshold(inputs={"base": PARENT["base"]})

            with WhileOp(
                name="dynamic_loop",
                inputs={"value": 0, "threshold": calc["threshold"]},
                stop_condition="value >= threshold",
                max_iterations=20,
            ) as loop:
                inc = increment(inputs={"value": PARENT["value"]})
                inc["new_value"] >> PARENT["value"]
                START >> inc >> END

            START >> calc >> loop >> END

        graph.build()
        schema = StateSchema(graph)
        state = MemoryState(schema, inputs={"base": 5})

        await graph.run(state)

        # threshold = 5 * 3 = 15
        # value: 0->2->4->6->8->10->12->14->16
        # stops when value >= 15, so at 8 iterations (value=16)
        loop_result = state[loop.full_name, "iteration_metrics", None]
        assert loop_result.get("total_iterations") == 8
        assert loop_result.get("stopped_by_condition") is True

        final_value = state[loop.full_name, "value", None]
        assert final_value == 16


# ============================================================
# Test: WhileOp.of() Shorthand
# ============================================================


class TestWhileShorthand:
    """Test WhileOp.of() shorthand classmethod."""

    @pytest.mark.asyncio
    async def test_while_shorthand_basic(self):
        """Test basic WhileOp.of() with stop_condition."""

        @op
        def increment(counter: int):
            return {"counter": counter + 1}

        with WhileOp.of(counter=0, stop_condition="counter >= 5") as loop:
            node = increment(inputs={"counter": PARENT["counter"]})
            node["counter"] >> PARENT["counter"]
            START >> node >> END

        loop.build()
        schema = StateSchema(loop)
        state = MemoryState(schema)

        result = await loop.run(state)
        assert result["iteration_metrics"]["total_iterations"] == 5
        assert result["iteration_metrics"]["stopped_by_condition"] is True

    def test_while_shorthand_auto_name(self):
        """Test that WhileOp.of() auto-names from variable assignment."""
        my_while = WhileOp.of(counter=0, stop_condition="counter >= 5")
        assert my_while.name == "my_while"

    def test_while_shorthand_is_whileloopnode(self):
        """Test that WhileOp.of() returns a WhileOp instance."""
        loop = WhileOp.of(counter=0, stop_condition="counter >= 5")
        assert isinstance(loop, WhileOp)
