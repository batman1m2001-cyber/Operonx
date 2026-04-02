"""Tests for conditional iteration patterns using generator ops.

These tests replace the old WhileOp tests. Generator ops with internal
state management + the streaming scheduler handle all while-loop patterns.
"""

import pytest

from hush.core import END, PARENT, START, GraphOp, op
from hush.core.states import StateSchema

# ============================================================
# Test 1: Simple Counter Loop
# ============================================================


class TestSimpleCounterLoop:
    """Test basic counter loop that stops at a threshold."""

    @pytest.mark.asyncio
    async def test_count_to_five(self):
        """Test counting loop that stops when counter >= 5."""

        @op
        def count_up(start: int, limit: int):
            counter = start
            while counter < limit:
                counter += 1
                yield {"counter": counter}

        with GraphOp(name="counter_loop") as g:
            src = count_up(start=PARENT["start"], limit=PARENT["limit"])
            START >> src >> END

        g.build()
        schema = StateSchema(g)
        state = schema.create_state(inputs={"start": 0, "limit": 5})

        result = {}
        async for _, result in g.run(state):
            pass
        assert result["counter"] == [1, 2, 3, 4, 5]


# ============================================================
# Test 2: Accumulator Loop
# ============================================================


class TestAccumulatorLoop:
    """Test accumulator loop that sums until threshold."""

    @pytest.mark.asyncio
    async def test_sum_until_100(self):
        """Test accumulator loop that stops when total >= 100."""

        @op
        def accumulate(step: int, limit: int):
            total = 0
            while total < limit:
                total += step
                yield {"total": total}

        with GraphOp(name="accumulator_loop") as g:
            src = accumulate(step=PARENT["step"], limit=PARENT["limit"])
            START >> src >> END

        g.build()
        schema = StateSchema(g)
        state = schema.create_state(inputs={"step": 15, "limit": 100})

        result = {}
        async for _, result in g.run(state):
            pass
        # 15, 30, 45, 60, 75, 90, 105 — 7 iterations
        assert len(result["total"]) == 7
        assert result["total"][-1] == 105


# ============================================================
# Test 3: Max Iterations Safety
# ============================================================


class TestMaxIterationsSafety:
    """Test that max_iterations prevents infinite loops."""

    @pytest.mark.asyncio
    async def test_max_iterations_reached(self):
        """Test that generator stops at max iterations."""

        @op
        def infinite_counter(max_steps: int):
            value = 0
            for _ in range(max_steps):
                value += 1
                yield {"value": value}

        with GraphOp(name="safe_loop") as g:
            src = infinite_counter(max_steps=PARENT["max_steps"])
            START >> src >> END

        g.build()
        schema = StateSchema(g)
        state = schema.create_state(inputs={"max_steps": 5})

        result = {}
        async for _, result in g.run(state):
            pass
        assert result["value"] == [1, 2, 3, 4, 5]


# ============================================================
# Test 4: Complex Condition
# ============================================================


class TestComplexCondition:
    """Test complex stop conditions with multiple variables."""

    @pytest.mark.asyncio
    async def test_or_condition(self):
        """Test stop condition with OR: x >= 10 or done == True."""

        @op
        def complex_step():
            x = 0
            done = False
            while not (x >= 10 or done):
                x += 3
                done = x > 8
                yield {"x": x, "done": done}

        with GraphOp(name="complex_loop") as g:
            src = complex_step()
            START >> src >> END

        g.build()
        schema = StateSchema(g)
        state = schema.create_state()

        result = {}
        async for _, result in g.run(state):
            pass
        # 0->3->6->9 (done=True at 9>8), so 3 iterations
        assert len(result["x"]) == 3
        assert result["x"] == [3, 6, 9]
        assert result["done"] == [False, False, True]


# ============================================================
# Test 5: WhileLoop in GraphOp with Ref
# ============================================================


class TestWhileLoopWithRef:
    """Test while-loop generator inside GraphOp with Ref inputs."""

    @pytest.mark.asyncio
    async def test_ref_inputs(self):
        """Test while-loop generator receiving inputs via Ref from upstream node."""

        @op
        def get_config():
            return {"config": {"settings": {"start_value": 0, "increment": 3, "limit": 10}}}

        @op
        def count_by_step(start: int, step: int, limit: int):
            counter = start
            while counter < limit:
                counter += step
                yield {"counter": counter}

        with GraphOp(name="ref_while_graph") as graph:
            config_node = get_config()
            src = count_by_step(
                start=config_node["config"]["settings"]["start_value"],
                step=config_node["config"]["settings"]["increment"],
                limit=config_node["config"]["settings"]["limit"],
            )
            START >> config_node >> src >> END

        graph.build()
        schema = StateSchema(graph)
        state = schema.create_state()

        result = {}
        async for _, result in graph.run(state):
            pass
        # 0->3->6->9->12, 4 iterations to reach counter >= 10
        assert result["counter"] == [3, 6, 9, 12]


# ============================================================
# Test 6: Fibonacci Sequence
# ============================================================


class TestFibonacciSequence:
    """Test Fibonacci sequence generation with generator."""

    @pytest.mark.asyncio
    async def test_fibonacci(self):
        """Test Fibonacci via generator with internal state."""

        @op
        def fibonacci(limit: int):
            a, b = 0, 1
            while b < limit:
                a, b = b, a + b
                yield {"a": a, "b": b}

        with GraphOp(name="fib_loop") as g:
            src = fibonacci(limit=PARENT["limit"])
            START >> src >> END

        g.build()
        schema = StateSchema(g)
        state = schema.create_state(inputs={"limit": 21})

        result = {}
        async for _, result in g.run(state):
            pass
        # Fibonacci: 0,1 -> 1,1 -> 1,2 -> 2,3 -> 3,5 -> 5,8 -> 8,13 -> 13,21
        assert result["a"] == [1, 1, 2, 3, 5, 8, 13]
        assert result["b"] == [1, 2, 3, 5, 8, 13, 21]


# ============================================================
# Test 7: Doubling Loop
# ============================================================


class TestDoublingLoop:
    """Test doubling loop with generator."""

    @pytest.mark.asyncio
    async def test_doubling(self):
        """Test value doubling until threshold."""

        @op
        def double_until(start: int, limit: int):
            value = start
            while value < limit:
                value *= 2
                yield {"value": value}

        with GraphOp(name="double_loop") as g:
            src = double_until(start=PARENT["start"], limit=PARENT["limit"])
            START >> src >> END

        g.build()
        schema = StateSchema(g)
        state = schema.create_state(inputs={"start": 1, "limit": 16})

        result = {}
        async for _, result in g.run(state):
            pass
        # 1->2->4->8->16, 4 iterations
        assert result["value"] == [2, 4, 8, 16]


# ============================================================
# Test 8: Counter from Upstream Node
# ============================================================


class TestWhileLoopInitialFromUpstream:
    """Test while-loop generator receiving initial values from upstream node."""

    @pytest.mark.asyncio
    async def test_counter_from_upstream_node(self):
        """Test generator's counter starts from value computed by upstream node."""

        @op
        def compute_start():
            return {"start_value": 3}

        @op
        def count_up(start: int, limit: int):
            counter = start
            while counter < limit:
                counter += 1
                yield {"counter": counter}

        with GraphOp(name="upstream_while_graph") as graph:
            start_node = compute_start()
            src = count_up(start=start_node["start_value"], limit=PARENT["limit"])
            START >> start_node >> src >> END

        graph.build()
        schema = StateSchema(graph)
        state = schema.create_state(inputs={"limit": 7})

        result = {}
        async for _, result in graph.run(state):
            pass
        # Counter starts at 3: 4, 5, 6, 7 — 4 iterations
        assert result["counter"] == [4, 5, 6, 7]

    @pytest.mark.asyncio
    async def test_multiple_vars_from_upstream(self):
        """Test generator receiving multiple values from upstream."""

        @op
        def compute_config():
            return {"initial_counter": 0, "step_size": 5, "target_limit": 20}

        @op
        def count_by_step(start: int, step: int, limit: int):
            counter = start
            while counter < limit:
                counter += step
                yield {"counter": counter}

        with GraphOp(name="multi_var_graph") as graph:
            config = compute_config()
            src = count_by_step(
                start=config["initial_counter"],
                step=config["step_size"],
                limit=config["target_limit"],
            )
            START >> config >> src >> END

        graph.build()
        schema = StateSchema(graph)
        state = schema.create_state()

        result = {}
        async for _, result in graph.run(state):
            pass
        # 0->5->10->15->20, 4 iterations
        assert result["counter"] == [5, 10, 15, 20]

    @pytest.mark.asyncio
    async def test_dynamic_stop_threshold(self):
        """Test generator where stop threshold is computed dynamically."""

        @op
        def calculate_threshold(base: int):
            return {"threshold": base * 3}

        @op
        def count_by_two(limit: int):
            value = 0
            while value < limit:
                value += 2
                yield {"value": value}

        with GraphOp(name="dynamic_threshold_graph") as graph:
            calc = calculate_threshold(base=PARENT["base"])
            src = count_by_two(limit=calc["threshold"])
            START >> calc >> src >> END

        graph.build()
        schema = StateSchema(graph)
        state = schema.create_state(inputs={"base": 5})

        result = {}
        async for _, result in graph.run(state):
            pass
        # threshold = 15, value: 2, 4, 6, 8, 10, 12, 14, 16
        assert result["value"] == [2, 4, 6, 8, 10, 12, 14, 16]


# ============================================================
# Test 9: While-loop with Downstream Processing
# ============================================================


class TestWhileWithDownstream:
    """Test while-loop generator with downstream processing per yield."""

    @pytest.mark.asyncio
    async def test_while_then_process(self):
        """Test while generator >> process >> END."""

        @op
        def count_up(limit: int):
            counter = 0
            while counter < limit:
                counter += 1
                yield {"counter": counter}

        @op
        def square(counter: int):
            return {"squared": counter * counter}

        with GraphOp(name="while_process") as g:
            src = count_up(limit=PARENT["limit"])
            sq = square(counter=src["counter"])
            START >> src >> sq >> END

        g.build()
        schema = StateSchema(g)
        state = schema.create_state(inputs={"limit": 5})

        result = {}
        async for _, result in g.run(state):
            pass
        assert result["squared"] == [1, 4, 9, 16, 25]
