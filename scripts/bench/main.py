"""Python e2e bench — runs each pattern through the Python ``Operon`` engine.

Imports the same ``@graph`` factories that ``generate.py`` uses to dump
``graph.json``, so the Python and Rust benches exercise the same logical
topology. Reports mean / p50 / p99 / min / max / peak-mem per pattern.

Run from the repo root:

    uv run python scripts/bench/main.py
    uv run python scripts/bench/main.py --runs 50      # default 200
    uv run python scripts/bench/main.py --warmup 10    # default 5
"""

from __future__ import annotations

import argparse
import asyncio
import statistics
import sys
import time
import tracemalloc
from pathlib import Path

# Make `from generate import ...` work regardless of where this script is
# invoked from (CWD agnostic).
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from generate import PATTERNS  # noqa: E402

from operonx.core import Operon  # noqa: E402


async def bench_one(name: str, factory, inputs: dict, runs: int, warmup: int) -> dict:
    graph = factory()
    engine = Operon(graph)

    for _ in range(warmup):
        await engine.run(inputs=inputs)

    times_ms: list[float] = []
    last_output = None
    for _ in range(runs):
        start = time.perf_counter_ns()
        result = await engine.run(inputs=inputs)
        elapsed = time.perf_counter_ns() - start
        times_ms.append(elapsed / 1_000_000)
        last_output = {k: v for k, v in result.items() if not k.startswith("$")}

    tracemalloc.start()
    for _ in range(10):
        await engine.run(inputs=inputs)
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    sorted_ms = sorted(times_ms)
    p99_idx = min(len(sorted_ms) - 1, int(len(sorted_ms) * 0.99))
    return {
        "name": name,
        "mean_ms": statistics.mean(times_ms),
        "p50_ms": statistics.median(times_ms),
        "p99_ms": sorted_ms[p99_idx],
        "min_ms": min(times_ms),
        "max_ms": max(times_ms),
        "peak_mem_kb": peak / 1024,
        "output": last_output,
    }


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Operonx Python e2e bench")
    p.add_argument("--runs", type=int, default=200, help="timed iterations per pattern")
    p.add_argument("--warmup", type=int, default=5, help="untimed warmup iterations")
    return p.parse_args()


async def main() -> None:
    args = parse_args()
    print(f"  runs={args.runs}, warmup={args.warmup}, patterns={len(PATTERNS)}\n")
    print(
        f"  {'pattern':>22s} | {'mean':>9s} | {'p50':>9s} | {'p99':>9s} |"
        f" {'min':>9s} | {'max':>9s} | {'peak mem':>10s}"
    )
    print(f"  {'-' * 22}-+-{'-' * 9}-+-{'-' * 9}-+-{'-' * 9}-+-"
          f"{'-' * 9}-+-{'-' * 9}-+-{'-' * 10}")
    for name, factory, inputs in PATTERNS:
        r = await bench_one(name, factory, inputs, args.runs, args.warmup)
        print(
            f"  {r['name']:>22s} | {r['mean_ms']:7.3f}ms | {r['p50_ms']:7.3f}ms |"
            f" {r['p99_ms']:7.3f}ms | {r['min_ms']:7.3f}ms | {r['max_ms']:7.3f}ms |"
            f" {r['peak_mem_kb']:8.1f}KB"
        )


if __name__ == "__main__":
    asyncio.run(main())
