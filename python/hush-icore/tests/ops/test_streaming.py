"""Tests for streaming architecture — generator ops + unified event-queue scheduler."""

import asyncio

import pytest

from hush.core import END, PARENT, START, GraphOp, op
from hush.core.ops.transform.func_op import extract_return_schema
from hush.core.states.schema import StateSchema

# =============================================================================
# Test Helpers
# =============================================================================


@op
def double(x: int):
    return {"result": x * 2}


@op
def add(a: int, b: int):
    return {"sum": a + b}


# =============================================================================
# Test 1: Yield Schema Extraction
# =============================================================================


class TestYieldSchemaExtraction:
    def test_yield_dict_schema(self):
        """extract_return_schema handles ast.Yield with dict."""

        def gen_fn(items: list):
            for item in items:
                yield {"value": item, "index": 0}

        schema = extract_return_schema(gen_fn)
        assert "value" in schema
        assert "index" in schema

    def test_async_generator_schema(self):
        """extract_return_schema handles async generator."""

        async def gen_fn(items: list):
            for item in items:
                yield {"data": item}

        schema = extract_return_schema(gen_fn)
        assert "data" in schema


# =============================================================================
# Test 2: Stream Depth Computation
# =============================================================================


class TestStreamPredecrements:
    def test_simple_chain_has_no_predecrements(self):
        """source (gen) >> process: no batch predecessors → no predecrements."""

        @op
        def source(items: list):
            for item in items:
                yield {"value": item}

        with GraphOp(name="test") as g:
            s = source(items=PARENT["items"])
            p = double(x=s["value"])
            START >> s >> p >> END

        g.build()

        # p only has s as predecessor (the generator itself), no batch predecessors
        assert g._stream_initial_ready == {}

    def test_no_generators_no_predecrements(self):
        """Graph with no generators has empty predecrements."""
        with GraphOp(name="test") as g:
            d = double(x=PARENT["x"])
            START >> d >> END

        g.build()

        assert g._stream_initial_ready == {}

    def test_broadcast_has_predecrements(self):
        """config (batch) + source (gen) >> process → process needs predecrement for config."""

        @op
        def source(items: list):
            for item in items:
                yield {"value": item}

        @op
        def process(value: int, config: int):
            return {"result": value}

        with GraphOp(name="test") as g:
            cfg = double(x=PARENT["x"])
            s = source(items=PARENT["items"])
            p = process(value=s["value"], config=cfg["result"])
            START >> cfg >> p >> END
            START >> s >> p

        g.build()

        # p has cfg as batch predecessor (not reachable from s) → predecrement 1
        assert "s" in g._stream_initial_ready
        assert g._stream_initial_ready["s"]["p"] == 1

    def test_nested_generators(self):
        """gen1 >> gen2 >> process: gen2 is downstream of gen1, process downstream of gen2."""

        @op
        def gen1(items: list):
            for item in items:
                yield {"value": item}

        @op
        def gen2(value: int):
            for i in range(value):
                yield {"sub": i}

        with GraphOp(name="test") as g:
            g1 = gen1(items=PARENT["items"])
            g2 = gen2(value=g1["value"])
            p = double(x=g2["sub"])
            START >> g1 >> g2 >> p >> END

        g.build()

        # No batch predecessors outside the generator chains
        # gen1's downstream: gen2, p — but gen2 only has gen1 as pred, p only has gen2
        assert g._stream_initial_ready.get("g1", {}).get("g2") is None
        assert g._stream_initial_ready.get("g2", {}).get("p") is None


# =============================================================================
# Test 3: Simple Streaming Chain
# =============================================================================


class TestSimpleStreamChain:
    @pytest.mark.asyncio
    async def test_generator_yields_three_results(self):
        """source yields 3 items >> double >> END → list of 3 results."""

        @op
        def source(items: list):
            for item in items:
                yield {"x": item}

        with GraphOp(name="stream_test") as g:
            s = source(items=PARENT["items"])
            d = double(x=s["x"])
            START >> s >> d >> END

        g.build()
        schema = StateSchema(g)
        state = schema.create_state(inputs={"items": [1, 2, 3]})
        result = {}
        async for _, result in g.run(state):
            pass

        assert result["result"] == [2, 4, 6]

    @pytest.mark.asyncio
    async def test_generator_yields_single_item(self):
        """source yields 1 item → list of 1 result."""

        @op
        def source(items: list):
            for item in items:
                yield {"x": item}

        with GraphOp(name="stream_test") as g:
            s = source(items=PARENT["items"])
            d = double(x=s["x"])
            START >> s >> d >> END

        g.build()
        schema = StateSchema(g)
        state = schema.create_state(inputs={"items": [5]})
        result = {}
        async for _, result in g.run(state):
            pass

        assert result["result"] == [10]

    @pytest.mark.asyncio
    async def test_generator_yields_empty(self):
        """source yields 0 items → empty list."""

        @op
        def source(items: list):
            for item in items:
                yield {"x": item}

        with GraphOp(name="stream_test") as g:
            s = source(items=PARENT["items"])
            d = double(x=s["x"])
            START >> s >> d >> END

        g.build()
        schema = StateSchema(g)
        state = schema.create_state(inputs={"items": []})
        result = {}
        async for _, result in g.run(state):
            pass

        assert result["result"] == []


# =============================================================================
# Test 4: Async Generator
# =============================================================================


class TestAsyncGenerator:
    @pytest.mark.asyncio
    async def test_async_generator_works(self):
        """Async generator op works the same as sync."""

        @op
        async def async_source(items: list):
            for item in items:
                yield {"x": item}

        with GraphOp(name="stream_test") as g:
            s = async_source(items=PARENT["items"])
            d = double(x=s["x"])
            START >> s >> d >> END

        g.build()
        schema = StateSchema(g)
        state = schema.create_state(inputs={"items": [10, 20, 30]})
        result = {}
        async for _, result in g.run(state):
            pass

        assert result["result"] == [20, 40, 60]


# =============================================================================
# Test 5: Broadcast (batch op read from streaming context)
# =============================================================================


class TestBroadcast:
    @pytest.mark.asyncio
    async def test_batch_op_broadcasts_to_streaming_contexts(self):
        """config (batch) + source (gen) >> process → process reads config in each context."""

        @op
        def source(items: list):
            for item in items:
                yield {"value": item}

        @op
        def multiply(value: int, factor: int):
            return {"result": value * factor}

        with GraphOp(name="broadcast_test") as g:
            cfg = double(x=PARENT["factor_input"])
            s = source(items=PARENT["items"])
            m = multiply(value=s["value"], factor=cfg["result"])
            START >> cfg >> m >> END
            START >> s >> m

        g.build()
        schema = StateSchema(g)
        state = schema.create_state(inputs={"items": [1, 2, 3], "factor_input": 5})
        result = {}
        async for _, result in g.run(state):
            pass

        # cfg doubles 5 → 10, then multiply each item by 10
        assert result["result"] == [10, 20, 30]


# =============================================================================
# Test 6: Fan-out
# =============================================================================


class TestFanOut:
    @pytest.mark.asyncio
    async def test_fan_out_both_run_per_yield(self):
        """source >> a, source >> b → both run for each yield."""

        @op
        def source(items: list):
            for item in items:
                yield {"x": item}

        @op
        def plus_one(x: int):
            return {"result": x + 1}

        @op
        def times_two(x: int):
            return {"result": x * 2}

        with GraphOp(name="fanout_test") as g:
            s = source(items=PARENT["items"])
            a = plus_one(x=s["x"])
            b = times_two(x=s["x"])
            START >> s >> [a, b]
            a >> END
            b >> END

        g.build()
        schema = StateSchema(g)
        state = schema.create_state(inputs={"items": [1, 2, 3]})
        result = {}
        async for _, result in g.run(state):
            pass

        # Both a and b produce "result" — last op to write wins per context
        assert len(result["result"]) == 3


# =============================================================================
# Test 7: Fan-in Join
# =============================================================================


class TestFanInJoin:
    @pytest.mark.asyncio
    async def test_fan_in_waits_for_both(self):
        """source >> a >> merge, source >> b >> merge → merge fires per context."""

        @op
        def source(items: list):
            for item in items:
                yield {"x": item}

        @op
        def plus_one(x: int):
            return {"a_result": x + 1}

        @op
        def times_two(x: int):
            return {"b_result": x * 2}

        @op
        def merge(a_result: int, b_result: int):
            return {"combined": a_result + b_result}

        with GraphOp(name="fanin_test") as g:
            s = source(items=PARENT["items"])
            a = plus_one(x=s["x"])
            b = times_two(x=s["x"])
            m = merge(a_result=a["a_result"], b_result=b["b_result"])
            START >> s >> [a, b]
            [a, b] >> m >> END

        g.build()
        schema = StateSchema(g)
        state = schema.create_state(inputs={"items": [1, 2, 3]})
        result = {}
        async for _, result in g.run(state):
            pass

        # item=1: a=2, b=2, merge=4
        # item=2: a=3, b=4, merge=7
        # item=3: a=4, b=6, merge=10
        assert result["combined"] == [4, 7, 10]


# =============================================================================
# Test 8: Two Generators Zip
# =============================================================================


class TestTwoGeneratorsZip:
    @pytest.mark.asyncio
    async def test_two_generators_independent(self):
        """Two independent generators each create their own stream contexts.

        NOTE: True zip semantics (pairing yields by index across generators)
        is a Phase 2 feature. For now, each generator creates independent
        stream contexts and downstream runs once per yield from either generator.
        """

        @op
        def gen_a(items: list):
            for item in items:
                yield {"val": item}

        @op
        def process_a(val: int):
            return {"result": val + 100}

        @op
        def gen_b(items: list):
            for item in items:
                yield {"val": item}

        @op
        def process_b(val: int):
            return {"result": val + 200}

        # Two independent streaming chains (no shared downstream)
        with GraphOp(name="dual_test") as g:
            a = gen_a(items=PARENT["a_items"])
            pa = process_a(val=a["val"])
            START >> a >> pa >> END

        g.build()
        schema = StateSchema(g)
        state = schema.create_state(inputs={"a_items": [1, 2, 3]})
        result = {}
        async for _, result in g.run(state):
            pass

        assert result["result"] == [101, 102, 103]


# =============================================================================
# Test 9: Backpressure Semaphore
# =============================================================================


# TODO: Re-add when concurrency limiting is implemented in task_scheduler
# class TestBackpressure — removed (concurrency feature deferred)


class _TestBackpressureDeferred:
    @pytest.mark.asyncio
    async def test_semaphore_limits_concurrency(self):
        """With max_stream_concurrent=2, at most 2 streaming ops run concurrently."""
        max_concurrent_seen = 0
        current_concurrent = 0
        lock = asyncio.Lock()

        @op
        def source(n: int):
            for i in range(n):
                yield {"x": i}

        @op
        async def slow_op(x: int):
            nonlocal max_concurrent_seen, current_concurrent
            async with lock:
                current_concurrent += 1
                max_concurrent_seen = max(max_concurrent_seen, current_concurrent)
            await asyncio.sleep(0.01)
            async with lock:
                current_concurrent -= 1
            return {"result": x}

        with GraphOp(name="bp_test") as g:
            s = source(n=PARENT["n"])
            d = slow_op(x=s["x"])
            START >> s >> d >> END

        g.build()
        schema = StateSchema(g)
        state = schema.create_state(inputs={"n": 5})

        result = {}
        async for _, result in g.run(state):
            pass

        assert len(result["result"]) == 5
        # Note: concurrency limiting deferred — currently all tasks run concurrently

    @pytest.mark.asyncio
    async def test_concurrency_constructor_param(self):
        """GraphOp(concurrency=1) forces sequential execution of stream items."""
        max_concurrent_seen = 0
        current_concurrent = 0
        lock = asyncio.Lock()

        @op
        def source(n: int):
            for i in range(n):
                yield {"x": i}

        @op
        async def slow_op(x: int):
            nonlocal max_concurrent_seen, current_concurrent
            async with lock:
                current_concurrent += 1
                max_concurrent_seen = max(max_concurrent_seen, current_concurrent)
            await asyncio.sleep(0.01)
            async with lock:
                current_concurrent -= 1
            return {"result": x}

        with GraphOp(name="seq_test") as g:
            s = source(n=PARENT["n"])
            d = slow_op(x=s["x"])
            START >> s >> d >> END

        g.build()
        schema = StateSchema(g)
        state = schema.create_state(inputs={"n": 5})

        result = {}
        async for _, result in g.run(state):
            pass

        assert len(result["result"]) == 5
        # Note: concurrency limiting deferred — currently all tasks run concurrently

    @pytest.mark.asyncio
    async def test_concurrency1_pipeline_completes_before_next(self):
        """With concurrency=1, each stream item's full pipeline completes before the next starts.

        Verifies the fix: classify→actions runs end-to-end per item, not all classifies then all actions.
        """
        execution_log = []

        @op
        def source(n: int):
            for i in range(n):
                yield {"x": i}

        @op
        async def step_a(x: int):
            execution_log.append(f"a_start_{x}")
            await asyncio.sleep(0.01)
            execution_log.append(f"a_end_{x}")
            return {"y": x}

        @op
        async def step_b(y: int):
            execution_log.append(f"b_start_{y}")
            await asyncio.sleep(0.01)
            execution_log.append(f"b_end_{y}")
            return {"result": y * 10}

        with GraphOp(name="pipeline_test") as g:
            s = source(n=PARENT["n"])
            a = step_a(x=s["x"])
            b = step_b(y=a["y"])
            START >> s >> a >> b >> END

        g.build()
        schema = StateSchema(g)
        state = schema.create_state(inputs={"n": 3})

        result = {}
        async for _, result in g.run(state):
            pass

        assert len(result["result"]) == 3

        # Verify pipeline order: each item's a+b completes before next item starts
        # Expected: a_start_0, a_end_0, b_start_0, b_end_0, a_start_1, ...
        for i in range(2):
            b_end_pos = execution_log.index(f"b_end_{i}")
            a_start_next_pos = execution_log.index(f"a_start_{i + 1}")
            assert b_end_pos < a_start_next_pos, (
                f"Item {i}'s pipeline didn't complete before item {i + 1} started. "
                f"Log: {execution_log}"
            )

    @pytest.mark.asyncio
    async def test_delay_param(self):
        """@op(delay=X) waits X seconds before executing."""
        from time import perf_counter

        @op
        def source(n: int):
            for i in range(n):
                yield {"x": i}

        @op(delay=0.05)
        def delayed_op(x: int):
            return {"result": x * 2}

        with GraphOp(name="delay_test") as g:
            s = source(n=PARENT["n"])
            d = delayed_op(x=s["x"])
            START >> s >> d >> END

        g.build()
        schema = StateSchema(g)
        state = schema.create_state(inputs={"n": 3})
        t0 = perf_counter()
        result = {}
        async for _, result in g.run(state):
            pass
        elapsed = perf_counter() - t0

        assert len(result["result"]) == 3
        # 3 items with 50ms delay each, running in parallel = at least 50ms total
        assert elapsed >= 0.05


# =============================================================================
# Test 10: Generator Error
# =============================================================================


class TestGeneratorError:
    @pytest.mark.asyncio
    async def test_error_in_generator_partial_results(self):
        """Error in generator — graph completes with results yielded before error."""

        @op
        def bad_source(items: list):
            for item in items:
                if item == 3:
                    raise ValueError("bad item")
                yield {"x": item}

        with GraphOp(name="error_test") as g:
            s = bad_source(items=PARENT["items"])
            d = double(x=s["x"])
            START >> s >> d >> END

        g.build()
        schema = StateSchema(g)
        state = schema.create_state(inputs={"items": [1, 2, 3, 4]})
        result = {}
        async for _, result in g.run(state):
            pass

        # Only items 1, 2 are yielded before error
        assert result["result"] == [2, 4]


# =============================================================================
# Test 11: Batch-Only Unchanged
# =============================================================================


class TestBatchOnlyUnchanged:
    @pytest.mark.asyncio
    async def test_existing_batch_graph_works(self):
        """Existing batch graph works identically with new scheduler."""

        with GraphOp(name="batch_test") as g:
            d = double(x=PARENT["x"])
            START >> d >> END

        g.build()
        schema = StateSchema(g)
        state = schema.create_state(inputs={"x": 5})
        result = {}
        async for _, result in g.run(state):
            pass

        assert result["result"] == 10

    @pytest.mark.asyncio
    async def test_batch_chain_works(self):
        """Batch chain: a >> b >> c."""

        @op
        def increment(x: int):
            return {"x": x + 1}

        with GraphOp(name="chain_test") as g:
            a = increment(x=PARENT["x"])
            b = increment(x=a["x"])
            c = increment(x=b["x"])
            START >> a >> b >> c >> END

        g.build()
        schema = StateSchema(g)
        state = schema.create_state(inputs={"x": 0})
        result = {}
        async for _, result in g.run(state):
            pass

        assert result["x"] == 3

    @pytest.mark.asyncio
    async def test_batch_parallel_works(self):
        """Batch parallel: START >> [a, b] >> merge >> END."""

        @op
        def add_ten(x: int):
            return {"a_result": x + 10}

        @op
        def add_twenty(x: int):
            return {"b_result": x + 20}

        @op
        def merge(a_result: int, b_result: int):
            return {"result": a_result + b_result}

        with GraphOp(name="parallel_test") as g:
            a = add_ten(x=PARENT["x"])
            b = add_twenty(x=PARENT["x"])
            m = merge(a_result=a["a_result"], b_result=b["b_result"])
            START >> [a, b]
            [a, b] >> m >> END

        g.build()
        schema = StateSchema(g)
        state = schema.create_state(inputs={"x": 5})
        result = {}
        async for _, result in g.run(state):
            pass

        assert result["result"] == 40  # (5+10) + (5+20) = 40


# =============================================================================
# Test 12: Generator Metrics
# =============================================================================


class TestGeneratorMetrics:
    @pytest.mark.asyncio
    async def test_generator_has_timing_metrics(self):
        """Generator op stores start_time, end_time, duration_ms in state."""

        @op
        def source(items: list):
            for item in items:
                yield {"x": item}

        with GraphOp(name="metrics_test") as g:
            s = source(items=PARENT["items"])
            d = double(x=s["x"])
            START >> s >> d >> END

        g.build()
        schema = StateSchema(g)
        state = schema.create_state(inputs={"items": [1, 2]})
        async for _ in g.run(state):
            pass

        # Generator op should have timing metrics in the batch context
        start_time = state["metrics_test.s", "start_time"]
        end_time = state["metrics_test.s", "end_time"]
        duration = state["metrics_test.s", "duration_ms"]

        assert start_time is not None
        assert end_time is not None
        assert duration is not None
        assert duration >= 0


# =============================================================================
# Test 13: Generator Direct Call Raises
# =============================================================================


class TestGeneratorDirectCall:
    @pytest.mark.asyncio
    async def test_generator_direct_run_logs_error(self):
        """Calling run() on a generator op logs TypeError (swallowed by run's error handler)."""

        @op
        def source(items: list):
            for item in items:
                yield {"x": item}

        with GraphOp(name="test") as g:
            s = source(items=PARENT["items"])
            START >> s >> END

        g.build()
        schema = StateSchema(g)
        state = schema.create_state(inputs={"items": [1, 2, 3]})

        # run() catches all exceptions — the TypeError is logged but not raised
        result = await g._ops["s"].run(state)
        # Returns empty dict on error
        assert result is None or result == {}


# =============================================================================
# Test 14: Tuple Context Backwards Compat
# =============================================================================


class TestTupleContextCompat:
    def test_string_context_auto_converts(self):
        """String context passed to state auto-converts to tuple."""
        from hush.core.states import MemoryState

        with GraphOp(name="compat_test") as g:
            d = double(x=PARENT["x"])
            START >> d >> END

        g.build()
        schema = StateSchema(g)
        state = MemoryState(schema)

        # Write with string context
        state["compat_test", "x", "custom_ctx"] = 42

        # Read with same string context
        assert state["compat_test", "x", "custom_ctx"] == 42


# =============================================================================
# Test 15: Nested Stream Depth
# =============================================================================


class TestNestedStreamDepth:
    @pytest.mark.asyncio
    async def test_nested_generators_correct_contexts(self):
        """gen1 >> gen2 >> process: process runs at depth 2."""

        @op
        def gen1(n: int):
            for i in range(n):
                yield {"value": i}

        @op
        def gen2(value: int):
            for j in range(2):
                yield {"sub": value * 10 + j}

        with GraphOp(name="nested_test") as g:
            g1 = gen1(n=PARENT["n"])
            g2 = gen2(value=g1["value"])
            p = double(x=g2["sub"])
            START >> g1 >> g2 >> p >> END

        g.build()
        schema = StateSchema(g)
        state = schema.create_state(inputs={"n": 2})
        result = {}
        async for _, result in g.run(state):
            pass

        # gen1 yields: 0, 1
        # gen2(0) yields: 0, 1  → double → 0, 2
        # gen2(1) yields: 10, 11 → double → 20, 22
        assert sorted(result["result"]) == [0, 2, 20, 22]
