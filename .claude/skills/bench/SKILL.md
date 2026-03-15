---
name: bench
description: Run benchmark comparing Python and Rust backends for a specific example
---

# /bench — Benchmark Python vs Rust Backend

Run an example's `bench.py` to compare Python (FastAPI) and Rust (Axum) backends.

## Usage

```
/bench 01                    # Run bench for example 01
/bench 02 --total 1000 --ccu 50   # Custom params
```

## Steps

### 1. Parse arguments

- First arg: example number (e.g., `01`, `02`, `05`)
- `--total N`: Total requests (default: 100)
- `--ccu N`: Concurrent users (default: 20)

### 2. Ensure Rust backend is built

```bash
# Build hush-serve binary
cd rust && cargo build --release -p hush-serve

# Build rust_ops plugin (if examples use @op(rust=...))
cd examples/rust_ops && cargo build --release
```

### 3. Run the benchmark

```bash
cd examples && uv run python {NN}_{name}/bench.py
```

If custom `--total` or `--ccu` is requested, temporarily edit the `TOTAL` and `CCU` constants in `bench.py`, run, then revert.

### 4. Report results

Show the output with key metrics: avg, median, p99 latency for both backends.

## Example bench.py structure

All bench.py files follow the same pattern:
- `PORT_PY = 9001`, `PORT_RS = 9002` — separate ports
- `TOTAL = 100`, `CCU = 20` — configurable
- `ENDPOINTS = [("/path", {payload}, "label")]` — per-example
- Spawns `serve_python.py` and `serve_rust.py` as subprocesses
- Warmup (5 requests), then benchmark with aiohttp semaphore
- Reports avg/median/p99 in ms

## Available examples with bench.py

```
01_hello_world/bench.py
02_data_pipeline/bench.py
03_llm_chat/bench.py
04_llm_advanced/bench.py
05_loops_and_branches/bench.py
06_tracing/{local,langfuse,otel}/bench.py
07_embeddings_and_rag/bench.py
08_error_handling/bench.py
09_agent_workflow/bench.py
10_multi_model/bench.py
11_parallel_advanced/bench.py
12_rag_advanced/bench.py
```
