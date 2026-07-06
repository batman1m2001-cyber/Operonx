"""Tests for GraphOp.loop() feedback loops (Phase 3)."""

import pytest

from operonx.core import END, PARENT, START, GraphOp, graph, op
from operonx.core.states import StateSchema

# ============================================================
# Test 1: Simple Counter Loop
# ============================================================


class TestSimpleCounterLoop:
    """Test basic counter loop with string until condition."""

    @pytest.mark.asyncio
    async def test_count_to_five(self):
        @op
        def increment(counter: int):
            return {"counter": counter + 1}

        with GraphOp.loop(name="counter", until="count >= 5", count=0) as g:
            inc = increment(counter=PARENT["count"])
            inc["counter"] >> PARENT["count"]
            START >> inc >> END

        g.build()
        schema = StateSchema(g)
        state = schema.create_state()

        result = {}
        async for _, result in g.run(state):
            pass
        assert result["count"] == 5


# ============================================================
# Test 2: Loop Max Iterations
# ============================================================


class TestLoopMaxIterations:
    """Test that max_iterations prevents infinite loops."""

    @pytest.mark.asyncio
    async def test_max_iterations_reached(self):
        @op
        def increment(counter: int):
            return {"counter": counter + 1}

        with GraphOp.loop(name="bounded", max_iterations=3, count=0) as g:
            inc = increment(counter=PARENT["count"])
            inc["counter"] >> PARENT["count"]
            START >> inc >> END

        g.build()
        schema = StateSchema(g)
        state = schema.create_state()

        result = {}
        async for _, result in g.run(state):
            pass
        assert result["count"] == 3


# ============================================================
# Test 3: Loop with Callable Until
# ============================================================


class TestLoopCallableUntil:
    """Test loop with callable until condition."""

    @pytest.mark.asyncio
    async def test_callable_until(self):
        @op
        def increment(counter: int):
            return {"counter": counter + 1}

        with GraphOp.loop(
            name="callable_loop",
            until=lambda out: out["count"] > 3,
            count=0,
        ) as g:
            inc = increment(counter=PARENT["count"])
            inc["counter"] >> PARENT["count"]
            START >> inc >> END

        g.build()
        schema = StateSchema(g)
        state = schema.create_state()

        result = {}
        async for _, result in g.run(state):
            pass
        assert result["count"] == 4


# ============================================================
# Test 4: Fibonacci via State Carry-Forward
# ============================================================


class TestFibonacciLoop:
    """Test Fibonacci sequence via loop state carry-forward."""

    @pytest.mark.asyncio
    async def test_fibonacci(self):
        @op
        def fib_step(a: int, b: int):
            return {"a": b, "b": a + b}

        with GraphOp.loop(name="fib", until="b >= 21", a=0, b=1) as g:
            step = fib_step(a=PARENT["a"], b=PARENT["b"])
            step["a"] >> PARENT["a"]
            step["b"] >> PARENT["b"]
            START >> step >> END

        g.build()
        schema = StateSchema(g)
        state = schema.create_state()

        result = {}
        async for _, result in g.run(state):
            pass
        assert result["a"] == 13
        assert result["b"] == 21


# ============================================================
# Test 5: Loop Accumulator
# ============================================================


class TestLoopAccumulator:
    """Test accumulator pattern with loop."""

    @pytest.mark.asyncio
    async def test_accumulator_loop(self):
        """Test accumulator loop that sums by step until threshold."""

        @op
        def step(total: int):
            return {"total": total + 15}

        with GraphOp.loop(
            name="sum_loop",
            until="total >= 100",
            max_iterations=20,
            total=0,
        ) as g:
            s = step(total=PARENT["total"])
            s["total"] >> PARENT["total"]
            START >> s >> END

        g.build()
        schema = StateSchema(g)
        state = schema.create_state()

        result = {}
        async for _, result in g.run(state):
            pass
        # 0->15->30->45->60->75->90->105 (7 iterations)
        assert result["total"] == 105


# ============================================================
# Test 6: Loop Inside Graph
# ============================================================


class TestLoopInsideGraph:
    """Test GraphOp.loop() nested inside a parent GraphOp."""

    @pytest.mark.asyncio
    async def test_nested_loop(self):
        @op
        def increment(counter: int):
            return {"counter": counter + 1}

        @op
        def prepare(start_val: int):
            return {"initial": start_val}

        with GraphOp(name="outer") as outer:
            prep = prepare(start_val=PARENT["start_val"])

            with GraphOp.loop(name="inner_loop", until="count >= 5") as loop:
                inc = increment(counter=PARENT["count"])
                inc["counter"] >> PARENT["count"]
                START >> inc >> END
            loop.inputs = {"count": prep["initial"]}

            START >> prep >> loop >> END

        outer.build()
        schema = StateSchema(outer)
        state = schema.create_state(inputs={"start_val": 2})

        result = {}
        async for _, result in outer.run(state):
            pass
        assert result["count"] == 5


# ============================================================
# Test 7: Loop with Branch
# ============================================================


class TestLoopWithBranch:
    """Test branch inside a loop iteration."""

    @pytest.mark.asyncio
    async def test_branch_inside_loop(self):
        @op
        def step(value: int):
            if value % 2 == 0:
                return {"value": value // 2}
            else:
                return {"value": value * 3 + 1}

        with GraphOp.loop(name="collatz", until="value == 1", max_iterations=50, value=6) as g:
            s = step(value=PARENT["value"])
            s["value"] >> PARENT["value"]
            START >> s >> END

        g.build()
        schema = StateSchema(g)
        state = schema.create_state()

        result = {}
        async for _, result in g.run(state):
            pass
        assert result["value"] == 1


# ============================================================
# Test 8: Loop with Initial from Upstream
# ============================================================


class TestLoopInitialFromUpstream:
    """Test loop receiving initial values from upstream op."""

    @pytest.mark.asyncio
    async def test_upstream_initial(self):
        @op
        def get_start():
            return {"start": 10}

        @op
        def halve(value: int):
            return {"value": value // 2}

        with GraphOp(name="outer") as outer:
            starter = get_start()

            with GraphOp.loop(name="halve_loop", until="value <= 1", max_iterations=20) as loop:
                h = halve(value=PARENT["value"])
                h["value"] >> PARENT["value"]
                START >> h >> END
            loop.inputs = {"value": starter["start"]}

            START >> starter >> loop >> END

        outer.build()
        schema = StateSchema(outer)
        state = schema.create_state()

        result = {}
        async for _, result in outer.run(state):
            pass
        assert result["value"] == 1
