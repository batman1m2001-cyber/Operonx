"""Regression tests for streaming refactor bugs (Phases 2-4).

One test per bug to ensure they never re-appear:
  BUG-1  _on_yield missing from __slots__ → AttributeError on BranchOp
  BUG-2  Cache hit skips store_result() → downstream reads None
  BUG-3  yield_waiter + gen_task race → first yield lost from collect_buffers
  BUG-4  yield_queue not drained before collect_buffers check → items missed
  BUG-5  collect stored to dst_op namespace → downstream reads None
  BUG-6  __collect__ context output wrapped in list instead of scalar
"""

import asyncio

import pytest

from operonx.core import END, PARENT, START, GraphOp, Operon, op

# ── BUG-2 ───────────────────────────────────────────────────────────────────
# Cache hit path skipped store_result() → downstream received None


@pytest.mark.asyncio
async def test_bug2_cache_hit_propagates_to_downstream():
    """Downstream must receive the cached value, not None."""
    call_count = []

    @op(cache=True)
    async def cached_source(x: int):
        call_count.append(x)
        return {"value": x * 10}

    @op
    async def consumer(value: int):
        return {"result": value + 1}

    with GraphOp(name="g") as graph:
        src = cached_source(x=PARENT["x"])
        cons = consumer(value=src["value"])
        START >> src >> cons >> END

    engine = Operon(graph)

    # First run — cache miss, executes
    result1 = await engine.run(inputs={"x": 5})
    assert result1["result"] == 51, f"first run wrong: {result1}"
    assert len(call_count) == 1

    # Second run — cache hit; downstream must still get value=50
    result2 = await engine.run(inputs={"x": 5})
    assert result2["result"] == 51, (
        f"BUG-2 regression: cache hit did not propagate to downstream, got {result2}"
    )
    assert len(call_count) == 1, "cached_source should not have run again"


# ── BUG-3 ───────────────────────────────────────────────────────────────────
# yield_waiter + gen_task complete in same asyncio.wait batch →
# first item removed from queue by yield_waiter but gen_task processed first →
# collect_buffers missing item [0]


@pytest.mark.asyncio
async def test_bug3_first_yield_included_in_collect():
    """All yielded items, including the first, must appear in collected list."""

    @op
    async def fast_gen(n: int):
        """Yields all items without delay — maximises chance of race."""
        for i in range(n):
            yield {"item": i}

    @op
    async def receiver(items: list):
        return {"items": sorted(items), "count": len(items)}

    with GraphOp(name="g") as graph:
        gen = fast_gen(n=PARENT["n"])
        recv = receiver(items=gen["item"].collect())
        START >> gen >> recv >> END

    # Run multiple times to expose any race condition
    engine = Operon(graph)
    for trial in range(5):
        result = await engine.run(inputs={"n": 4})
        assert result["count"] == 4, (
            f"BUG-3 regression (trial {trial}): expected 4 items, got {result['count']}. "
            f"items={result['items']} — first yield was likely lost from collect_buffers"
        )
        assert result["items"] == [0, 1, 2, 3], (
            f"BUG-3 regression (trial {trial}): items={result['items']}"
        )


# ── BUG-4 ───────────────────────────────────────────────────────────────────
# yield_queue not drained before collect_buffers check →
# buffered yield events never reach collect_buffers → collect list is empty


@pytest.mark.asyncio
async def test_bug4_all_buffered_yields_collected():
    """Collect must include items queued-but-not-yet-processed when gen completes."""

    @op
    async def burst_gen(n: int):
        """Yields all items in rapid succession — fills queue before scheduler drains."""
        for i in range(n):
            yield {"item": i}
            # No sleep: force multiple items into queue simultaneously

    @op
    async def receiver(items: list):
        return {"count": len(items), "items": sorted(items)}

    with GraphOp(name="g") as graph:
        gen = burst_gen(n=6)
        recv = receiver(items=gen["item"].collect())
        START >> gen >> recv >> END

    engine = Operon(graph)
    result = await engine.run(inputs={})

    assert result["count"] == 6, (
        f"BUG-4 regression: expected 6 items, got {result['count']}. "
        f"items={result['items']} — yield_queue was not drained before collect_buffers check"
    )
    assert result["items"] == [0, 1, 2, 3, 4, 5]


# ── BUG-5 ───────────────────────────────────────────────────────────────────
# collect stored to dst_op namespace → downstream get_inputs() resolves Ref
# pointing to src_op namespace → reads None


@pytest.mark.asyncio
async def test_bug5_collect_downstream_receives_non_none_input():
    """Downstream op must receive the collected list, not None or empty."""
    received_inputs = []

    @op
    async def source(n: int):
        for i in range(n):
            yield {"val": i}

    @op
    async def sink(values: list):
        received_inputs.append(list(values))
        return {"total": sum(values)}

    with GraphOp(name="g") as graph:
        src = source(n=PARENT["n"])
        snk = sink(values=src["val"].collect())
        START >> src >> snk >> END

    engine = Operon(graph)
    result = await engine.run(inputs={"n": 3})

    assert len(received_inputs) == 1, "sink must be called exactly once"
    assert received_inputs[0] == [0, 1, 2], (
        f"BUG-5 regression: sink received {received_inputs[0]} instead of [0,1,2]. "
        "Collected values were likely stored in dst_op namespace instead of src_op."
    )
    assert result["total"] == 3


# ── BUG-6 ───────────────────────────────────────────────────────────────────
# __collect__ context output appended to per_item_vals list →
# outputs["count"] = [5] instead of 5


@pytest.mark.asyncio
async def test_bug6_collect_graph_output_is_scalar_not_list():
    """Graph output from a .collect() downstream must be scalar, never [value]."""

    @op
    async def gen(n: int):
        for i in range(n):
            yield {"item": i}

    @op
    async def aggregator(items: list):
        return {"count": len(items), "total": sum(items)}

    with GraphOp(name="g") as graph:
        g = gen(n=PARENT["n"])
        agg = aggregator(items=g["item"].collect())
        START >> g >> agg >> END

    engine = Operon(graph)
    result = await engine.run(inputs={"n": 5})

    # count must be 5, not [5]
    assert result["count"] == 5, (
        f"BUG-6 regression: result['count'] = {result['count']!r}. "
        "Expected scalar 5, not [5]. __collect__ context output was wrapped in a list."
    )
    assert isinstance(result["count"], int), (
        f"BUG-6 regression: result['count'] is {type(result['count'])}, expected int"
    )
    assert result["total"] == 10  # 0+1+2+3+4
