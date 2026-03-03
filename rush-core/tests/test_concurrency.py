"""Concurrency tests — proving the GIL-free Rust backend achieves true parallelism.

These tests compare Python mode vs Rust mode under concurrent workloads.
The Rust backend must have lower wall-clock time (higher throughput) than Python.
"""

import asyncio
import os
import time


from hush.core import END, PARENT, START, GraphOp, Hush, op
from hush.core.ops.iteration.base import Each
from hush.core.ops.iteration.for_op import ForOp
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


@op(rust=f"{BUILTIN_CRATE}::hash_chain")
def hash_chain(data: str, iterations: int):
    """CPU-heavy op: iterated hashing."""
    import hashlib

    current = data
    for _ in range(iterations):
        current = hashlib.sha256(current.encode()).hexdigest()
    return {"hash": current}


@op(rust=f"{BUILTIN_CRATE}::add")
def add(a: int, b: int):
    return {"result": a + b}


@op(rust=f"{BUILTIN_CRATE}::square")
def square(n: int):
    return {"result": n * n}


@op(rust=f"{BUILTIN_CRATE}::increment")
def increment(x: int):
    return {"result": x + 1}


# =============================================================================
# Test 1: Concurrent runs — Rust vs Python wall-clock comparison
# =============================================================================


class TestConcurrentThroughput:
    """Compare wall-clock time for concurrent workflow runs: Rust must beat Python."""

    async def test_concurrent_cpu_work_rust_faster(self):
        """N concurrent hash_chain ops — Rust wall time < Python wall time.

        Uses CPU-heavy ops so compute time dominates serialization overhead.
        """
        with GraphOp(name="cpu_wf") as g:
            h = hash_chain(data=PARENT["data"], iterations=PARENT["iterations"])
            START >> h >> END

        N = 10
        ITERS = 2000

        # Python mode
        py_engine = Hush(g, mode="python")
        t0 = time.perf_counter()
        py_tasks = [
            py_engine.run(inputs={"data": f"py_{i}", "iterations": ITERS})
            for i in range(N)
        ]
        py_results = await asyncio.gather(*py_tasks)
        py_wall = time.perf_counter() - t0

        # Rust mode — GIL-free
        rs_engine = Hush(g, mode="rust")
        t0 = time.perf_counter()
        rs_tasks = [
            rs_engine.run(inputs={"data": f"rs_{i}", "iterations": ITERS})
            for i in range(N)
        ]
        rs_results = await asyncio.gather(*rs_tasks)
        rs_wall = time.perf_counter() - t0

        # Correctness: all produced hashes
        assert all("hash" in r for r in py_results)
        assert all("hash" in r for r in rs_results)

        # Throughput: Rust must be faster
        print(
            f"\n  [hash_chain x{N}] Python: {py_wall:.3f}s | Rust: {rs_wall:.3f}s | "
            f"Speedup: {py_wall / rs_wall:.2f}x"
        )
        assert rs_wall < py_wall, (
            f"Rust ({rs_wall:.3f}s) should be faster than Python ({py_wall:.3f}s)"
        )

    async def test_concurrent_map_heavy_rust_faster(self):
        """Concurrent workflows with MapOp iteration — Rust beats Python.

        Each workflow runs a MapOp with many items, so per-run time is significant.
        """
        with GraphOp(name="map_heavy") as g:
            with MapOp(
                name="m",
                inputs={"x": Each(PARENT["items"])},
            ) as m:
                d = double(x=PARENT["x"])
                START >> d >> END
            START >> m >> END

        N = 10
        ITEMS_PER_RUN = 50  # 50 iterations per workflow run

        py_engine = Hush(g, mode="python")
        t0 = time.perf_counter()
        py_tasks = [
            py_engine.run(inputs={"items": list(range(ITEMS_PER_RUN))})
            for _ in range(N)
        ]
        py_results = await asyncio.gather(*py_tasks)
        py_wall = time.perf_counter() - t0

        rs_engine = Hush(g, mode="rust")
        t0 = time.perf_counter()
        rs_tasks = [
            rs_engine.run(inputs={"items": list(range(ITEMS_PER_RUN))})
            for _ in range(N)
        ]
        rs_results = await asyncio.gather(*rs_tasks)
        rs_wall = time.perf_counter() - t0

        # Correctness
        assert len(py_results) == N
        assert len(rs_results) == N

        print(
            f"\n  [map x{N} ({ITEMS_PER_RUN} items each)] "
            f"Python: {py_wall:.3f}s | Rust: {rs_wall:.3f}s | "
            f"Speedup: {py_wall / rs_wall:.2f}x"
        )
        assert rs_wall < py_wall, (
            f"Rust ({rs_wall:.3f}s) should be faster than Python ({py_wall:.3f}s)"
        )

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
        """Same concurrent workload → same results in both modes."""
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


# =============================================================================
# Test 4: Concurrent with iteration ops
# =============================================================================


class TestConcurrentWithIteration:
    """Concurrent workflows containing iteration — parallelism within parallelism."""

    async def test_concurrent_map_op_rust_faster(self):
        """Concurrent workflows each containing MapOp — Rust beats Python."""
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
        t0 = time.perf_counter()
        py_tasks = [
            py_engine.run(inputs={"items": list(range(i, i + 5))}) for i in range(N)
        ]
        py_results = await asyncio.gather(*py_tasks)
        py_wall = time.perf_counter() - t0

        rs_engine = Hush(g, mode="rust")
        t0 = time.perf_counter()
        rs_tasks = [
            rs_engine.run(inputs={"items": list(range(i, i + 5))}) for i in range(N)
        ]
        rs_results = await asyncio.gather(*rs_tasks)
        rs_wall = time.perf_counter() - t0

        # Correctness
        assert len(py_results) == N
        assert len(rs_results) == N

        print(
            f"\n  [map_op x{N}] Python: {py_wall:.3f}s | Rust: {rs_wall:.3f}s | "
            f"Speedup: {py_wall / rs_wall:.2f}x"
        )
        assert rs_wall < py_wall, (
            f"Rust ({rs_wall:.3f}s) should be faster than Python ({py_wall:.3f}s)"
        )

    async def test_concurrent_for_op_rust_faster(self):
        """Concurrent workflows each containing ForOp with many items — Rust beats Python."""
        with GraphOp(name="for_wf") as g:
            with ForOp(
                name="loop",
                inputs={"x": Each(PARENT["items"])},
            ) as loop:
                inc = increment(x=PARENT["x"])
                START >> inc >> END
            START >> loop >> END

        N = 10
        ITEMS = 50  # enough iterations per run for compute to dominate

        py_engine = Hush(g, mode="python")
        t0 = time.perf_counter()
        py_tasks = [
            py_engine.run(inputs={"items": list(range(ITEMS))}) for _ in range(N)
        ]
        py_results = await asyncio.gather(*py_tasks)
        py_wall = time.perf_counter() - t0

        rs_engine = Hush(g, mode="rust")
        t0 = time.perf_counter()
        rs_tasks = [
            rs_engine.run(inputs={"items": list(range(ITEMS))}) for _ in range(N)
        ]
        rs_results = await asyncio.gather(*rs_tasks)
        rs_wall = time.perf_counter() - t0

        assert len(py_results) == N
        assert len(rs_results) == N

        print(
            f"\n  [for_op x{N} ({ITEMS} items)] Python: {py_wall:.3f}s | "
            f"Rust: {rs_wall:.3f}s | Speedup: {py_wall / rs_wall:.2f}x"
        )
        assert rs_wall < py_wall, (
            f"Rust ({rs_wall:.3f}s) should be faster than Python ({py_wall:.3f}s)"
        )


# =============================================================================
# Test 5: Sequential vs concurrent speedup (within Rust mode itself)
# =============================================================================


class TestSequentialVsConcurrent:
    """Concurrent Rust runs should be faster than sequential Rust runs."""

    async def test_concurrent_faster_than_sequential(self):
        """10 runs: concurrent wall time < sequential wall time in Rust.

        Uses CPU-heavy hash_chain so each run takes enough time to show
        the parallelism benefit of concurrent execution.
        """
        with GraphOp(name="seq_vs_ccu") as g:
            h = hash_chain(data=PARENT["data"], iterations=PARENT["iterations"])
            START >> h >> END

        engine = Hush(g, mode="rust")
        N = 10
        ITERS = 500_000  # enough CPU work per run (Rust DefaultHasher is very fast)

        # Sequential
        t0 = time.perf_counter()
        for i in range(N):
            await engine.run(inputs={"data": f"seq_{i}", "iterations": ITERS})
        seq_wall = time.perf_counter() - t0

        # Concurrent
        t0 = time.perf_counter()
        tasks = [
            engine.run(inputs={"data": f"ccu_{i}", "iterations": ITERS})
            for i in range(N)
        ]
        await asyncio.gather(*tasks)
        ccu_wall = time.perf_counter() - t0

        speedup = seq_wall / ccu_wall
        print(
            f"\n  [seq vs ccu x{N}] Sequential: {seq_wall:.3f}s | "
            f"Concurrent: {ccu_wall:.3f}s | Speedup: {speedup:.2f}x"
        )
        assert speedup > 1.3, f"Expected speedup > 1.3x, got {speedup:.2f}x"
