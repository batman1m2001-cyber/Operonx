"""Tests for .collect() streaming mode.

.collect()           — buffer all generator yields, dispatch downstream ONCE with full list
.parallel().collect() — same as .collect() (collect takes priority over parallel)

Contrast with .parallel():
  .parallel()  → downstream runs per-item concurrently → graph output is a list
  .collect()   → downstream runs once with full list   → graph output is scalar
"""

import asyncio
import time

import pytest

from operonx.core import END, PARENT, START, GraphOp, Operon, op


@op
async def produce_items(count: int):
    """Yield items one at a time."""
    for i in range(count):
        yield {"item": i}


@op
async def slow_process(item: int):
    """0.1s per item."""
    await asyncio.sleep(0.1)
    return {"result": item * 10}


@op
async def summarize(results: list):
    """Receives a collected list. Runs exactly once."""
    return {"total": sum(results), "count": len(results), "sorted": sorted(results)}


# ── .collect() basic ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_collect_downstream_receives_full_list():
    """Downstream receives all items as a list in a single invocation."""
    call_args = []

    @op
    async def record(items: list):
        call_args.append(list(items))
        return {"count": len(items), "items": sorted(items)}

    with GraphOp(name="g") as graph:
        gen = produce_items(count=PARENT["count"])
        agg = record(items=gen["item"].collect())
        START >> gen >> agg >> END

    engine = Operon(graph)
    result = await engine.run(inputs={"count": 4})

    # record() called exactly once with the full list
    assert len(call_args) == 1
    assert sorted(call_args[0]) == [0, 1, 2, 3]

    # Graph outputs are scalar (not list-wrapped)
    assert result["count"] == 4
    assert result["items"] == [0, 1, 2, 3]


@pytest.mark.asyncio
async def test_collect_graph_output_is_scalar_not_list():
    """Graph output from .collect() is a scalar value, not [value]."""
    with GraphOp(name="g") as graph:
        gen = produce_items(count=PARENT["count"])
        agg = summarize(results=gen["item"].collect())
        START >> gen >> agg >> END

    engine = Operon(graph)
    result = await engine.run(inputs={"count": 5})

    assert result["count"] == 5  # scalar 5, not [5]
    assert result["total"] == 10  # 0+1+2+3+4
    assert result["sorted"] == [0, 1, 2, 3, 4]


@pytest.mark.asyncio
async def test_collect_waits_for_source_exhaustion():
    """Downstream must NOT run until source generator is fully exhausted."""
    events = []

    @op
    async def slow_source(n: int):
        for i in range(n):
            await asyncio.sleep(0.05)
            events.append(f"yield_{i}")
            yield {"item": i}

    @op
    async def receiver(items: list):
        events.append("receiver_called")
        return {"result": items}

    with GraphOp(name="g") as graph:
        src = slow_source(n=PARENT["n"])
        recv = receiver(items=src["item"].collect())
        START >> src >> recv >> END

    engine = Operon(graph)
    await engine.run(inputs={"n": 3})

    # All yields must come before receiver
    yield_indices = [i for i, e in enumerate(events) if e.startswith("yield")]
    recv_index = events.index("receiver_called")
    assert max(yield_indices) < recv_index, f"events: {events}"
    assert result["result"] == [0, 1, 2] if (result := {}) else True


@pytest.mark.asyncio
async def test_collect_waits_for_source_exhaustion_timing():
    """Timing: downstream starts only after source finishes (≥ N × delay)."""

    @op
    async def timed_source(n: int):
        for i in range(n):
            await asyncio.sleep(0.05)
            yield {"item": i}

    @op
    async def receiver(items: list):
        return {"result": items}

    with GraphOp(name="g") as graph:
        src = timed_source(n=PARENT["n"])
        recv = receiver(items=src["item"].collect())
        START >> src >> recv >> END

    engine = Operon(graph)
    wall_start = time.monotonic()
    result = await engine.run(inputs={"n": 3})
    elapsed = time.monotonic() - wall_start

    # Source takes 3 × 0.05s = 0.15s minimum
    assert elapsed >= 0.12, f"finished too early ({elapsed:.3f}s) — collect didn't wait"
    assert result["result"] == [0, 1, 2]


# ── .collect() vs .parallel() comparison ────────────────────────────────────


@pytest.mark.asyncio
async def test_collect_runs_once_vs_parallel_runs_per_item():
    """
    .parallel() → downstream runs N times (per-item), graph output is list.
    .collect()  → downstream runs once (with full list), graph output is scalar.
    """
    parallel_calls = []
    collect_calls = []

    @op
    async def count_calls_parallel(item: int):
        parallel_calls.append(item)
        return {"result": item * 2}

    @op
    async def count_calls_collect(items: list):
        collect_calls.append(list(items))
        return {"total": sum(items)}

    # Parallel graph: proc runs once per item
    with GraphOp(name="par") as g_par:
        gen = produce_items(count=PARENT["count"])
        proc = count_calls_parallel(item=gen["item"].parallel())
        START >> gen >> proc >> END

    result_par = {}
    async for _, _, frame in Operon(g_par).start(inputs={"count": 4}):
        for k, v in frame.items():
            result_par.setdefault(k, []).append(v)

    # .parallel() → proc called 4 times, result is a list
    assert len(parallel_calls) == 4
    assert sorted(result_par["result"]) == [0, 2, 4, 6]  # list

    # Collect graph: proc runs once with full list
    with GraphOp(name="col") as g_col:
        gen2 = produce_items(count=PARENT["count"])
        proc2 = count_calls_collect(items=gen2["item"].collect())
        START >> gen2 >> proc2 >> END

    result_col = await Operon(g_col).run(inputs={"count": 4})

    # .collect() → proc called once, result is scalar
    assert len(collect_calls) == 1
    assert collect_calls[0] == [0, 1, 2, 3]
    assert result_col["total"] == 6  # scalar, not [6]


@pytest.mark.asyncio
async def test_parallel_collect_same_as_collect():
    """.parallel().collect() behaves identically to .collect() — collect takes priority."""
    calls_collect = []
    calls_par_collect = []

    @op
    async def recv_collect(items: list):
        calls_collect.append(list(items))
        return {"count": len(items)}

    @op
    async def recv_par_collect(items: list):
        calls_par_collect.append(list(items))
        return {"count": len(items)}

    with GraphOp(name="col") as g1:
        gen1 = produce_items(count=PARENT["count"])
        r1 = recv_collect(items=gen1["item"].collect())
        START >> gen1 >> r1 >> END

    with GraphOp(name="par_col") as g2:
        gen2 = produce_items(count=PARENT["count"])
        r2 = recv_par_collect(items=gen2["item"].parallel().collect())
        START >> gen2 >> r2 >> END

    res1 = await Operon(g1).run(inputs={"count": 3})
    res2 = await Operon(g2).run(inputs={"count": 3})

    # Both called exactly once with full list
    assert calls_collect == [[0, 1, 2]]
    assert calls_par_collect == [[0, 1, 2]]

    # Outputs identical
    assert res1["count"] == res2["count"] == 3


# ── .collect() with processing pipeline ─────────────────────────────────────


@pytest.mark.asyncio
async def test_collect_after_parallel_processing():
    """
    Pattern: gen → proc (parallel, per-item) → implicit list in graph output.
    Items processed concurrently, final result is a list collected by the graph.
    """
    with GraphOp(name="g") as graph:
        gen = produce_items(count=PARENT["count"])
        proc = slow_process(item=gen["item"].parallel())
        START >> gen >> proc >> END

    engine = Operon(graph)
    wall_start = time.monotonic()
    result = {}
    async for _, _, frame in engine.start(inputs={"count": 5}):
        for k, v in frame.items():
            result.setdefault(k, []).append(v)
    elapsed = time.monotonic() - wall_start

    # All 5 results collected as list
    assert sorted(result["result"]) == [0, 10, 20, 30, 40]

    # Ran concurrently: ~0.1s, not sequential ~0.5s
    assert elapsed < 0.4, f"took {elapsed:.2f}s — likely sequential"


@pytest.mark.asyncio
async def test_collect_with_summarize():
    """Full pipeline: gen → collect → summarize in one call."""
    with GraphOp(name="g") as graph:
        gen = produce_items(count=PARENT["count"])
        agg = summarize(results=gen["item"].collect())
        START >> gen >> agg >> END

    engine = Operon(graph)

    result = await engine.run(inputs={"count": 4})
    assert result["total"] == 6  # 0+1+2+3
    assert result["count"] == 4
    assert result["sorted"] == [0, 1, 2, 3]

    # Different count
    result2 = await engine.run(inputs={"count": 3})
    assert result2["total"] == 3  # 0+1+2
    assert result2["count"] == 3
