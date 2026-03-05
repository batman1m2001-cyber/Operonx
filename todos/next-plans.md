# Pre-Merge Validation Plan

Before merging `feat/stream-architecture` into `main`, complete these checks.

## 1. Integration Benchmarks — No Performance Regression

Compare `main` vs `feat/stream-architecture` on identical workflows.

| Benchmark | What to measure |
|-----------|-----------------|
| Batch graph (10 ops, no streaming) | Latency, memory |
| Batch graph (50 ops, mixed sync/async) | Latency, throughput |
| ForOp equivalent (generator yielding 100 items) | Latency vs old ForOp on `main` |
| MapOp equivalent (concurrent generator, semaphore=10) | Latency vs old MapOp on `main` |
| WhileOp equivalent (GraphOp.loop, 20 iterations) | Latency vs old WhileOp on `main` |
| Nested graphs (3 levels deep) | Latency, memory |

Use `benchmarks/` directory. Run each benchmark 10x, report mean/p99.

## 2. Streaming Correctness — Edge Cases

| Test | Description |
|------|-------------|
| Nested generators | Gen inside gen — correct context nesting `("main", "s0", "s0")` |
| Generator error mid-stream | Yields 2 items, raises on 3rd — partial results handled |
| Empty generator | 0 yields — graph produces empty lists, no hang |
| Streaming inside loop | Generator op inside `GraphOp.loop()` — contexts combine correctly |
| Large fan-out | Generator yields 500 items — all downstream ops complete |
| Two generators zip | Two generators at same depth, unequal yield counts |
| Backpressure | Semaphore=2, generator yields 50 — max 2 concurrent downstream |
| Stream depth broadcast | 3 depth levels — ops read correct ancestor context |
| Generator with branch | Generator downstream of a BranchOp |
| Soft edge + streaming | Soft edges with streaming contexts — `soft_satisfied` per context |

## 3. API Compatibility — Cross-Package

| Package | Check |
|---------|-------|
| `hush-providers` | `cd hush-providers && uv run -m pytest` — imports from hush-core still work |
| `hush-serve` | `cd hush-serve && uv run -m pytest` — serve layer still works |
| `hush-telemetry` | `cd hush-telemetry && uv run -m pytest` — tracers handle tuple contexts |
| Tutorial examples | Run `tutorial/examples/01-*` through `tutorial/examples/15-*` — no import errors |

Any code importing `ForOp`, `MapOp`, `AIterOp`, `WhileOp` directly will break.
Scan all packages:

```bash
grep -r "ForOp\|MapOp\|AIterOp\|WhileOp\|BaseIterationOp\|Each\|Broadcast" \
  --include="*.py" hush-providers/ hush-serve/ hush-telemetry/ tutorial/
```

## 4. Stress Tests — Push the Scheduler

| Test | Description |
|------|-------------|
| Wide graph | 100 ops, fan-out then fan-in |
| Deep stream | Generator yields 1000 items, single downstream op |
| Deep nesting | GraphOp → GraphOp → GraphOp (5 levels) |
| Loop + stream combo | `GraphOp.loop()` with generator inside, 50 iterations × 20 yields |
| Memory check | Run wide graph 100x — no memory leak (state cleanup) |
| Concurrent graphs | 10 graphs running simultaneously via `asyncio.gather` |

## Order of Execution

1. Run cross-package compatibility check (quick, catches import breaks)
2. Run streaming edge case tests (correctness first)
3. Run stress tests (stability)
4. Run benchmarks against `main` (performance comparison)
5. If all pass → merge to `main`
