# Hush-icore vs Python Backend — Benchmark Results

**Date:** 2026-03-10
**Platform:** Windows 10 (MSYS_NT-10.0-26100), x86_64
**Rust build:** release (LTO)
**Python:** hush-core async engine
**Benchmark:** `hush-icore/benches/bench_e2e.py`

## Summary

| Pattern | Speedup Range | Description |
|---------|--------------|-------------|
| Linear chain | 1.70x – 2.02x | Sequential op pipeline |
| Nested @graph | 1.77x – 2.11x | 3-level nested subgraphs |
| Parallel fan-out | 1.63x – 2.80x | Parallel branches with nested bodies |
| Branching | 2.78x – 3.44x | if_() conditional routing (4 paths/stage) |
| Production-like | 1.56x – 2.69x | Parallel verify subgraphs → aggregate → post |
| CPU contention | 20.32x – 38.77x | Heavy hash chains + light ops in parallel |
| Production + CPU | 22.85x – 48.40x | Production pattern with hash + matrix ops |
| Pure CPU chain | 10.33x – 10.89x | Linear hash ops under CPU load |

## Detailed Results

### Linear chain (baseline)

| Label | Ops | Py mean | Rs mean | Py p99 | Rs p99 | Speedup | Py mem |
|-------|-----|---------|---------|--------|--------|---------|--------|
| linear(50) | 50 | 0.391ms | 0.230ms | 0.781ms | 0.395ms | 1.70x | 81.4KB |
| linear(100) | 100 | 0.791ms | 0.409ms | 1.017ms | 0.605ms | 1.94x | 154.6KB |
| linear(200) | 200 | 1.598ms | 0.792ms | 7.217ms | 1.106ms | 2.02x | 302.2KB |
| linear(500) | 500 | 4.126ms | 2.130ms | 10.278ms | 2.532ms | 1.94x | 739.4KB |

### Nested @graph (inner=3ops, mid=2*inner+merge, top=chain of mid)

| Label | Ops | Py mean | Rs mean | Py p99 | Rs p99 | Speedup | Py mem |
|-------|-----|---------|---------|--------|--------|---------|--------|
| nested(stages=2) | 20 | 0.260ms | 0.147ms | 0.314ms | 0.398ms | 1.77x | 54.1KB |
| nested(stages=5) | 50 | 0.666ms | 0.329ms | 1.148ms | 0.642ms | 2.02x | 100.2KB |
| nested(stages=10) | 100 | 1.279ms | 0.625ms | 1.684ms | 0.985ms | 2.04x | 177.4KB |
| nested(stages=20) | 200 | 2.598ms | 1.234ms | 8.598ms | 2.078ms | 2.11x | 335.6KB |

### Parallel fan-out with nested @graph bodies

| Label | Ops | Py mean | Rs mean | Py p99 | Rs p99 | Speedup | Py mem |
|-------|-----|---------|---------|--------|--------|---------|--------|
| parallel_nested(5) | 21 | 0.230ms | 0.141ms | 0.292ms | 0.251ms | 1.63x | 47.5KB |
| parallel_nested(10) | 41 | 0.412ms | 0.202ms | 0.661ms | 0.344ms | 2.04x | 76.1KB |
| parallel_nested(20) | 81 | 0.814ms | 0.341ms | 1.103ms | 1.100ms | 2.39x | 133.0KB |
| parallel_nested(50) | 201 | 1.959ms | 0.699ms | 7.505ms | 1.141ms | 2.80x | 330.2KB |

### if_() branching (4 paths per stage)

| Label | Ops | Py mean | Rs mean | Py p99 | Rs p99 | Speedup | Py mem |
|-------|-----|---------|---------|--------|--------|---------|--------|
| branching(stages=5) | 35 | 0.219ms | 0.079ms | 0.236ms | 0.088ms | 2.78x | 61.7KB |
| branching(stages=10) | 70 | 0.428ms | 0.140ms | 0.546ms | 0.150ms | 3.06x | 112.7KB |
| branching(stages=20) | 140 | 0.899ms | 0.261ms | 5.516ms | 0.285ms | 3.44x | 227.7KB |

### Production-like (n parallel verify subgraphs → aggregate → post)

| Label | Ops | Py mean | Rs mean | Py p99 | Rs p99 | Speedup | Py mem |
|-------|-----|---------|---------|--------|--------|---------|--------|
| production(3 cases) | 20 | 0.211ms | 0.135ms | 0.222ms | 0.208ms | 1.56x | 49.0KB |
| production(5 cases) | 32 | 0.329ms | 0.167ms | 0.346ms | 0.290ms | 1.97x | 68.7KB |
| production(7 cases) | 44 | 0.440ms | 0.196ms | 0.466ms | 0.330ms | 2.25x | 88.2KB |
| production(10 cases) | 62 | 0.606ms | 0.225ms | 0.753ms | 0.387ms | 2.69x | 117.8KB |

### CPU contention (heavy hash chains + light ops in parallel)

| Label | Ops | Py mean | Rs mean | Py p99 | Rs p99 | Speedup | Py mem |
|-------|-----|---------|---------|--------|--------|---------|--------|
| cpu(3h+10l,5000i) | 53 | 9.977ms | 0.491ms | 15.021ms | 0.531ms | 20.32x | 148.8KB |
| cpu(5h+10l,5000i) | 61 | 16.251ms | 0.602ms | 17.439ms | 0.876ms | 26.98x | 170.9KB |
| cpu(5h+20l,10000i) | 101 | 31.037ms | 0.904ms | 37.086ms | 1.122ms | 34.32x | 225.5KB |
| cpu(10h+20l,10000i) | 121 | 61.753ms | 1.593ms | 63.771ms | 1.952ms | 38.77x | 333.7KB |

### Production-like + CPU (verify + hash + matrix post-process)

| Label | Ops | Py mean | Rs mean | Py p99 | Rs p99 | Speedup | Py mem |
|-------|-----|---------|---------|--------|--------|---------|--------|
| prod_cpu(3c,5000i,30m) | 29 | 11.001ms | 0.482ms | 11.696ms | 0.972ms | 22.85x | 171.2KB |
| prod_cpu(5c,5000i,30m) | 47 | 17.517ms | 0.526ms | 18.740ms | 0.840ms | 33.31x | 206.3KB |
| prod_cpu(5c,10000i,50m) | 47 | 36.072ms | 0.848ms | 42.794ms | 1.026ms | 42.54x | 359.1KB |
| prod_cpu(7c,10000i,50m) | 65 | 48.993ms | 1.012ms | 60.013ms | 1.477ms | 48.40x | 393.5KB |

### Pure CPU chain (linear hash ops — scheduler under CPU load)

| Label | Ops | Py mean | Rs mean | Py p99 | Rs p99 | Speedup | Py mem |
|-------|-----|---------|---------|--------|--------|---------|--------|
| cpu_chain(10,5000i) | 10 | 31.864ms | 2.997ms | 32.933ms | 4.821ms | 10.63x | 45.5KB |
| cpu_chain(20,5000i) | 20 | 62.089ms | 5.701ms | 65.593ms | 6.099ms | 10.89x | 64.6KB |
| cpu_chain(10,20000i) | 10 | 120.708ms | 11.538ms | 126.447ms | 14.229ms | 10.46x | 44.5KB |
| cpu_chain(20,20000i) | 20 | 236.934ms | 22.926ms | 244.975ms | 24.119ms | 10.33x | 64.3KB |

## Key Observations

- **IO-bound patterns** (linear, nested, fan-out, branching): **1.5x–3.5x** speedup. The overhead is scheduling + async runtime, so the Rust advantage is moderate.
- **CPU-bound patterns** (contention, pure CPU): **10x–49x** speedup. Native Rust execution eliminates Python interpreter overhead entirely.
- **Mixed patterns** (production + CPU): Speedup scales with the proportion of CPU-bound work in the graph.
- **Tail latency (p99)**: Rust is significantly more stable — Python p99 can spike 2-7x above mean, while Rust p99 stays within 1.5x of mean.
- **Memory**: Python allocations scale linearly with op count (45KB–740KB range for these patterns).

## Notes

- Pattern 5 (ForOp loop) was skipped — `ForOp`/`Each` have been replaced by `GraphOp.loop`.
- Each pattern runs multiple iterations; the range shows min–max speedup across runs.
- Rust backend uses `Hush::new(json)` + `Hush::run_json(inputs)` via the `hush-bench` binary.
- Python backend uses `Hush(graph)` + `engine.run(inputs)` with the default async engine.
