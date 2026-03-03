"""End-to-end benchmark: Python mode vs Rust mode.

Stress tests with nested @graph subgraphs, parallel branches,
if_() routing, ForOp loops, CPU-bound contention — mirroring real production patterns.

Usage:
    uv run python benches/bench_e2e.py

Requirements:
    hush-core and rush-core must be installed.
"""

import asyncio
import hashlib
import math
import os
import statistics
import sys
import time
import tracemalloc

from hush.core import END, PARENT, START, GraphOp, Hush, graph, op
from hush.core.ops.flow.branch_op import Branch
from hush.core.ops.iteration import Each, ForOp

BUILTIN_CRATE = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "examples", "rush-ops-builtin")
)


def check_deps():
    try:
        from rush_core import is_rust_available

        if not is_rust_available():
            print(
                "ERROR: rush-core native module not built. Run: maturin develop --release"
            )
            sys.exit(1)
    except ImportError:
        print("ERROR: rush-core not installed. Run: maturin develop --release")
        sys.exit(1)


# =============================================================================
# Reusable @op functions
# =============================================================================


@op(rust=f"{BUILTIN_CRATE}::bench_noop")
def noop(x: int):
    return {"x": x}


@op(rust=f"{BUILTIN_CRATE}::classify")
def classify(score: int):
    """Classify score into grade bucket."""
    if score >= 90:
        grade = "excellent"
    elif score >= 70:
        grade = "good"
    elif score >= 50:
        grade = "average"
    else:
        grade = "fail"
    return {"grade": grade, "score": score}


@op(rust=f"{BUILTIN_CRATE}::process_grade")
def process_grade(grade: str, score: int):
    return {"result": f"{grade}:{score}"}


@op(rust=f"{BUILTIN_CRATE}::aggregate")
def aggregate(results: list):
    return {"summary": len(results or [])}


@op(rust=f"{BUILTIN_CRATE}::bench_transform")
def transform(item: str, prefix: str):
    return {"output": f"{prefix}-{item}"}


@op(rust=f"{BUILTIN_CRATE}::merge_two")
def merge_two(a, b):
    """Merge two values into a single output."""
    return {"merged": a, "x": a}


@op(rust=f"{BUILTIN_CRATE}::combine_all")
def combine_all(
    r1: dict = None,
    r2: dict = None,
    r3: dict = None,
    r4: dict = None,
    r5: dict = None,
):
    """Aggregate up to 5 parallel branch results."""
    parts = [r for r in [r1, r2, r3, r4, r5] if r is not None]
    return {"combined": parts, "count": len(parts)}


# =============================================================================
# CPU-bound @op functions (for stress testing under contention)
# =============================================================================


@op(executor="thread", rust=f"{BUILTIN_CRATE}::cpu_hash_chain")
def cpu_hash_chain(x: int, iterations: int):
    """Chain SHA-256 hashes — pure CPU, no I/O."""
    data = str(x).encode()
    for _ in range(iterations):
        data = hashlib.sha256(data).digest()
    return {"hash": data.hex()[:16], "x": x}


@op(executor="thread", rust=f"{BUILTIN_CRATE}::cpu_prime_sieve")
def cpu_prime_sieve(limit: int):
    """Sieve of Eratosthenes up to limit — heavy memory + CPU."""
    sieve = [True] * (limit + 1)
    sieve[0] = sieve[1] = False
    for i in range(2, int(math.isqrt(limit)) + 1):
        if sieve[i]:
            for j in range(i * i, limit + 1, i):
                sieve[j] = False
    count = sum(sieve)
    return {"prime_count": count}


@op(executor="thread", rust=f"{BUILTIN_CRATE}::cpu_matrix_mult")
def cpu_matrix_mult(size: int):
    """Naive matrix multiplication — O(n^3) CPU burn."""
    a = [[float(i + j) for j in range(size)] for i in range(size)]
    b = [[float(i * j + 1) for j in range(size)] for i in range(size)]
    c = [[0.0] * size for _ in range(size)]
    for i in range(size):
        for j in range(size):
            s = 0.0
            for k in range(size):
                s += a[i][k] * b[k][j]
            c[i][j] = s
    return {"trace": sum(c[i][i] for i in range(size))}


@op(rust=f"{BUILTIN_CRATE}::cpu_fibonacci")
def cpu_fibonacci(n: int):
    """Iterative fibonacci — lightweight CPU."""
    a, b = 0, 1
    for _ in range(n):
        a, b = b, a + b
    return {"fib": a % (10**9 + 7)}


# =============================================================================
# Pattern 1: Linear chain (baseline)
# =============================================================================


def build_linear(n: int):
    with GraphOp(name=f"linear_{n}") as g:
        prev = noop(x=PARENT["x"], name="op0")
        START >> prev
        for i in range(1, n):
            cur = noop(x=prev["x"], name=f"op{i}")
            prev >> cur
            prev = cur
        prev >> END
    return g


# =============================================================================
# Pattern 2: Nested @graph — 3-level deep subgraphs
# =============================================================================


@graph
def inner_pipeline(x):
    """Innermost: 3-step chain."""
    a = noop(x=x, name="inner_a")
    b = noop(x=a["x"], name="inner_b")
    c = noop(x=b["x"], name="inner_c")
    START >> a >> b >> c >> END


@graph
def mid_pipeline(x):
    """Middle: calls inner_pipeline twice in parallel, merges."""
    left = inner_pipeline(x=x, name="left")
    right = inner_pipeline(x=x, name="right")
    m = merge_two(a=left["x"], b=right["x"], name="merge")
    START >> [left, right] >> m >> END


def build_nested(n: int):
    """Top-level: chain of n mid_pipeline subgraphs (each = 2 inner x 3 ops + merge = 9 nodes)."""
    with GraphOp(name=f"nested_{n}") as g:
        prev = mid_pipeline(x=PARENT["x"], name="stage0")
        START >> prev
        for i in range(1, n):
            cur = mid_pipeline(x=prev["x"], name=f"stage{i}")
            prev >> cur
            prev = cur
        prev >> END
    return g


# =============================================================================
# Pattern 3: Wide parallel fan-out with nested subgraphs
# =============================================================================


def build_parallel_nested(n: int):
    """n parallel inner_pipeline calls -> single aggregation.
    Each branch has 3 internal ops, total ~ n*3 + 1 ops.
    """
    with GraphOp(name=f"parallel_nested_{n}") as g:
        branches = []
        for i in range(n):
            branch = inner_pipeline(x=PARENT["x"], name=f"branch{i}")
            START >> branch
            branches.append(branch)
        # aggregate last 5 branches (or fewer)
        agg = combine_all(
            r1=branches[0]["x"] if len(branches) > 0 else None,
            r2=branches[1]["x"] if len(branches) > 1 else None,
            r3=branches[2]["x"] if len(branches) > 2 else None,
            r4=branches[3]["x"] if len(branches) > 3 else None,
            r5=branches[-1]["x"] if len(branches) > 4 else None,
            name="agg",
        )
        for b in branches:
            b >> agg
        agg >> END
    return g


# =============================================================================
# Pattern 4: Branching — if_() routing with 4 paths
# =============================================================================


def build_branching(n: int):
    """n sequential classify-and-route stages. Each stage:
    classify -> if_() -> 4 branches -> merge. Total ~ n*7 ops.
    """
    with GraphOp(name=f"branching_{n}") as g:
        prev_out = PARENT["score"]
        first = None
        last = None

        for i in range(n):
            cls = classify(score=prev_out, name=f"cls{i}")
            router = (
                Branch(name=f"r{i}")
                .if_(cls["grade"] == "excellent", f"exc{i}")
                .if_(cls["grade"] == "good", f"good{i}")
                .if_(cls["grade"] == "average", f"avg{i}")
                .else_(f"fail{i}")
            )

            exc = process_grade(grade=cls["grade"], score=cls["score"], name=f"exc{i}")
            good = process_grade(
                grade=cls["grade"], score=cls["score"], name=f"good{i}"
            )
            avg = process_grade(grade=cls["grade"], score=cls["score"], name=f"avg{i}")
            fail = process_grade(
                grade=cls["grade"], score=cls["score"], name=f"fail{i}"
            )

            if first is None:
                START >> cls
                first = cls
            else:
                last >> cls

            cls >> router
            router >> [exc, good, avg, fail]

            # Merge point — soft edges since only 1 branch runs
            merge = noop(x=cls["score"], name=f"merge{i}")
            [exc, good, avg, fail] >> ~merge

            prev_out = merge["x"]
            last = merge

        last >> END
    return g


# =============================================================================
# Pattern 5: ForOp sequential loop with nested graph body
# =============================================================================


def build_for_loop(n: int):
    """ForOp iterating over n items sequentially."""
    with GraphOp(name=f"for_loop_{n}") as g:
        with ForOp(
            name="loop",
            inputs={"item": Each(PARENT["items"]), "prefix": PARENT["prefix"]},
        ) as loop:
            t = transform(item=PARENT["item"], prefix=PARENT["prefix"], name="xform")
            t["output"] >> PARENT["output"]
            START >> t >> END

        loop["output"] >> PARENT["results"]
        START >> loop >> END
    return g


# =============================================================================
# Pattern 6: Production-like workflow
#   init -> [5 parallel verification @graph subgraphs] -> aggregate -> post-process
#   Each verification = classify -> branch -> 2 paths -> merge (5-7 nodes)
#   Total: ~40-50 nodes
# =============================================================================


@graph
def verify_case(x, threshold):
    """Single verification subgraph: classify -> branch -> 2 paths -> merge."""
    cls = classify(score=x, name="cls")
    router = (
        Branch(name="router").if_(cls["score"] >= threshold, "pass_op").else_("fail_op")
    )
    pass_op = process_grade(grade=cls["grade"], score=cls["score"], name="pass_op")
    fail_op = process_grade(grade=cls["grade"], score=cls["score"], name="fail_op")
    merge = noop(x=cls["score"], name="out")

    START >> cls >> router
    router >> [pass_op, fail_op]
    [pass_op, fail_op] >> ~merge
    merge >> END


def build_production(n: int):
    """n parallel verify_case subgraphs -> combine_all -> noop post-process.
    Each subgraph has ~6 internal ops. Total ~ n*6 + 2 ops.
    """
    with GraphOp(name=f"production_{n}") as g:
        cases = []
        for i in range(n):
            case = verify_case(x=PARENT["score"], threshold=50 + i, name=f"case{i}")
            START >> case
            cases.append(case)

        agg = combine_all(
            r1=cases[0]["x"] if len(cases) > 0 else None,
            r2=cases[1]["x"] if len(cases) > 1 else None,
            r3=cases[2]["x"] if len(cases) > 2 else None,
            r4=cases[3]["x"] if len(cases) > 3 else None,
            r5=cases[-1]["x"] if len(cases) > 4 else None,
            name="agg",
        )
        for case in cases:
            case >> agg

        post = noop(x=agg["count"], name="post")
        agg >> post >> END
    return g


# =============================================================================
# Pattern 7: CPU-bound parallel contention
#   n parallel CPU-heavy ops (hash chains) running alongside lightweight ops
#   Tests scheduler throughput under real CPU pressure
# =============================================================================


@graph
def cpu_heavy_branch(x, iterations):
    """CPU-heavy subgraph: hash chain -> fibonacci -> merge."""
    h = cpu_hash_chain(x=x, iterations=iterations, name="hash")
    f = cpu_fibonacci(n=x, name="fib")
    m = merge_two(a=h["x"], b=f["fib"], name="merge")
    START >> [h, f] >> m >> END


def build_cpu_contention(n_heavy: int, n_light: int, hash_iters: int):
    """n_heavy CPU-bound branches + n_light lightweight branches, all parallel -> aggregate.
    Stresses scheduler with mixed CPU contention.
    """
    with GraphOp(name=f"cpu_contention_{n_heavy}h_{n_light}l") as g:
        branches = []

        # Heavy CPU branches (thread executor, blocks CPU)
        for i in range(n_heavy):
            branch = cpu_heavy_branch(
                x=PARENT["x"], iterations=hash_iters, name=f"heavy{i}"
            )
            START >> branch
            branches.append(branch)

        # Light branches (no executor, fast)
        for i in range(n_light):
            branch = inner_pipeline(x=PARENT["x"], name=f"light{i}")
            START >> branch
            branches.append(branch)

        agg = combine_all(
            r1=branches[0]["x"] if len(branches) > 0 else None,
            r2=branches[1]["x"] if len(branches) > 1 else None,
            r3=branches[2]["x"] if len(branches) > 2 else None,
            r4=branches[3]["x"] if len(branches) > 3 else None,
            r5=branches[-1]["x"] if len(branches) > 4 else None,
            name="agg",
        )
        for b in branches:
            b >> agg
        agg >> END
    return g


# =============================================================================
# Pattern 8: Production-like with CPU-bound stages
#   init -> [parallel verify + CPU ops] -> aggregate -> CPU post-process
#   Simulates real workload: verification logic + heavy computation
# =============================================================================


@graph
def cpu_verify_and_process(score, threshold, hash_iters):
    """Verify case + CPU hash chain in parallel, then merge."""
    v = verify_case(x=score, threshold=threshold, name="verify")
    h = cpu_hash_chain(x=score, iterations=hash_iters, name="cpu")
    m = merge_two(a=v["x"], b=h["x"], name="merge")
    START >> [v, h] >> m >> END


def build_production_cpu(n: int, hash_iters: int):
    """n parallel cpu_verify_and_process subgraphs -> combine -> matrix post-process.
    Each subgraph: verify (6 ops) + hash chain (1 CPU op) + merge = 8+ ops.
    """
    with GraphOp(name=f"production_cpu_{n}") as g:
        cases = []
        for i in range(n):
            case = cpu_verify_and_process(
                score=PARENT["score"],
                threshold=50 + i,
                hash_iters=hash_iters,
                name=f"case{i}",
            )
            START >> case
            cases.append(case)

        agg = combine_all(
            r1=cases[0]["x"] if len(cases) > 0 else None,
            r2=cases[1]["x"] if len(cases) > 1 else None,
            r3=cases[2]["x"] if len(cases) > 2 else None,
            r4=cases[3]["x"] if len(cases) > 3 else None,
            r5=cases[-1]["x"] if len(cases) > 4 else None,
            name="agg",
        )
        for case in cases:
            case >> agg

        # CPU-heavy post-processing
        post = cpu_matrix_mult(size=PARENT["matrix_size"], name="matrix_post")
        agg >> post >> END
    return g


# =============================================================================
# Pattern 9: Pure CPU stress — linear chain of heavy ops
#   Tests scheduler overhead when every op is CPU-bound
# =============================================================================


def build_cpu_chain(n: int, hash_iters: int):
    """Linear chain of n CPU hash ops — worst case for scheduler under CPU load."""
    with GraphOp(name=f"cpu_chain_{n}") as g:
        prev = cpu_hash_chain(x=PARENT["x"], iterations=hash_iters, name="op0")
        START >> prev
        for i in range(1, n):
            cur = cpu_hash_chain(x=prev["x"], iterations=hash_iters, name=f"op{i}")
            prev >> cur
            prev = cur
        prev >> END
    return g


# =============================================================================
# Benchmark runner
# =============================================================================


async def bench_mode(graph, mode: str, inputs: dict, runs: int = 200):
    engine = Hush(graph, mode=mode)

    # Warmup
    for _ in range(5):
        await engine.run(inputs=inputs)

    # Measure time
    times = []
    for _ in range(runs):
        start = time.perf_counter_ns()
        await engine.run(inputs=inputs)
        elapsed = time.perf_counter_ns() - start
        times.append(elapsed / 1_000_000)

    # Measure memory
    tracemalloc.start()
    for _ in range(10):
        await engine.run(inputs=inputs)
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    return {
        "mean_ms": statistics.mean(times),
        "p50_ms": statistics.median(times),
        "p99_ms": sorted(times)[int(len(times) * 0.99)],
        "min_ms": min(times),
        "max_ms": max(times),
        "peak_mem_kb": peak / 1024,
    }


def print_header(name: str):
    print(f"\n  {name}:")
    print(
        f"  {'Label':>30s} | {'Ops':>5s} | {'Py mean':>10s} | {'Rs mean':>10s} | "
        f"{'Py p99':>10s} | {'Rs p99':>10s} | {'Speedup':>8s} | {'Py mem':>10s} | {'Rs mem':>10s}"
    )
    print(
        f"  {'-' * 30}-+-{'-' * 5}-+-{'-' * 10}-+-{'-' * 10}-+-{'-' * 10}-+-{'-' * 10}-+-{'-' * 8}-+-{'-' * 10}-+-{'-' * 10}"
    )


async def bench_one(label: str, graph, inputs: dict, runs: int = 200):
    # Count total ops (rough)
    def count_ops(g):
        n = len(getattr(g, "_ops", {}))
        for child in getattr(g, "_ops", {}).values():
            if hasattr(child, "_ops"):
                n += count_ops(child)
        return n

    num_ops = count_ops(graph)
    py = await bench_mode(graph, "python", inputs, runs)
    rs = await bench_mode(graph, "rust", inputs, runs)
    speedup = py["mean_ms"] / rs["mean_ms"] if rs["mean_ms"] > 0 else float("inf")

    print(
        f"  {label:>30s} | {num_ops:5d} | {py['mean_ms']:8.3f}ms | {rs['mean_ms']:8.3f}ms | "
        f"{py['p99_ms']:8.3f}ms | {rs['p99_ms']:8.3f}ms | "
        f"{speedup:6.2f}x | {py['peak_mem_kb']:8.1f}KB | {rs['peak_mem_kb']:8.1f}KB"
    )


async def main():
    check_deps()

    print("=" * 130)
    print("  End-to-End Stress Benchmark: Python mode vs Rust mode")
    print(
        "  Patterns: linear, nested, parallel, branching, ForOp, production, CPU-contention, CPU-production, CPU-chain"
    )
    print("=" * 130)

    # --- Linear chains ---
    print_header("Linear chain (baseline)")
    for n in [50, 100, 200, 500]:
        await bench_one(f"linear({n})", build_linear(n), {"x": 42})

    # --- Nested @graph (3-level deep) ---
    print_header("Nested @graph (inner=3ops, mid=2*inner+merge, top=chain of mid)")
    for n in [2, 5, 10, 20]:
        await bench_one(f"nested(stages={n})", build_nested(n), {"x": 42})

    # --- Parallel + nested ---
    print_header("Parallel fan-out with nested @graph bodies")
    for n in [5, 10, 20, 50]:
        await bench_one(f"parallel_nested({n})", build_parallel_nested(n), {"x": 42})

    # --- Branching ---
    print_header("if_() branching (4 paths per stage)")
    for n in [5, 10, 20]:
        await bench_one(f"branching(stages={n})", build_branching(n), {"score": 75})

    # --- ForOp loop ---
    print_header("ForOp sequential loop")
    for n in [10, 50, 100]:
        items = [f"item{i}" for i in range(n)]
        await bench_one(
            f"for_loop({n} items)",
            build_for_loop(n),
            {"items": items, "prefix": "test"},
        )

    # --- Production-like ---
    print_header("Production-like (n parallel verify subgraphs -> aggregate -> post)")
    for n in [3, 5, 7, 10]:
        await bench_one(f"production({n} cases)", build_production(n), {"score": 75})

    # --- CPU contention: heavy + light parallel ---
    print_header("CPU contention (heavy hash chains + light ops in parallel)")
    for n_heavy, n_light, iters in [
        (3, 10, 5000),
        (5, 10, 5000),
        (5, 20, 10000),
        (10, 20, 10000),
    ]:
        await bench_one(
            f"cpu({n_heavy}h+{n_light}l,{iters}i)",
            build_cpu_contention(n_heavy, n_light, iters),
            {"x": 42},
            runs=50,
        )

    # --- Production + CPU ---
    print_header("Production-like + CPU (verify + hash + matrix post-process)")
    for n, iters, msize in [
        (3, 5000, 30),
        (5, 5000, 30),
        (5, 10000, 50),
        (7, 10000, 50),
    ]:
        await bench_one(
            f"prod_cpu({n}c,{iters}i,{msize}m)",
            build_production_cpu(n, iters),
            {"score": 75, "matrix_size": msize},
            runs=50,
        )

    # --- Pure CPU chain ---
    print_header("Pure CPU chain (linear hash ops — scheduler under CPU load)")
    for n, iters in [(10, 5000), (20, 5000), (10, 20000), (20, 20000)]:
        await bench_one(
            f"cpu_chain({n},{iters}i)",
            build_cpu_chain(n, iters),
            {"x": 42},
            runs=50,
        )

    print("\n" + "=" * 130)
    print("  Done.")
    print("=" * 130)


if __name__ == "__main__":
    asyncio.run(main())
