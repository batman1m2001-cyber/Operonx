"""Generate graph.json + inputs.json for every bench pattern.

Each pattern is a `@graph` factory that builds a stress topology. We
serialise the result to ``scripts/bench/data/<name>.graph.json`` and
the matching inputs to ``<name>.inputs.json``. Both bench drivers
(Python and Rust) consume the same files so the comparison is
apples-to-apples.

Usage::

    uv run python scripts/bench/generate.py

Patterns intentionally avoid nested ``@graph``, ``if_()`` branching,
generators, and loops — all of those have Rust-runtime gaps today and
would diverge from Python output.
"""

from __future__ import annotations

import json
from pathlib import Path

from operonx.core import END, PARENT, START, GraphOp, graph, op
from operonx.core.ops.flow.branch_op import Branch, if_

DATA_DIR = Path(__file__).resolve().parent / "data"


# ── Bench ops ────────────────────────────────────────────────────────────


@op
def bench_noop(x: int):
    """Passthrough — measures pure scheduler overhead per op."""
    return {"x": x}


@op
def bench_fib(x: int, fib_n: int):
    """Iterative Fibonacci — bounded CPU work per op."""
    a, b = 0, 1
    for _ in range(fib_n):
        a, b = b, a + b
    return {"x": x, "fib": a % (10**9 + 7)}


@op
def bench_matrix(x: int, size: int):
    """Naive O(n^3) matrix multiplication — pure interpreted compute, no
    library shortcut. Python loops in interpreter; Rust loops at native
    speed. Expect a large Rust win on bigger sizes."""
    a = [[float(i + j) for j in range(size)] for i in range(size)]
    b = [[float(i * j + 1) for j in range(size)] for i in range(size)]
    c = [[0.0] * size for _ in range(size)]
    for i in range(size):
        for j in range(size):
            s = 0.0
            for k in range(size):
                s += a[i][k] * b[k][j]
            c[i][j] = s
    trace = sum(c[i][i] for i in range(size))
    return {"x": x, "trace": trace}


@op
def combine_all(r1=None, r2=None, r3=None, r4=None, r5=None):
    """Aggregate up to five parallel branch outputs into a single result."""
    parts = [r for r in (r1, r2, r3, r4, r5) if r is not None]
    return {"combined": parts, "count": len(parts)}


@op
def merge_two(a, b):
    """Two-input merge — used as the join op inside `mid_pipeline`."""
    return {"merged": a, "x": a, "b": b}


@op
def classify(score: int):
    """Score → grade bucket. Used by branching + production patterns."""
    if score >= 90:
        grade = "excellent"
    elif score >= 70:
        grade = "good"
    elif score >= 50:
        grade = "average"
    else:
        grade = "fail"
    return {"grade": grade, "score": score}


@op
def process_grade(grade: str, score: int):
    """Format a classified result. Run as one of `if_()` branch leaves."""
    return {"result": f"{grade}:{score}", "x": score}


# ── Patterns ─────────────────────────────────────────────────────────────


def build_linear(n: int) -> GraphOp:
    """n-op chain of bench_noop. Stresses per-op scheduler cost."""
    with GraphOp(name=f"linear_{n}") as g:
        prev = bench_noop(x=PARENT["x"], name="op0")
        START >> prev
        for i in range(1, n):
            cur = bench_noop(x=prev["x"], name=f"op{i}")
            prev >> cur
            prev = cur
        prev >> END
    return g


def build_fib_chain(n: int, fib_n: int) -> GraphOp:
    """n-op chain where each op computes fib(fib_n). Stresses CPU+scheduler."""
    with GraphOp(name=f"fib_chain_{n}x{fib_n}") as g:
        prev = bench_fib(x=PARENT["x"], fib_n=PARENT["fib_n"], name="op0")
        START >> prev
        for i in range(1, n):
            cur = bench_fib(x=prev["x"], fib_n=PARENT["fib_n"], name=f"op{i}")
            prev >> cur
            prev = cur
        prev >> END
    return g


def build_parallel(n: int) -> GraphOp:
    """n parallel bench_noop branches → combine_all (first 5 wired as inputs).

    Stresses fan-out / fan-in: scheduler dispatches all n branches
    concurrently, then waits for them before firing the merge.
    """
    with GraphOp(name=f"parallel_{n}") as g:
        branches = []
        for i in range(n):
            branch = bench_noop(x=PARENT["x"], name=f"b{i}")
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


def build_matrix_chain(n: int, size: int) -> GraphOp:
    """Linear chain of n bench_matrix ops — pure-compute CPU showcase."""
    with GraphOp(name=f"matrix_chain_{n}x{size}") as g:
        prev = bench_matrix(x=PARENT["x"], size=PARENT["size"], name="op0")
        START >> prev
        for i in range(1, n):
            cur = bench_matrix(x=prev["x"], size=PARENT["size"], name=f"op{i}")
            prev >> cur
            prev = cur
        prev >> END
    return g


@graph
def inner_pipeline(x):
    """Inner subgraph: 3-step linear chain with light fib compute.

    Includes a small `bench_fib` so the nested-@graph dispatch overhead
    is amortized across actual CPU work. Pure-noop nesting is dominated
    by Rust's per-sub-engine setup cost (`tokio::spawn` + new mpsc
    channel + UUID gen per nested call) — adding even tiny compute lets
    the native iteration speed close the gap and pull ahead.
    """
    a = bench_fib(x=x, fib_n=2000, name="inner_a")
    b = bench_fib(x=a["x"], fib_n=2000, name="inner_b")
    c = bench_fib(x=b["x"], fib_n=2000, name="inner_c")
    START >> a >> b >> c >> END


@graph
def mid_pipeline(x):
    """Middle subgraph: 2 parallel inner_pipelines + merge. = 7 ops."""
    left = inner_pipeline(x=x, name="left")
    right = inner_pipeline(x=x, name="right")
    m = merge_two(a=left["x"], b=right["x"], name="merge")
    START >> [left, right] >> m >> END


def build_nested(n: int) -> GraphOp:
    """Top-level chain of n mid_pipeline subgraphs (~9 ops each)."""
    with GraphOp(name=f"nested_{n}") as g:
        prev = mid_pipeline(x=PARENT["x"], name="stage0")
        START >> prev
        for i in range(1, n):
            cur = mid_pipeline(x=prev["x"], name=f"stage{i}")
            prev >> cur
            prev = cur
        prev >> END
    return g


def build_branching(n: int) -> GraphOp:
    """n sequential classify → if_() → 4-path → merge stages (~7 ops each)."""
    with GraphOp(name=f"branching_{n}") as g:
        prev_out = PARENT["score"]
        last = None

        for i in range(n):
            cls = classify(score=prev_out, name=f"cls{i}")
            router = (
                Branch(name=f"router{i}")
                .if_(cls["grade"] == "excellent", f"exc{i}")
                .if_(cls["grade"] == "good", f"good{i}")
                .if_(cls["grade"] == "average", f"avg{i}")
                .else_(f"fail{i}")
            )

            exc = process_grade(grade=cls["grade"], score=cls["score"], name=f"exc{i}")
            good = process_grade(grade=cls["grade"], score=cls["score"], name=f"good{i}")
            avg = process_grade(grade=cls["grade"], score=cls["score"], name=f"avg{i}")
            fail = process_grade(grade=cls["grade"], score=cls["score"], name=f"fail{i}")

            if last is None:
                START >> cls
            else:
                last >> cls

            cls >> router
            router >> [exc, good, avg, fail]

            merge = bench_noop(x=cls["score"], name=f"merge{i}")
            [exc, good, avg, fail] >> ~merge

            prev_out = merge["x"]
            last = merge

        last >> END
    return g


@graph
def verify_case(x, threshold, size):
    """Single verification subgraph: classify → if_ → pass/fail → matrix-compute.

    Includes a real compute step (`bench_matrix`) so the bench measures
    scheduler+CPU rather than just the nested-engine dispatch overhead.
    """
    cls = classify(score=x, name="cls")
    router = if_(cls["score"] >= threshold, "pass_op").else_("fail_op")
    pass_op = process_grade(grade=cls["grade"], score=cls["score"], name="pass_op")
    fail_op = process_grade(grade=cls["grade"], score=cls["score"], name="fail_op")
    work = bench_matrix(x=cls["score"], size=size, name="work")

    START >> cls >> router
    router >> [pass_op, fail_op]
    [pass_op, fail_op] >> ~work
    work >> END


def build_production(n: int, size: int = 30) -> GraphOp:
    """n parallel verify_case subgraphs → combine_all → noop post-process."""
    with GraphOp(name=f"production_{n}") as g:
        cases = []
        for i in range(n):
            case = verify_case(
                x=PARENT["score"],
                threshold=50 + i,
                size=PARENT["size"],
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

        post = bench_noop(x=agg["count"], name="post")
        agg >> post >> END
    return g


def build_cpu_contention(n_heavy: int, n_light: int, size: int) -> GraphOp:
    """n_heavy CPU-bound + n_light no-op branches in parallel → combine.

    Mixed workload: heavy compute (naive matmul — pure interpreted in
    Python, native in Rust) and lightweight passthroughs racing through
    the same scheduler. Tests fairness + throughput under contention.

    Uses `bench_matrix` (not `bench_hash`) to avoid the hashlib bias —
    Python's `hashlib` is OpenSSL-backed C, while Rust's `sha2` is pure
    Rust, so a hash chain measures hash-library throughput rather than
    scheduler + compute. Naive matmul has no library shortcut on either
    side.
    """
    name = f"cpu_contention_{n_heavy}h_{n_light}l_{size}m"
    with GraphOp(name=name) as g:
        branches = []
        for i in range(n_heavy):
            heavy = bench_matrix(
                x=PARENT["x"],
                size=PARENT["size"],
                name=f"heavy{i}",
            )
            START >> heavy
            branches.append(heavy)
        for i in range(n_light):
            light = bench_noop(x=PARENT["x"], name=f"light{i}")
            START >> light
            branches.append(light)
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


# ── Pattern registry — driven by both main.py and the Rust bench ────────


PATTERNS: list[tuple[str, callable, dict]] = [
    # Linear chains (scheduler overhead per op)
    ("linear_50", lambda: build_linear(50), {"x": 42}),
    ("linear_100", lambda: build_linear(100), {"x": 42}),
    ("linear_200", lambda: build_linear(200), {"x": 42}),
    ("linear_500", lambda: build_linear(500), {"x": 42}),
    # CPU-bounded chains (Fibonacci — pure compute, no allocation)
    ("fib_chain_10x500", lambda: build_fib_chain(10, 500), {"x": 42, "fib_n": 500}),
    ("fib_chain_20x500", lambda: build_fib_chain(20, 500), {"x": 42, "fib_n": 500}),
    # Parallel fan-out → combine
    ("parallel_5", lambda: build_parallel(5), {"x": 42}),
    ("parallel_10", lambda: build_parallel(10), {"x": 42}),
    ("parallel_20", lambda: build_parallel(20), {"x": 42}),
    # Matrix chain — pure interpreted compute, fair Python↔Rust
    # (hash-chain patterns dropped — `hashlib` is OpenSSL C-backed and
    # `sha2` is pure Rust, so they measured library throughput, not the
    # engine. `matrix_chain_*` covers CPU-chain stress fairly.)
    ("matrix_chain_5x30", lambda: build_matrix_chain(5, 30), {"x": 42, "size": 30}),
    ("matrix_chain_5x60", lambda: build_matrix_chain(5, 60), {"x": 42, "size": 60}),
    ("matrix_chain_10x40", lambda: build_matrix_chain(10, 40), {"x": 42, "size": 40}),
    ("matrix_chain_5x100", lambda: build_matrix_chain(5, 100), {"x": 42, "size": 100}),
    # CPU contention — heavy matmul + light no-op branches racing through
    # the scheduler. Uses bench_matrix (not bench_hash) to avoid the
    # hashlib OpenSSL bias.
    (
        "cpu_contention_3h_10l_30m",
        lambda: build_cpu_contention(3, 10, 30),
        {"x": 42, "size": 30},
    ),
    (
        "cpu_contention_5h_10l_30m",
        lambda: build_cpu_contention(5, 10, 30),
        {"x": 42, "size": 30},
    ),
    # Nested @graph (3-level deep) — Rust today: stubbed (logged as backlog).
    ("nested_2", lambda: build_nested(2), {"x": 42}),
    ("nested_5", lambda: build_nested(5), {"x": 42}),
    ("nested_10", lambda: build_nested(10), {"x": 42}),
    # if_() branching (4 paths per stage) — Rust today: stubbed.
    ("branching_5", lambda: build_branching(5), {"score": 75}),
    ("branching_10", lambda: build_branching(10), {"score": 75}),
    # Production-like (parallel verify subgraphs → aggregate → post)
    # Each verify_case includes a real bench_matrix step so the scheduler
    # has actual work to amortize across the nested @graph dispatches.
    ("production_3", lambda: build_production(3, 30), {"score": 75, "size": 30}),
    ("production_5", lambda: build_production(5, 30), {"score": 75, "size": 30}),
]


def main() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    print(f"Writing to {DATA_DIR}")
    drop_keys = {"python_callable", "resource_config", "resource_configs", "fallback_configs"}

    def walk(node):
        if isinstance(node, dict):
            return {k: walk(v) for k, v in node.items() if k not in drop_keys}
        if isinstance(node, list):
            return [walk(v) for v in node]
        return node

    for name, factory, inputs in PATTERNS:
        g = factory()
        g.build()
        config = walk(g.serialize())
        config["schema_version"] = "1.0"
        graph_path = DATA_DIR / f"{name}.graph.json"
        inputs_path = DATA_DIR / f"{name}.inputs.json"
        graph_path.write_text(json.dumps(config, indent=2, ensure_ascii=False, default=str))
        inputs_path.write_text(json.dumps(inputs, indent=2))
        print(f"  {name:>20s}  {graph_path.name} ({graph_path.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main()
