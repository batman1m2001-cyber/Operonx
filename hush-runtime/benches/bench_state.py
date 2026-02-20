"""Microbenchmark: RustMemoryState vs Python MemoryState.

Isolates state access overhead — no scheduling, just set/get throughput.

Usage:
    cd hush-runtime
    uv run python benches/bench_state.py

Patterns:
    1. Raw state access (set/get N cells)
    2. State access during scheduling (linear graph, 1 read + 1 write per op)
    3. Multi-context iteration (C contexts × M variables)
"""

import asyncio
import statistics
import sys
import time

from hush.core import END, PARENT, START, Each, GraphOp, Hush, MapOp, graph, op
from hush.core.states import MemoryState, StateSchema


def check_deps():
    try:
        from hush_runtime import RustMemoryState, is_rust_available

        if not is_rust_available():
            print("ERROR: hush-runtime native module not built. Run: maturin develop --release")
            sys.exit(1)
    except ImportError:
        print("ERROR: hush-runtime not installed. Run: maturin develop --release")
        sys.exit(1)


# =============================================================================
# Shared ops
# =============================================================================


@op
def noop(x: int):
    return {"x": x}


@op
def multi_var(a: int, b: int, c: int, d: int, e: int):
    return {"sum": a + b + c + d + e, "product": a * b, "x": a}


# =============================================================================
# Pattern 1: Raw state access (no scheduling)
# =============================================================================


def bench_raw_state(n_cells: int, runs: int = 500):
    """Create a schema with N cells, measure set/get throughput."""
    # Build a schema with N ops, each with 1 var
    schema = StateSchema(name="raw_bench")
    schema._register("raw_bench", "input", None)
    for i in range(n_cells):
        schema._register(f"op{i}", "x", None)
        schema._register(f"op{i}", "result", None)
        schema._register(f"op{i}", "start_time", None)
        schema._register(f"op{i}", "end_time", None)
        schema._register(f"op{i}", "duration_ms", None)
        schema._register(f"op{i}", "cost_usd", None)
        schema._register(f"op{i}", "error", None)
    schema._build()

    # Python MemoryState
    py_times = []
    for _ in range(runs):
        state = schema.create_state(inputs={"input": 42})
        start = time.perf_counter_ns()
        for i in range(n_cells):
            state[f"op{i}", "result", None] = i * 2
        for i in range(n_cells):
            _ = state[f"op{i}", "result", None]
        elapsed = time.perf_counter_ns() - start
        py_times.append(elapsed / 1000)  # us

    # Rust MemoryState
    from hush_runtime import RustMemoryState

    rs_times = []
    for _ in range(runs):
        state = RustMemoryState.from_schema(schema, inputs={"input": 42})
        start = time.perf_counter_ns()
        for i in range(n_cells):
            state[f"op{i}", "result", None] = i * 2
        for i in range(n_cells):
            _ = state[f"op{i}", "result", None]
        elapsed = time.perf_counter_ns() - start
        rs_times.append(elapsed / 1000)  # us

    return statistics.mean(py_times), statistics.mean(rs_times)


# =============================================================================
# Pattern 2: State access during scheduling (end-to-end)
# =============================================================================


async def bench_e2e_state(n_ops: int, runs: int = 30):
    """Linear graph with N ops, each reads 1 input + writes 1 output."""
    with GraphOp(name=f"e2e_{n_ops}") as g:
        prev = noop(x=PARENT["x"], name="op0")
        START >> prev
        for i in range(1, n_ops):
            cur = noop(x=prev["x"], name=f"op{i}")
            prev >> cur
            prev = cur
        prev >> END

    py_times = []
    rs_times = []

    for mode, times in [("python", py_times), ("rust", rs_times)]:
        engine = Hush(g, mode=mode)
        # Warmup
        for _ in range(5):
            await engine.run(inputs={"x": 42})
        # Measure
        for _ in range(runs):
            start = time.perf_counter_ns()
            await engine.run(inputs={"x": 42})
            elapsed = time.perf_counter_ns() - start
            times.append(elapsed / 1_000_000)  # ms

    return statistics.mean(py_times), statistics.mean(rs_times)


# =============================================================================
# Pattern 3: Multi-context iteration
# =============================================================================


async def bench_iteration(n_items: int, runs: int = 30):
    """MapOp with n_items concurrent iterations."""
    @op
    def process(value: int):
        return {"out": value * 2}

    with GraphOp(name=f"iter_{n_items}") as g:
        with MapOp.of(
            value=Each(PARENT["items"]),
            max_concurrency=10,
            name="mapper",
        ) as mapper:
            node = process(value=PARENT["value"], name="proc")
            node["out"] >> PARENT["out"]
            START >> node >> END
        mapper["out"] >> PARENT["results"]
        START >> mapper >> END

    py_times = []
    rs_times = []
    items = list(range(n_items))

    for mode, times in [("python", py_times), ("rust", rs_times)]:
        engine = Hush(g, mode=mode)
        for _ in range(3):
            await engine.run(inputs={"items": items})
        for _ in range(runs):
            start = time.perf_counter_ns()
            await engine.run(inputs={"items": items})
            elapsed = time.perf_counter_ns() - start
            times.append(elapsed / 1_000_000)

    return statistics.mean(py_times), statistics.mean(rs_times)


# =============================================================================
# Pattern 4: Multi-var ops (heavy state reads/writes per op)
# =============================================================================


async def bench_multi_var(n_ops: int, runs: int = 30):
    """Linear chain of multi_var ops (5 reads + 3 writes per op)."""
    with GraphOp(name=f"multivar_{n_ops}") as g:
        prev = multi_var(
            a=PARENT["a"], b=PARENT["b"], c=PARENT["c"],
            d=PARENT["d"], e=PARENT["e"], name="op0",
        )
        START >> prev
        for i in range(1, n_ops):
            cur = multi_var(
                a=prev["sum"], b=prev["product"], c=prev["x"],
                d=PARENT["d"], e=PARENT["e"], name=f"op{i}",
            )
            prev >> cur
            prev = cur
        prev >> END

    py_times = []
    rs_times = []
    inputs = {"a": 1, "b": 2, "c": 3, "d": 4, "e": 5}

    for mode, times in [("python", py_times), ("rust", rs_times)]:
        engine = Hush(g, mode=mode)
        for _ in range(5):
            await engine.run(inputs=inputs)
        for _ in range(runs):
            start = time.perf_counter_ns()
            await engine.run(inputs=inputs)
            elapsed = time.perf_counter_ns() - start
            times.append(elapsed / 1_000_000)

    return statistics.mean(py_times), statistics.mean(rs_times)


# =============================================================================
# Runner
# =============================================================================


def print_row(label: str, py_us, rs_us, unit="us"):
    speedup = py_us / rs_us if rs_us > 0 else float("inf")
    print(f"  {label:>35s} | Py={py_us:10.1f}{unit} | Rs={rs_us:10.1f}{unit} | speedup={speedup:.2f}x")


async def main():
    check_deps()

    print("=" * 90)
    print("  State Microbenchmark: Python MemoryState vs RustMemoryState")
    print("=" * 90)

    # --- Pattern 1: Raw state access ---
    print("\n  Pattern 1: Raw state set/get (no scheduling)")
    print(f"  {'Label':>35s} | {'Py':>12s} | {'Rs':>12s} | {'Speedup':>10s}")
    print(f"  {'-'*35}-+-{'-'*12}-+-{'-'*12}-+-{'-'*10}")
    for n in [100, 500, 1000]:
        py, rs = bench_raw_state(n)
        print_row(f"raw set+get ({n} cells)", py, rs)

    # --- Pattern 2: E2E linear ---
    print("\n  Pattern 2: End-to-end linear (1 read + 1 write per op)")
    print(f"  {'Label':>35s} | {'Py':>12s} | {'Rs':>12s} | {'Speedup':>10s}")
    print(f"  {'-'*35}-+-{'-'*12}-+-{'-'*12}-+-{'-'*10}")
    for n in [10, 50, 100]:
        py, rs = await bench_e2e_state(n)
        print_row(f"e2e linear ({n} ops)", py, rs, unit="ms")

    # --- Pattern 3: Iteration ---
    print("\n  Pattern 3: MapOp iteration (concurrent contexts)")
    print(f"  {'Label':>35s} | {'Py':>12s} | {'Rs':>12s} | {'Speedup':>10s}")
    print(f"  {'-'*35}-+-{'-'*12}-+-{'-'*12}-+-{'-'*10}")
    for n in [10, 50, 100]:
        py, rs = await bench_iteration(n)
        print_row(f"MapOp ({n} items)", py, rs, unit="ms")

    # --- Pattern 4: Multi-var ---
    print("\n  Pattern 4: Multi-var ops (5 reads + 3 writes per op)")
    print(f"  {'Label':>35s} | {'Py':>12s} | {'Rs':>12s} | {'Speedup':>10s}")
    print(f"  {'-'*35}-+-{'-'*12}-+-{'-'*12}-+-{'-'*10}")
    for n in [10, 50, 100]:
        py, rs = await bench_multi_var(n)
        print_row(f"multi-var linear ({n} ops)", py, rs, unit="ms")

    print("\n" + "=" * 90)
    print("  Done.")
    print("=" * 90)


if __name__ == "__main__":
    asyncio.run(main())
