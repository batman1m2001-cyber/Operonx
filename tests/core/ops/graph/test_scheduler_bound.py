"""Test suite for scheduler bound-aware dispatch optimization.

Verifies that _dispatch_type (inline vs task) works correctly for
all graph patterns: simple, fan-out, mixed, generators, nested,
loops, branches, and error propagation.
"""

import asyncio
import threading

import pytest

from operon.core import (
    END,
    PARENT,
    START,
    GraphOp,
    Operon,
    graph,
    op,
)
from operon.core.ops.flow import if_

# ── Helpers ──


@op
def double(x: int):
    return {"result": x * 2}


@op
def add_ten(x: int):
    return {"result": x + 10}


@op
def merge(a: int, b: int):
    return {"result": a + b}


@op(bound="io")
async def async_double(x: int):
    await asyncio.sleep(0)
    return {"result": x * 2}


@op(bound="cpu")
def thread_double(x: int):
    return {"result": x * 2, "tid": threading.current_thread().ident}


@op
def gen_items(items: list):
    for item in items:
        yield {"value": item}


@op
def process_item(value: int):
    return {"result": value * 3}


@op
def failing_op(x: int):
    raise ValueError("intentional error")


# ============================================================
# Test 1: Inline sync op produces correct result
# ============================================================


class TestInlineSyncOp:
    @pytest.mark.asyncio
    async def test_inline_sync_op(self):
        with GraphOp(name="g") as g:
            d = double(x=PARENT["x"])
            START >> d >> END

        result = await Operon(g).run(inputs={"x": 5})
        assert result["result"] == 10

    @pytest.mark.asyncio
    async def test_dispatch_type_is_inline(self):
        with GraphOp(name="g") as g:
            d = double(x=PARENT["x"])
            START >> d >> END
        g.build()
        op_d = g._ops["d"]
        assert op_d.bound == "sync"


# ============================================================
# Test 2: Inline sync generator
# ============================================================


class TestInlineSyncGenerator:
    @pytest.mark.asyncio
    async def test_generator_yields_all(self):
        with GraphOp(name="g") as g:
            gen = gen_items(items=PARENT["items"])
            step = process_item(value=gen["value"])
            START >> gen >> step >> END

        result = await Operon(g).run(inputs={"items": [1, 2, 3]})
        results = result.get("result", [])
        if not isinstance(results, list):
            results = [results]
        assert sorted(results) == [3, 6, 9]

    @pytest.mark.asyncio
    async def test_generator_dispatch_type(self):
        with GraphOp(name="g") as g:
            gen = gen_items(items=PARENT["items"])
            START >> gen >> END
        g.build()
        assert g._ops["gen"].bound == "sync"


# ============================================================
# Test 3: Inline fan-out (both sync)
# ============================================================


class TestInlineFanout:
    @pytest.mark.asyncio
    async def test_fanout_both_sync(self):
        with GraphOp(name="g") as g:
            a = double(x=PARENT["x"])
            b = add_ten(x=PARENT["x"])
            m = merge(a=a["result"], b=b["result"])
            START >> [a, b] >> m >> END

        result = await Operon(g).run(inputs={"x": 5})
        # double(5) = 10, add_ten(5) = 15, merge = 25
        assert result["result"] == 25


# ============================================================
# Test 4: Mixed fan-out (sync + io)
# ============================================================


class TestMixedFanout:
    @pytest.mark.asyncio
    async def test_mixed_inline_and_io(self):
        with GraphOp(name="g") as g:
            a = double(x=PARENT["x"])  # inline
            b = async_double(x=PARENT["x"])  # task (io)
            m = merge(a=a["result"], b=b["result"])
            START >> [a, b] >> m >> END

        result = await Operon(g).run(inputs={"x": 5})
        assert result["result"] == 20  # 10 + 10


# ============================================================
# Test 5: IO-bound op works correctly
# ============================================================


class TestIOBound:
    @pytest.mark.asyncio
    async def test_io_bound_correct_result(self):
        with GraphOp(name="g") as g:
            d = async_double(x=PARENT["x"])
            START >> d >> END

        result = await Operon(g).run(inputs={"x": 7})
        assert result["result"] == 14

    @pytest.mark.asyncio
    async def test_io_bound_dispatch_type(self):
        with GraphOp(name="g") as g:
            d = async_double(x=PARENT["x"])
            START >> d >> END
        g.build()
        assert g._ops["d"].bound == "io"


# ============================================================
# Test 6: CPU-bound (executor="thread") runs in thread pool
# ============================================================


class TestCPUBound:
    @pytest.mark.asyncio
    async def test_thread_executor_different_thread(self):
        main_tid = threading.current_thread().ident

        with GraphOp(name="g") as g:
            step = thread_double(x=PARENT["x"])
            START >> step >> END

        result = await Operon(g).run(inputs={"x": 3})
        assert result["result"] == 6
        assert result["tid"] != main_tid

    @pytest.mark.asyncio
    async def test_thread_dispatch_type(self):
        with GraphOp(name="g") as g:
            step = thread_double(x=PARENT["x"])
            START >> step >> END
        g.build()
        assert g._ops["step"].bound == "cpu"


# ============================================================
# Test 7: Inline error propagation
# ============================================================


class TestInlineErrorPropagation:
    @pytest.mark.asyncio
    async def test_error_stored_in_state(self):
        with GraphOp(name="g") as g:
            step = failing_op(x=PARENT["x"])
            START >> step >> END

        engine = Operon(g)
        result = await engine.run(inputs={"x": 1})
        # Error should be captured, not crash engine
        state = result.get("$state")
        if state is not None:
            error = state[g._ops["step"].full_name, "error"]
            assert error is not None
            assert "intentional error" in str(error)


# ============================================================
# Test 8: Nested graph (inner inline, outer detects inline)
# ============================================================


class TestNestedGraph:
    @pytest.mark.asyncio
    async def test_nested_all_inline(self):
        @graph
        def inner(x):
            d = double(x=x)
            START >> d >> END

        with GraphOp(name="outer") as g:
            sub = inner(x=PARENT["x"])
            a = add_ten(x=sub["result"])
            START >> sub >> a >> END

        result = await Operon(g).run(inputs={"x": 5})
        assert result["result"] == 20  # double(5)=10, add_ten(10)=20

    @pytest.mark.asyncio
    async def test_nested_dispatch_type_auto(self):
        @graph
        def inner(x):
            d = double(x=x)
            START >> d >> END

        with GraphOp(name="outer") as g:
            sub = inner(x=PARENT["x"])
            START >> sub >> END
        g.build()
        # Inner graph has all inline children → auto inline
        assert g._ops["sub"].bound == "sync"

    @pytest.mark.asyncio
    async def test_nested_with_io_child_is_task(self):
        @graph
        def inner(x):
            d = async_double(x=x)  # io bound
            START >> d >> END

        with GraphOp(name="outer") as g:
            sub = inner(x=PARENT["x"])
            START >> sub >> END
        g.build()
        assert g._ops["sub"].bound == "io"


# ============================================================
# Test 9: Loop with inline ops
# ============================================================


class TestInlineLoop:
    @pytest.mark.asyncio
    async def test_loop_inline(self):
        from operon.core.states import StateSchema

        @op
        def increment(counter: int):
            return {"counter": counter + 1}

        with GraphOp.loop(name="counter", until="count >= 3", count=0) as g:
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
# Test 10: Branch routing with inline ops
# ============================================================


class TestInlineBranch:
    @pytest.mark.asyncio
    async def test_branch_routes_correctly(self):
        @op
        def get_score(x: int):
            return {"score": x}

        @op
        def handle_high(score: int):
            return {"label": "high"}

        @op
        def handle_low(score: int):
            return {"label": "low"}

        with GraphOp(name="g") as g:
            s = get_score(x=PARENT["x"])
            hi = handle_high(score=s["score"])
            lo = handle_low(score=s["score"])
            router = if_(s["score"] >= 50, "hi").else_("lo")

            START >> s >> router
            router >> hi >> END
            router >> lo >> END

        # High score
        result = await Operon(g).run(inputs={"x": 80})
        assert result["label"] == "high"

        # Low score
        result = await Operon(g).run(inputs={"x": 20})
        assert result["label"] == "low"


# ============================================================
# Test 11: GraphOp dispatch type — user override
# ============================================================


class TestGraphOpBoundOverride:
    @pytest.mark.asyncio
    async def test_graph_bound_io_forces_task(self):
        @graph(bound="io")
        def inner(x):
            d = double(x=x)  # all inline children
            START >> d >> END

        with GraphOp(name="outer") as g:
            sub = inner(x=PARENT["x"])
            START >> sub >> END
        g.build()
        # Should be io despite all children being sync
        assert g._ops["sub"].bound == "io"

    @pytest.mark.asyncio
    async def test_graph_bound_io_still_correct(self):
        @graph(bound="io")
        def inner(x):
            d = double(x=x)
            START >> d >> END

        with GraphOp(name="outer") as g:
            sub = inner(x=PARENT["x"])
            START >> sub >> END

        result = await Operon(g).run(inputs={"x": 5})
        assert result["result"] == 10


# ============================================================
# Test 12: Concurrent runs with inline ops (state isolation)
# ============================================================


class TestConcurrentInline:
    @pytest.mark.asyncio
    async def test_concurrent_runs_isolated(self):
        with GraphOp(name="g") as g:
            d = double(x=PARENT["x"])
            a = add_ten(x=d["result"])
            START >> d >> a >> END

        engine = Operon(g)
        results = await asyncio.gather(*[engine.run(inputs={"x": i}) for i in range(5)])
        for i, r in enumerate(results):
            assert r["result"] == i * 2 + 10
