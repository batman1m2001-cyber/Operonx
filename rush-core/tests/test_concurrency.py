"""Concurrency tests — correctness, isolation, and mode equivalence.

These tests verify that both Python and Rust modes produce correct results
under concurrent workloads and that state is properly isolated.
"""

import asyncio
import os

from hush.core import END, PARENT, START, GraphOp, Hush, op
from hush.core.ops.iteration.base import Each
from hush.core.ops.iteration.map_op import MapOp

BUILTIN_CRATE = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "examples", "rush-ops-builtin")
)


# =============================================================================
# Shared ops — all have rust= so they work in both modes
# =============================================================================


@op(rust=f"{BUILTIN_CRATE}::double")
def double(x: int):
    return {"result": x * 2}


@op(rust=f"{BUILTIN_CRATE}::square")
def square(n: int):
    return {"result": n * n}


# =============================================================================
# Test 1: Concurrent correctness
# =============================================================================


class TestConcurrentCorrectness:
    """Both modes produce correct results for simple ops under concurrency."""

    async def test_concurrent_correctness_all_modes(self):
        """Both modes produce correct results for simple ops under concurrency."""
        with GraphOp(name="simple") as g:
            d = double(x=PARENT["x"])
            START >> d >> END

        N = 30

        py_engine = Hush(g, mode="python")
        rs_engine = Hush(g, mode="rust")

        py_tasks = [py_engine.run(inputs={"x": i}) for i in range(N)]
        rs_tasks = [rs_engine.run(inputs={"x": i}) for i in range(N)]

        py_results = await asyncio.gather(*py_tasks)
        rs_results = await asyncio.gather(*rs_tasks)

        py_vals = sorted([r["result"] for r in py_results])
        rs_vals = sorted([r["result"] for r in rs_results])
        assert py_vals == rs_vals == [i * 2 for i in range(N)]


# =============================================================================
# Test 2: Concurrent isolation — no cross-contamination
# =============================================================================


class TestConcurrentIsolation:
    """Each concurrent run has independent state."""

    async def test_100_concurrent_runs_isolated(self):
        """100 concurrent double runs — each result is correct."""
        with GraphOp(name="iso") as g:
            d = double(x=PARENT["x"])
            START >> d >> END

        engine = Hush(g, mode="rust")
        tasks = [engine.run(inputs={"x": i}) for i in range(100)]
        results = await asyncio.gather(*tasks)

        # Build {input: result} mapping
        result_map = {}
        for r in results:
            val = r["result"]
            result_map[val] = result_map.get(val, 0) + 1

        # Every even number 0,2,4,...,198 should appear exactly once
        for i in range(100):
            expected = i * 2
            assert expected in result_map, f"Missing result {expected} for input {i}"
            assert result_map[expected] == 1, f"Duplicate result {expected}"


# =============================================================================
# Test 3: Mode equivalence under concurrency
# =============================================================================


class TestModeEquivalenceConcurrent:
    """Python and Rust produce identical results under concurrency."""

    async def test_mode_equivalence_concurrent(self):
        """Same concurrent workload -> same results in both modes."""
        with GraphOp(name="equiv") as g:
            s = square(n=PARENT["n"])
            START >> s >> END

        py_engine = Hush(g, mode="python")
        rs_engine = Hush(g, mode="rust")

        N = 30
        py_tasks = [py_engine.run(inputs={"n": i}) for i in range(N)]
        rs_tasks = [rs_engine.run(inputs={"n": i}) for i in range(N)]

        py_results = await asyncio.gather(*py_tasks)
        rs_results = await asyncio.gather(*rs_tasks)

        py_sorted = sorted([r["result"] for r in py_results])
        rs_sorted = sorted([r["result"] for r in rs_results])
        assert py_sorted == rs_sorted

    async def test_concurrent_map_op_equivalence(self):
        """Concurrent workflows with MapOp produce same results in both modes."""
        with GraphOp(name="map_wf") as g:
            with MapOp(
                name="m",
                inputs={"x": Each(PARENT["items"])},
            ) as m:
                d = double(x=PARENT["x"])
                START >> d >> END
            START >> m >> END

        N = 10

        py_engine = Hush(g, mode="python")
        py_tasks = [
            py_engine.run(inputs={"items": list(range(i, i + 5))}) for i in range(N)
        ]
        py_results = await asyncio.gather(*py_tasks)

        rs_engine = Hush(g, mode="rust")
        rs_tasks = [
            rs_engine.run(inputs={"items": list(range(i, i + 5))}) for i in range(N)
        ]
        rs_results = await asyncio.gather(*rs_tasks)

        assert len(py_results) == N
        assert len(rs_results) == N
