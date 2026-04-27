---
name: bench
description: Run benchmark comparing Python and Rust backends for a specific example
---

# /bench — Benchmark Python vs Rust Backend

Run an example's `bench.py` to compare Python (FastAPI / `operonx[serve]`) and
Rust (Axum / `operonx-serve` binary) backends.

## Usage

```
/bench 01                    # Run bench for example ex01
/bench 02 --total 1000 --ccu 50   # Custom params
```

## Steps

### 1. Parse arguments

- First arg: example number (e.g., `01`, `02`, `05`)
- `--total N`: Total requests (default: 100)
- `--ccu N`: Concurrent users (default: 20)

### 2. Ensure the Rust backend is built

```bash
# Single Rust workspace — builds everything including operonx-serve
cd rust && cargo build --release --workspace
```

If the example registers custom Rust ops, make sure the consuming binary
links against the crate that defines them. Cdylib runtime loading is not
implemented.

### 3. Run the benchmark

```bash
cd examples/python && uv run python ex{NN}_{name}/bench.py
```

If custom `--total` or `--ccu` is requested, temporarily edit the `TOTAL` and
`CCU` constants in `bench.py`, run, then revert.

### 4. Report results

Show the output with key metrics: avg, median, p99 latency for both backends.

## bench.py structure

All `bench.py` files follow the same pattern:
- `PORT_PY = 9001`, `PORT_RS = 9002` — separate ports
- `TOTAL = 100`, `CCU = 20` — configurable
- `ENDPOINTS = [("/path", {payload}, "label")]` — per-example
- Spawns `serve_python.py` and the Rust serve binary as subprocesses
- Warmup (5 requests), then benchmark with aiohttp semaphore
- Reports avg/median/p99 in ms

## Available examples

Check `examples/python/` for the current list — each example with a
`bench.py` is bench-able. Pure-compute examples (ex01, ex02) can run without
API keys; LLM examples (ex03+) need credentials configured via
`operonx.bootstrap()`.
