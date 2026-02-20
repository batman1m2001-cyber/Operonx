"""Microbenchmark: CompiledGraph vs Python dict-based scheduling.

Measures pure scheduling overhead — no op execution, just
ready-count bookkeeping and successor activation.

Usage:
    cd hush-runtime
    uv run python benches/bench_scheduler.py
"""

import statistics
import time

from hush_runtime import CompiledGraph


def build_linear_chain(n: int) -> dict:
    """A0 -> A1 -> ... -> A(n-1)"""
    names = [f"op{i}" for i in range(n)]
    adj = {}
    rc = {}
    for i, name in enumerate(names):
        adj[name] = [(names[i + 1], False)] if i < n - 1 else []
        rc[name] = 0 if i == 0 else 1
    return {
        "adjacency": adj,
        "ready_count": rc,
        "entries": [names[0]],
        "can_inline": {name: True for name in names},
        "is_branch": {},
    }


def build_wide_fanout(n: int) -> dict:
    """A -> B0..B(n-1) -> C"""
    adj = {"A": [(f"B{i}", False) for i in range(n)]}
    rc = {"A": 0, "C": n}
    ci = {"A": True, "C": True}
    for i in range(n):
        name = f"B{i}"
        adj[name] = [("C", False)]
        rc[name] = 1
        ci[name] = True
    return {
        "adjacency": adj,
        "ready_count": rc,
        "entries": ["A"],
        "can_inline": ci,
        "is_branch": {},
    }


def build_diamond(depth: int) -> dict:
    """Create a diamond-shaped graph with given depth.

    Layer 0: A (entry)
    Layer 1: B0, B1 (A -> B0, B1)
    Layer 2: C0 (B0, B1 -> C0)
    ...and so on, alternating fork and join.
    """
    adj = {}
    rc = {}
    ci = {}
    current_layer = ["A"]
    adj["A"] = []
    rc["A"] = 0
    ci["A"] = True

    for d in range(depth):
        next_layer = []
        for node in current_layer:
            left = f"L{d}_{node}"
            right = f"R{d}_{node}"
            adj[node] = [(left, False), (right, False)]
            adj[left] = []
            adj[right] = []
            rc[left] = 1
            rc[right] = 1
            ci[left] = True
            ci[right] = True

            # Join node
            join = f"J{d}_{node}"
            adj[left] = [(join, False)]
            adj[right] = [(join, False)]
            adj[join] = []
            rc[join] = 2
            ci[join] = True
            next_layer.append(join)

        current_layer = next_layer

    return {
        "adjacency": adj,
        "ready_count": rc,
        "entries": ["A"],
        "can_inline": ci,
        "is_branch": {},
    }


def python_scheduler_simulate(data: dict, activation_sequence: list) -> None:
    """Simulate Python dict-based scheduling for fair comparison."""
    ready_count = dict(data["ready_count"])
    soft_satisfied = set()
    adjacency = data["adjacency"]

    for op_name in activation_sequence:
        for target, is_soft in adjacency.get(op_name, []):
            if is_soft:
                if target in soft_satisfied:
                    continue
                soft_satisfied.add(target)
            ready_count[target] -= 1


def rust_scheduler_simulate(sched: CompiledGraph, activation_sequence: list) -> None:
    """Simulate Rust scheduling."""
    state = sched.new_run_state()
    for op_name in activation_sequence:
        sched.activate_successors(op_name, state, None)


def bench_one(name: str, n: int, data: dict, runs: int = 1000):
    """Benchmark a single graph pattern."""
    # Build activation sequence (topological order based on ready_count)
    activation_order = sorted(data["ready_count"].keys(), key=lambda k: data["ready_count"][k])

    sched = CompiledGraph.from_dict(data)

    # Warmup
    for _ in range(10):
        python_scheduler_simulate(data, activation_order)
        rust_scheduler_simulate(sched, activation_order)

    # Benchmark Python
    py_times = []
    for _ in range(runs):
        start = time.perf_counter_ns()
        python_scheduler_simulate(data, activation_order)
        py_times.append(time.perf_counter_ns() - start)

    # Benchmark Rust
    rs_times = []
    for _ in range(runs):
        start = time.perf_counter_ns()
        rust_scheduler_simulate(sched, activation_order)
        rs_times.append(time.perf_counter_ns() - start)

    py_mean = statistics.mean(py_times) / 1000  # ns -> us
    rs_mean = statistics.mean(rs_times) / 1000
    py_p50 = statistics.median(py_times) / 1000
    rs_p50 = statistics.median(rs_times) / 1000
    py_p99 = sorted(py_times)[int(len(py_times) * 0.99)] / 1000
    rs_p99 = sorted(rs_times)[int(len(rs_times) * 0.99)] / 1000
    speedup = py_mean / rs_mean if rs_mean > 0 else float("inf")

    print(f"  {name:20s} | n={n:5d} | Py={py_mean:8.1f}us | Rs={rs_mean:8.1f}us | "
          f"p50: {py_p50:.1f}/{rs_p50:.1f} | p99: {py_p99:.1f}/{rs_p99:.1f} | "
          f"speedup={speedup:.1f}x")


def main():
    print("=" * 100)
    print("  Scheduler Microbenchmark: Python dict vs CompiledGraph")
    print("  (pure scheduling overhead, no op execution)")
    print("=" * 100)
    print()

    print("Linear chains:")
    for n in [10, 50, 100, 500, 1000]:
        bench_one("linear", n, build_linear_chain(n))

    print()
    print("Wide fan-out (A -> N ops -> C):")
    for n in [10, 50, 100, 500]:
        bench_one("fanout", n, build_wide_fanout(n))

    print()
    print("Diamond graphs:")
    for depth in [2, 4, 6, 8]:
        data = build_diamond(depth)
        num_ops = len(data["ready_count"])
        bench_one("diamond", num_ops, data)

    print()
    print("Done.")


if __name__ == "__main__":
    main()
