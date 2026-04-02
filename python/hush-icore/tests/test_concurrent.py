"""Concurrent execution tests — verify state isolation with 5 simultaneous engine.run() calls.

Each test builds ONE graph, creates ONE engine, then fires 5 concurrent requests
via asyncio.gather(). Asserts each request gets the correct result (no cross-talk).
"""

import asyncio

import pytest

from hush.core import END, PARENT, START, GraphOp, Hush, graph, op
from hush.core.ops.flow.branch_op import if_

CCU = 5


# =============================================================================
# Shared ops (defined once, reused across tests)
# =============================================================================


@op
def double(x: int):
    return {"result": x * 2}


@op
def add_ten(x: int):
    return {"result": x + 10}


@op
def square(x: int):
    return {"result": x * x}


@op
def merge(a: int, b: int):
    return {"result": a + b}


@op
def classify(x: int):
    return {"label": "pos" if x >= 0 else "neg", "x": x}


@op
def pos_handler(x: int):
    return {"result": x * 100}


@op
def neg_handler(x: int):
    return {"result": x * -1}


@op
def increment(counter: int):
    return {"counter": counter + 1}


@op
def gen_items(n: int):
    for i in range(n):
        yield {"item": i}


@op
def process_item(item: int):
    return {"result": item * 10}


@op
async def async_double(x: int):
    await asyncio.sleep(0.01)
    return {"result": x * 2}


# =============================================================================
# Test 1: Linear graph — A >> B >> C
# =============================================================================


class TestCcuLinearGraph:
    @pytest.mark.asyncio
    async def test_ccu_linear(self):
        """5 concurrent runs on a linear graph, each with different input."""
        with GraphOp(name="linear") as g:
            a = double(x=PARENT["x"])
            b = add_ten(x=a["result"])
            START >> a >> b >> END

        engine = Hush(g)
        results = await asyncio.gather(*[engine.run(inputs={"x": i}) for i in range(CCU)])

        for i, r in enumerate(results):
            expected = (i * 2) + 10
            assert r["result"] == expected, f"request {i}: expected {expected}, got {r['result']}"


# =============================================================================
# Test 2: Parallel fork-join — A >> [B, C] >> D
# =============================================================================


class TestCcuParallelForkJoin:
    @pytest.mark.asyncio
    async def test_ccu_fork_join(self):
        """5 concurrent runs on a fork-join graph."""
        with GraphOp(name="fork_join") as g:
            a = double(x=PARENT["x"])
            b = add_ten(x=a["result"])
            c = square(x=a["result"])
            d = merge(a=b["result"], b=c["result"])
            START >> a >> [b, c]
            [b, c] >> d >> END

        engine = Hush(g)
        results = await asyncio.gather(*[engine.run(inputs={"x": i}) for i in range(CCU)])

        for i, r in enumerate(results):
            doubled = i * 2
            expected = (doubled + 10) + (doubled * doubled)
            assert r["result"] == expected, f"request {i}: expected {expected}, got {r['result']}"


# =============================================================================
# Test 3: Branch routing — different inputs route to different branches
# =============================================================================


class TestCcuBranch:
    @pytest.mark.asyncio
    async def test_ccu_branch_routing(self):
        """5 concurrent runs: positive inputs → pos_handler, negative → neg_handler."""
        with GraphOp(name="branching") as g:
            c = classify(x=PARENT["x"])
            pos = pos_handler(x=c["x"])
            neg = neg_handler(x=c["x"])
            router = if_(c["label"] == "pos", pos).else_(neg)
            START >> c >> router
            router >> ~pos >> END
            router >> ~neg >> END

        engine = Hush(g)
        inputs = [-2, -1, 0, 1, 2]
        results = await asyncio.gather(*[engine.run(inputs={"x": x}) for x in inputs])

        for x, r in zip(inputs, results):
            if x >= 0:
                assert r["result"] == x * 100, f"x={x}: expected pos {x * 100}, got {r['result']}"
            else:
                assert r["result"] == x * -1, f"x={x}: expected neg {x * -1}, got {r['result']}"


# =============================================================================
# Test 4: Nested @graph — subgraph state isolation
# =============================================================================


class TestCcuNestedGraph:
    @pytest.mark.asyncio
    async def test_ccu_nested(self):
        """5 concurrent runs on nested @graph workflows."""

        @graph
        def double_flow(val):
            step = double(x=val)
            START >> step >> END

        with GraphOp(name="nested") as g:
            d1 = double_flow(val=PARENT["x"])
            d2 = double_flow(val=d1["result"])
            START >> d1 >> d2 >> END

        engine = Hush(g)
        results = await asyncio.gather(*[engine.run(inputs={"x": i}) for i in range(CCU)])

        for i, r in enumerate(results):
            expected = i * 4  # doubled twice
            assert r["result"] == expected, f"request {i}: expected {expected}, got {r['result']}"


# =============================================================================
# Test 5: Loop — concurrent loop iterations don't interfere
# =============================================================================


class TestCcuLoop:
    @pytest.mark.asyncio
    async def test_ccu_loop(self):
        """5 concurrent runs with loops of different lengths."""

        @op
        def inc_with_target(counter: int, target: int):
            return {"counter": counter + 1, "target": target}

        with GraphOp.loop(name="counter", until="counter >= target", counter=0, target=1) as g:
            inc = inc_with_target(counter=PARENT["counter"], target=PARENT["target"])
            inc["counter"] >> PARENT["counter"]
            inc["target"] >> PARENT["target"]
            START >> inc >> END

        engine = Hush(g)
        targets = [1, 2, 3, 4, 5]
        results = await asyncio.gather(*[engine.run(inputs={"target": t}) for t in targets])

        for t, r in zip(targets, results):
            assert r["counter"] == t, f"target={t}: expected counter={t}, got {r['counter']}"


# =============================================================================
# Test 6: Streaming — generator contexts isolated per request
# =============================================================================


class TestCcuStreaming:
    @pytest.mark.asyncio
    async def test_ccu_streaming(self):
        """5 concurrent runs with generators yielding different counts."""
        with GraphOp(name="streaming") as g:
            gen = gen_items(n=PARENT["n"])
            proc = process_item(item=gen["item"])
            START >> gen >> proc >> END

        engine = Hush(g)
        counts = [1, 2, 3, 4, 5]
        results = await asyncio.gather(*[engine.run(inputs={"n": n}) for n in counts])

        for n, r in zip(counts, results):
            expected = sorted([i * 10 for i in range(n)])
            actual = sorted(r["result"])
            assert actual == expected, f"n={n}: expected {expected}, got {actual}"


# =============================================================================
# Test 7: Async ops — concurrent async ops don't block each other
# =============================================================================


class TestCcuAsyncOps:
    @pytest.mark.asyncio
    async def test_ccu_async(self):
        """5 concurrent runs with async ops (sleep) complete in parallel."""
        with GraphOp(name="async_wf") as g:
            step = async_double(x=PARENT["x"])
            START >> step >> END

        engine = Hush(g)
        results = await asyncio.gather(*[engine.run(inputs={"x": i}) for i in range(CCU)])

        for i, r in enumerate(results):
            assert r["result"] == i * 2, f"request {i}: expected {i * 2}, got {r['result']}"
