# Hush Rust Backend — Complete Refactor & Acceleration Plan

## Project Context

Hush is a Python async workflow engine. The monorepo structure:

```
Hush-ai/
├── hush-core/          # Core engine: ops, state, scheduling, tracing
├── hush-providers/     # LLM, embedding, reranking integrations
├── hush-runtime/       # Rust backend (PyO3) — Phase 0-5 complete
├── hush-tutorial/      # Docs + examples
└── hush-eyes/          # Rust trace visualization server
```

### How Hush scheduling works (Python)

`GraphOp` in `hush-core/hush/core/ops/graph/graph_op.py` is the core container. It holds child ops in a DAG and executes them with a ready-count scheduler:

```python
class GraphOp(BaseOp):
    # Build-time (set once in build()):
    self._compiled_adj = {}        # {op_name: [(successor, is_soft), ...]}
    self.ready_count = {}          # {op_name: int} — NAMING BUG: should be initial_ready_count
    self.entries = []              # ops with ready_count == 0
    self.has_soft_preds = set()    # ops with soft predecessors

    # Per-run (created fresh each execution):
    def _run_python_scheduled(self, state, context_id, parent_context):
        ready_count = self.ready_count.copy()    # per-run mutable copy
        soft_satisfied = set()                    # tracks soft-edge groups

        # Schedule loop: activate successors when ready_count hits 0
        # Inline sync leaf ops, create asyncio.Task for async/graph ops
```

`BaseIterationOp` in `hush-core/hush/core/ops/iteration/base.py` has a duplicate scheduler in `_run_graph_python()`.

### Current Rust state (Phase 0+1+2+3 complete)

```
hush-runtime/
├── Cargo.toml                    # pyo3 0.22, ahash, smallvec
├── pyproject.toml                # maturin build
├── src/
│   ├── lib.rs                    # #[pymodule] _native — CompiledGraph, RunState, RustMemoryState, RustFuncRegistry
│   ├── ops/
│   │   ├── mod.rs
│   │   ├── graph/
│   │   │   ├── mod.rs
│   │   │   └── graph_op.rs       # CompiledGraph, RunState
│   │   └── transform/
│   │       ├── mod.rs
│   │       └── func_op.rs        # RustFuncRegistry, RustOp trait, built-in ops
│   └── states/
│       ├── mod.rs
│       ├── cell.rs               # Cell — multi-context storage (AHashMap)
│       └── state.rs              # RustMemoryState — drop-in replacement for MemoryState
├── python/
│   └── hush_runtime/
│       ├── __init__.py           # Re-exports CompiledGraph, RunState, RustMemoryState, RustFuncRegistry, rust_op
│       └── _mode.py              # ExecutionMode enum, is_rust_available()
├── tests/
│   ├── test_scheduler.py         # CompiledGraph/RunState tests (26 tests)
│   ├── test_state.py             # RustMemoryState unit tests (31 tests)
│   ├── test_rust_op.py           # RustFuncRegistry + @rust_op tests (17 tests)
│   ├── test_equivalence.py       # Cross-mode correctness tests (22 tests)
│   ├── test_mode.py              # Mode/availability tests (5 tests)
│   ├── test_builtin_ops.py       # Built-in Rust ops tests (30 tests)
│   ├── test_branch_resolution.py # Branch resolution tests (9 tests)
│   └── test_inline_execution.py  # Phase 5 inline execution tests (20 tests)
└── benches/
    ├── bench_e2e.py              # 9 patterns: linear, nested, parallel, branch, MapOp, CPU
    ├── bench_scheduler.py        # Scheduler microbenchmark
    └── bench_state.py            # State access microbenchmark
```

**Current Rust structs**:
- `CompiledGraph` — compiled graph topology, `activate_successors()`, `get_entries()`
- `RunState` — per-run mutable scheduling state
- `RustMemoryState` — Rust-backed state with `Vec<Cell>`, O(1) index access, pull/push ref resolution
- `RustFuncRegistry` — global registry of Rust-native ops with GIL release

**Integration**: `Hush(graph, mode="rust")` in `engine.py`:
- Calls `graph._build_compiled_graph_rs()` for Rust scheduling
- Creates `RustMemoryState.from_schema()` for Rust-backed state
- Falls back to Python if hush-runtime not installed

### Problems to fix

1. **Naming**: `RustScheduler`, `SchedulerState` don't map to any Python class
2. **File structure**: `scheduler/mod.rs` doesn't mirror `ops/graph/graph_op.py`
3. **`ready_count` collision**: build-time template and per-run copy share same name on GraphOp
4. **Hybrid design**: `_run_python_scheduled()` + `_run_rust_scheduled()` coexist in graph_op.py — should converge to single `run()` in Rust over phases
5. **No Rust loggings/utils**: missing infrastructure for later phases

---

## Naming Convention

Python has ONE class `GraphOp` with attributes + local variables. Rust needs separate `#[pyclass]` structs. We name them after what they hold:

| Rust struct | Python origin | Lives in |
|---|---|---|
| `CompiledGraph` | `GraphOp._compiled_adj` + `initial_ready_count` + `entries` + op metadata | `src/ops/graph/graph_op.rs` |
| `RunState` | Local vars `ready_count`, `soft_satisfied` in `_run_python_scheduled()` | `src/ops/graph/graph_op.rs` |

Python attribute renamed: `_rust_scheduler` → `_compiled_graph_rs` (rs = Rust version).

---

## Phase 1: Refactor — Rename + Restructure

**Goal**: Clean up naming and file structure. No new functionality. Must produce identical benchmark results.

### 1.1 Changes

**Python renames in `hush-core/hush/core/ops/graph/graph_op.py`**:
- `self.ready_count` → `self.initial_ready_count` (build-time template in `build()` and `__slots__`)
- `self._rust_scheduler` → `self._compiled_graph_rs`
- `_build_rust_scheduler()` → `_build_compiled_graph_rs()`
- `from hush_runtime import RustScheduler` → `from hush_runtime import CompiledGraph`
- Update `_run_python_scheduled()`: `ready_count = self.initial_ready_count.copy()`
- Update `_run_rust_scheduled()`: `sched = self._compiled_graph_rs`

**Python renames in `hush-core/hush/core/ops/iteration/base.py`**:
- `self.ready_count.copy()` → `self.initial_ready_count.copy()` in `_run_graph_python()`
- `self._rust_scheduler` → `self._compiled_graph_rs` in `_run_graph()` and `_run_graph_rust()`

**Python renames in `hush-core/hush/core/engine.py`**:
- `graph._build_rust_scheduler()` → `graph._build_compiled_graph_rs()`
- `graph._rust_scheduler` → `graph._compiled_graph_rs`

**Rust restructure**:
- Delete `src/scheduler/mod.rs` and `src/scheduler/types.rs`
- Create `src/ops/mod.rs` → `pub mod graph;`
- Create `src/ops/graph/mod.rs` → `pub mod graph_op;`
- Create `src/ops/graph/graph_op.rs` — move all code, rename `RustScheduler` → `CompiledGraph`, `SchedulerState` → `RunState`
- Update `src/lib.rs`: `mod scheduler` → `mod ops`, update class registrations

**Python wrapper renames in `hush-runtime/python/hush_runtime/__init__.py`**:
- `RustScheduler` → `CompiledGraph`, `SchedulerState` → `RunState`

### 1.2 Test Cases

**Rust unit tests** (in `src/ops/graph/graph_op.rs`, migrated from `scheduler/mod.rs`):

| Test | What it verifies | Graph shape |
|---|---|---|
| `test_linear_chain` | A→B→C activates in order | 3 ops, linear |
| `test_parallel_fork_join` | C activates only after both A and B complete | A,B→C (ready_count=2) |
| `test_soft_edge_branch` | Branch picks one path, soft edge to merge | Branch→~case_a, Branch→~case_b→merge |
| `test_soft_edge_only_counts_once` | Second soft activation is skipped | case_a→~merge, case_b→~merge |
| `test_diamond_graph` | D activates after both B and C | A→B,C→D |
| `test_new_run_state_is_independent` | Two RunStates don't interfere | A→B |

**Python integration tests** (`hush-runtime/tests/test_scheduler.py`, updated imports):

| Test | What it verifies |
|---|---|
| `test_compiled_graph_from_dict` | Build CompiledGraph from raw Python dict |
| `test_compiled_graph_from_graph` | Build from real GraphOp via `CompiledGraph.from_graph()` |
| `test_activate_successors_returns_ready_ops` | Correct (name, can_inline) tuples |
| `test_branch_routing` | Only chosen branch target activates |
| `test_run_state_independence` | Multiple `new_run_state()` calls don't share state |

**hush-core regression tests** (`cd hush-core && uv run -m pytest`):
- All existing tests must pass unchanged (they use Python mode by default)
- The `initial_ready_count` rename must not break any test

**End-to-end correctness** (run tutorial examples):
```bash
cd hush-tutorial && uv run python examples/16_graph.py   # @graph examples
cd hush-tutorial && uv run python examples/05_flow.py     # branch + loop
cd hush-tutorial && uv run python examples/13_parallel.py # parallel patterns
```

### 1.3 Benchmarks

**Purpose**: Verify refactor is performance-neutral. Same benchmarks, same results.

#### 1.3.1 Pytest benchmark tests (`hush-runtime/tests/test_benchmark.py`)

Automated latency comparison tests that run via `uv run -m pytest tests/test_benchmark.py`. Each test builds a graph, runs it in both modes, and asserts Rust is not slower than Python beyond a tolerance.

**Pass criteria**: Rust mode mean latency ≤ Python mode mean latency × 1.15 (15% regression tolerance to account for noise).

| Test | Graph | What it asserts |
|---|---|---|
| `test_bench_linear` | 50-op linear chain | Rust ≤ Python × 1.15 |
| `test_bench_nested_graph` | 5-stage nested @graph (3-level deep) | Rust ≤ Python × 1.15 |
| `test_bench_parallel_nested` | 20 parallel @graph branches → join | Rust ≤ Python × 1.15 |
| `test_bench_branching` | 10-stage if_() routing (4 paths each) | Rust ≤ Python × 1.15 |
| `test_bench_map_op` | MapOp with 50 items | Rust ≤ Python × 1.15 |
| `test_bench_production` | 5 parallel verify subgraphs → aggregate | Rust ≤ Python × 1.15 |

Each test:
1. Builds the graph once
2. Warms up both modes (5 runs each)
3. Measures 50 runs per mode
4. Asserts `mean(rust_times) <= mean(python_times) * 1.15`
5. Prints speedup for visibility

```bash
cd hush-runtime && uv run -m pytest tests/test_benchmark.py -v
```

#### 1.3.2 Manual benchmark scripts (for detailed profiling)

**Files**: `hush-runtime/benches/bench_e2e.py`, `hush-runtime/benches/bench_scheduler.py`

Run for detailed latency tables with p50/p99/memory:
```bash
cd hush-runtime && uv run python benches/bench_e2e.py
cd hush-runtime && uv run python benches/bench_scheduler.py
```

### 1.4 Verification Steps

```bash
# 1. Rust tests
cd hush-runtime && cargo test

# 2. Build
cd hush-runtime && uv run maturin develop --release

# 3. Runtime Python tests (includes benchmark assertions)
cd hush-runtime && uv run -m pytest tests/ -v

# 4. Core regression
cd hush-core && uv run -m pytest

# 5. Manual benchmark (optional, for detailed profiling)
cd hush-runtime && uv run python benches/bench_e2e.py

# 6. Tutorial end-to-end
cd hush-tutorial && uv run python examples/16_graph.py
```

---

## Phase 2: Rust State Management ✅ COMPLETE

**Goal**: Move `Cell`, `MemoryState`, `StateSchema` to Rust. Eliminate Python dict overhead for state reads/writes during scheduling.

**Status**: Complete. `RustMemoryState` is a drop-in replacement for `MemoryState`, built from Python `StateSchema` via `from_schema()`. 84 tests pass (31 state + 22 equivalence + 26 scheduler + 5 mode). hush-core regression: 581 passed.

**Design decisions**:
- `Cell` is internal Rust struct (not `#[pyclass]`) — never accessed from Python directly
- `StateSchema` stays in Python — `_load_from()` needs Python op objects for tree walking
- `Ref._fn` callbacks stored as `PyObject` in Rust, called back into Python for ref resolution
- `RustMemoryState.from_schema()` extracts schema data once at construction, then O(1) access

**Benchmark results** (from `bench_e2e.py`):
- Linear 500 ops: 1.23x speedup, 12x less memory (751KB → 61KB)
- Nested 20 stages: 1.19x speedup, 10x less memory (327KB → 32KB)
- MapOp 100 items: 1.70x speedup
- Parallel 50 branches: 1.13x speedup, 5x less memory (351KB → 71KB)

### 2.1 Folder structure after Phase 2

```
hush-runtime/src/
├── lib.rs
├── ops/
│   └── graph/
│       ├── mod.rs
│       └── graph_op.rs         # CompiledGraph, RunState
└── states/
    ├── mod.rs                  # re-exports
    ├── cell.rs                 # Cell (maps to hush-core states/cell.py)
    ├── schema.rs               # StateSchema (maps to hush-core states/schema.py)
    ├── state.rs                # MemoryState (maps to hush-core states/state.py)
    └── ref_index.rs            # Ref index/lookup types (maps to hush-core states/ref.py)
```

### 2.2 What moves to Rust

| Python class | Rust struct | Key data |
|---|---|---|
| `Cell` (`states/cell.py`) | `Cell` (`states/cell.rs`) | `contexts: HashMap<String, PyObject>`, `default_value: PyObject` |
| `MemoryState` (`states/state.py`) | `MemoryState` (`states/state.rs`) | `_cells: Vec<Cell>`, `__getitem__`/`__setitem__` with O(1) index |
| `StateSchema` (`states/schema.py`) | `StateSchema` (`states/schema.rs`) | `_var_to_idx: HashMap<(String,String), usize>`, `_defaults`, `_pull_refs`, `_push_refs` |

**What stays in Python**:
- `Ref` class and `Ref._fn` callbacks (compiled Python lambdas)
- `Ref._ops` chain (Python AST-based)
- `StateSchema._load_from()` tree walking (needs access to Python op objects)

**What changes in integration**:
- `engine.py`: `StateSchema.create_state()` returns Rust-backed `MemoryState` when `mode="rust"`
- `GraphOp._run_rust_scheduled()`: state reads/writes now go through Rust `MemoryState` — faster than Python dict
- `BaseOp.run()`: `state[op_name, var, ctx] = value` calls Rust `__setitem__`

### 2.3 Test Cases

**Rust unit tests** (`states/cell.rs`):

| Test | What it verifies |
|---|---|
| `test_cell_default_context` | `cell[None] = 42; cell[None] == 42` |
| `test_cell_named_context` | `cell["iter[0]"] = 1; cell["iter[1]"] = 2` — independent |
| `test_cell_default_value` | Unset context returns `default_value` |
| `test_cell_pop_context` | `pop_context("iter[0]")` removes and returns value |

**Rust unit tests** (`states/state.rs`):

| Test | What it verifies |
|---|---|
| `test_memory_state_set_get` | `state[(op, var, ctx)] = val; state[(op, var, ctx)] == val` |
| `test_memory_state_pull_ref` | Input ref pulls from source cell on first read |
| `test_memory_state_push_ref` | Output ref pushes to target cell on write |
| `test_memory_state_multi_context` | Iteration contexts are independent |

**Rust unit tests** (`states/schema.rs`):

| Test | What it verifies |
|---|---|
| `test_schema_get_index` | `get_index("op_name", "var_name")` returns correct index |
| `test_schema_create_state` | Returns MemoryState with correct cell count and defaults |
| `test_schema_unknown_var` | Returns -1 for unknown (op, var) pairs |

**Python integration tests** (`hush-runtime/tests/test_state.py`):

| Test | What it verifies |
|---|---|
| `test_rust_memory_state_basic` | Create state, set/get values, verify correctness |
| `test_rust_memory_state_with_graph` | Build a GraphOp, create Rust state, run workflow |
| `test_rust_vs_python_state_equivalence` | Same graph, same inputs → identical outputs for both modes |
| `test_state_with_iteration` | ForOp/MapOp with Rust state — iteration contexts work |
| `test_state_with_nested_graph` | Nested @graph with Rust state |

**hush-core regression**: `cd hush-core && uv run -m pytest` — all tests pass in Python mode.

**Cross-mode correctness** (`hush-runtime/tests/test_equivalence.py`):
```python
@pytest.mark.parametrize("mode", ["python", "rust"])
async def test_linear_workflow(mode):
    """Same graph produces identical outputs in both modes."""
    with GraphOp(name="linear") as graph:
        a = double(x=PARENT["input"])
        b = add(a=a["result"], b=PARENT["y"])
        START >> a >> b >> END
    result = await Hush(graph, mode=mode).run(inputs={"input": 5, "y": 3})
    assert result["result"] == 13

@pytest.mark.parametrize("mode", ["python", "rust"])
async def test_branch_workflow(mode):
    """Branch routing works identically in both modes."""
    # ... branch graph with if_() conditions ...

@pytest.mark.parametrize("mode", ["python", "rust"])
async def test_iteration_workflow(mode):
    """MapOp/ForOp produce identical results in both modes."""
    # ... iteration graph with Each() ...

@pytest.mark.parametrize("mode", ["python", "rust"])
async def test_nested_graph_workflow(mode):
    """Nested @graph produces identical results in both modes."""
    # ... nested graph using @graph decorator ...
```

### 2.4 Benchmarks

#### 2.4.1 Pytest benchmark tests (`hush-runtime/tests/test_benchmark.py`)

Extend Phase 1 benchmark tests with state-focused assertions:

| Test | Graph | What it asserts |
|---|---|---|
| `test_bench_state_heavy_linear` | 100-op linear chain (1 read + 1 write per op) | Rust ≤ Python × 1.0 (expect improvement) |
| `test_bench_state_iteration` | MapOp with 50 items × 5 vars | Rust ≤ Python × 1.0 |
| `test_bench_state_nested` | 10-stage nested @graph | Rust ≤ Python × 1.0 |

**Expected improvement**: 2-5x on state-heavy workflows.

```bash
cd hush-runtime && uv run -m pytest tests/test_benchmark.py -v
```

#### 2.4.2 Manual benchmark scripts

**New file**: `hush-runtime/benches/bench_state.py` — isolate state access overhead:
```python
# Pattern 1: Raw state access (no scheduling)
# Create MemoryState with N cells, measure set/get throughput
# N = 100, 500, 1000, 5000

# Pattern 2: State access during scheduling
# Linear graph with N ops, each reads 1 input + writes 1 output
# N = 10, 50, 100, 500

# Pattern 3: Multi-context iteration
# MapOp with C concurrent iterations, each accessing M variables
# C = 10, 50, 100; M = 5, 20
```

**E2E benchmark**: Update `bench_e2e.py` — same 9 patterns, expect improvement on state-heavy patterns (nested, iteration, production-like).

### 2.5 Verification Steps

```bash
# 1. Rust tests
cd hush-runtime && cargo test

# 2. Build
cd hush-runtime && uv run maturin develop --release

# 3. Python tests (includes benchmark assertions + equivalence)
cd hush-runtime && uv run -m pytest tests/ -v

# 4. Core regression
cd hush-core && uv run -m pytest

# 5. Manual benchmarks (optional, for detailed profiling)
cd hush-runtime && uv run python benches/bench_state.py
cd hush-runtime && uv run python benches/bench_e2e.py
```

---

## Phase 3: Rust Op Execution Bridge ✅ COMPLETE

**Goal**: Allow `@op` functions to have Rust implementations that execute without Python callback. GIL release for true multi-core.

**Status**: Complete. `RustFuncRegistry` provides global registry of Rust-native ops. `@rust_op` decorator links Python ops to Rust implementations. `BaseOp.run()` dispatches to Rust when available, falls back to Python otherwise. 96 tests pass. hush-core regression: 581 passed.

**What was built**:
- `RustOp` trait + `RustFuncRegistry` #[pyclass] singleton in `src/ops/transform/func_op.rs`
- 3 built-in ops: `rust_double`, `rust_add`, `rust_hash_chain` (CPU-heavy with GIL release)
- `@rust_op` decorator in `hush_runtime/__init__.py`
- `BaseOp._try_rust_execute()` dispatch hook in `hush-core/hush/core/ops/base.py`
- 17 integration tests in `test_rust_op.py`

### 3.1 Folder structure after Phase 3

```
hush-runtime/src/
├── lib.rs
├── ops/
│   ├── mod.rs
│   ├── base.rs                 # Op execution dispatch (maps to ops/base.py)
│   ├── graph/
│   │   ├── mod.rs
│   │   └── graph_op.rs
│   └── transform/
│       ├── mod.rs
│       └── func_op.rs          # RustFuncRegistry (maps to ops/transform/func_op.py)
├── states/
│   └── ...                     # (from Phase 2)
└── utils/
    ├── mod.rs
    └── common.rs               # Param type (maps to utils/common.py)
```

### 3.2 What moves to Rust

**`RustFuncRegistry`** (`ops/transform/func_op.rs`):
- Global `HashMap<String, Box<dyn RustOp>>` — maps op names to Rust implementations
- `RustOp` trait: `fn execute(&self, inputs: HashMap<String, PyObject>) -> PyResult<HashMap<String, PyObject>>`
- Auto-registered at module init

**`@rust_op` decorator** (Python side, in `hush_runtime/__init__.py`):
```python
def rust_op(name: str):
    """Link a Python @op to its Rust implementation."""
    def decorator(func):
        func._rust_op_name = name  # Tag for runtime dispatch
        return func
    return decorator
```

**Execution dispatch** (`ops/base.rs`):
- Before calling Python `op.core()`, check if `_rust_op_name` exists
- If yes, call Rust implementation inside `py.allow_threads()` — GIL released, true parallelism
- If no, fall back to Python `op.core()` as before

**What changes in hybrid**:
- `_run_rust_scheduled()` checks `can_run_in_rust` flag for each op
- Rust ops: `CompiledGraph.execute_op(name, inputs)` → runs in Rust, returns outputs
- Python ops: same `op.run(state, ctx, parent_ctx)` callback as before

### 3.3 Test Cases

**Rust unit tests** (`ops/transform/func_op.rs`):

| Test | What it verifies |
|---|---|
| `test_registry_register_and_lookup` | Register a Rust fn, look it up by name |
| `test_registry_execute` | Execute registered fn with inputs, verify outputs |
| `test_registry_missing_op` | Lookup non-existent op returns None |
| `test_execute_with_gil_release` | Fn runs inside `allow_threads()`, verify no GIL held |

**Python integration tests** (`hush-runtime/tests/test_rust_op.py`):

| Test | What it verifies |
|---|---|
| `test_rust_op_decorator` | `@rust_op("double")` tags function correctly |
| `test_rust_op_execution` | Rust-implemented op produces correct output |
| `test_rust_op_in_graph` | Graph with mix of Rust and Python ops runs correctly |
| `test_rust_op_parallel_execution` | Multiple Rust ops run truly in parallel (GIL released) |
| `test_fallback_to_python` | Unregistered ops fall back to Python gracefully |

**Cross-mode correctness** (extend `test_equivalence.py`):
```python
@pytest.mark.parametrize("mode", ["python", "rust"])
async def test_mixed_rust_python_ops(mode):
    """Graph with both Rust and Python ops produces identical results."""

@pytest.mark.parametrize("mode", ["python", "rust"])
async def test_rust_op_with_iteration(mode):
    """Rust ops inside MapOp/ForOp work correctly."""
```

### 3.4 Benchmarks

#### 3.4.1 Pytest benchmark tests (`hush-runtime/tests/test_benchmark.py`)

Extend with Rust op and GIL-release assertions:

| Test | Graph | What it asserts |
|---|---|---|
| `test_bench_pure_rust_chain` | 50-op chain, all @rust_op | Rust ≥ 5x vs Python @op |
| `test_bench_mixed_graph` | 100 ops, 50% Rust / 50% Python | Rust ≥ 2x vs pure Python |
| `test_bench_cpu_parallel_scaling` | Fan-out N=4 parallel CPU @rust_ops | Rust ≥ 2x vs single-threaded (GIL released) |
| `test_bench_rust_vs_thread_executor` | 4 parallel CPU ops: @rust_op vs executor="thread" | @rust_op ≥ 1.5x vs thread executor |

**Expected improvement**: 5-20x for pure Rust op chains; near-linear CPU scaling for parallel Rust ops.

```bash
cd hush-runtime && uv run -m pytest tests/test_benchmark.py -v
```

#### 3.4.2 Manual benchmark scripts

**New file**: `hush-runtime/benches/bench_rust_ops.py` — detailed profiling with N sweeps:
```python
# Pattern 1: Pure Rust ops chain (N = 10, 50, 100, 500)
# Pattern 2: Mixed graph (R = 0%, 25%, 50%, 75%, 100%)
# Pattern 3: CPU-bound parallel GIL release (N = 2, 4, 8, 16)
# Pattern 4: Rust ops vs Python executor="thread"
```

### 3.5 Verification Steps

```bash
# 1. Rust tests
cd hush-runtime && cargo test

# 2. Build
cd hush-runtime && uv run maturin develop --release

# 3. Python tests (includes benchmark assertions + equivalence)
cd hush-runtime && uv run -m pytest tests/ -v

# 4. Core regression
cd hush-core && uv run -m pytest

# 5. Manual benchmarks (optional, for detailed profiling)
cd hush-runtime && uv run python benches/bench_rust_ops.py
cd hush-runtime && uv run python benches/bench_e2e.py
```

---

## Phase 4: Rust-Native Ops + Branch Resolution ✅ COMPLETE

**Goal**: Move branch resolution to Rust (eliminates last Python callback in scheduling loop). Add built-in Rust ops.

### Implementation Summary

**Branch resolution in Rust** — eliminated Python callback for `op.get_target(state, ctx)`:
- Added `branch_full_names: AHashMap<String, String>` to `CompiledGraph` (short_name → full_name)
- Added `RustMemoryState::get_string()` — `pub(crate)` helper for direct state reads from Rust
- Added `CompiledGraph::activate_successors_with_state()` — combines branch target lookup + successor activation in a single Rust call
- Updated `_run_rust_scheduled()` to use the new method when state is `RustMemoryState`

**10 built-in Rust ops** registered in `RustFuncRegistry`:
- String: `rust_string_concat`, `rust_string_split`, `rust_string_template`
- JSON: `rust_json_parse`, `rust_json_extract`, `rust_json_merge`
- Math: `rust_math_sum`, `rust_math_mean`, `rust_math_max`, `rust_math_min`

**Logging** — deferred to Phase 5 (lower priority, not blocking).

**Test results**: 138 hush-runtime tests passed, 581 hush-core tests passed, 6 Rust unit tests passed.

**Design decisions**:
1. Branch target is read directly from `RustMemoryState` cells — no ref resolution needed (value was just written by the branch op's `run()` method)
2. `__END__` sentinel hardcoded in Rust to match Python `END.name`
3. Fallback to Python `get_target()` when state is not `RustMemoryState`
4. JSON ops use Python's `json` module for parsing (no serde needed — keeps PyObject compatibility)

### 4.1 Folder structure after Phase 4

```
hush-runtime/src/
├── lib.rs
├── ops/
│   ├── mod.rs
│   ├── base.rs
│   ├── graph/
│   │   ├── mod.rs
│   │   └── graph_op.rs
│   ├── transform/
│   │   ├── mod.rs
│   │   └── func_op.rs
│   └── flow/
│       ├── mod.rs
│       └── branch_op.rs        # Branch.get_target() in Rust (maps to ops/flow/branch_op.py)
├── states/
│   └── ...
├── loggings/
│   ├── mod.rs                  # LOGGER bridge (maps to loggings/__init__.py)
│   ├── config.rs               # LogConfig (maps to loggings/config.py)
│   └── formatters.rs           # format_log_data (maps to loggings/formatters.py)
└── utils/
    ├── mod.rs
    ├── common.rs
    └── auto_name.rs            # auto_name if needed (maps to utils/auto_name.py)
```

### 4.2 What moves to Rust

**Branch resolution** (`ops/flow/branch_op.rs`):
- `get_target(conditions, state, context_id) -> String` — evaluate conditions in Rust
- Conditions are compiled from Python `Ref` chains into Rust closures at build time
- Eliminates the `op.get_target(state, ctx)` Python callback from `_run_rust_scheduled()`

**Built-in Rust ops** (registered in `RustFuncRegistry`):
- `rust_string_concat`, `rust_string_split`, `rust_string_template`
- `rust_json_parse`, `rust_json_extract`, `rust_json_merge`
- `rust_math_sum`, `rust_math_mean`, `rust_math_max`, `rust_math_min`

**Loggings** (`loggings/`):
- Rust `tracing` crate for internal logging
- Bridge: Rust `tracing` events → Python `logging` module via PyO3
- `LogConfig` struct mirrors Python `LogConfig` for consistent configuration
- `format_log_data()` mirrors Python `formatters.py`

### 4.3 Test Cases

**Rust unit tests** (`ops/flow/branch_op.rs`):

| Test | What it verifies |
|---|---|
| `test_branch_simple_equality` | `if_(x == "a", "case_a")` routes correctly |
| `test_branch_comparison` | `if_(x > 10, "high")` routes correctly |
| `test_branch_compound_condition` | `(x > 10) & (y == "active")` |
| `test_branch_else_fallback` | Falls through to else target |
| `test_branch_with_rust_state` | Reads condition vars from Rust MemoryState |

**Rust unit tests** (`loggings/`):

| Test | What it verifies |
|---|---|
| `test_log_config_default` | Default config matches Python LogConfig defaults |
| `test_format_log_data` | Output matches Python format_log_data() |
| `test_tracing_bridge` | Rust tracing event reaches Python logger |

**Built-in ops tests** (`hush-runtime/tests/test_builtin_ops.py`):

| Test | What it verifies |
|---|---|
| `test_string_concat` | `["hello", "world"]` → `"helloworld"` |
| `test_json_extract` | `{"a": {"b": 1}}` + path `"a.b"` → `1` |
| `test_math_sum` | `[1, 2, 3]` → `6` |
| `test_builtin_in_graph` | Graph using built-in Rust ops produces correct output |

**Cross-mode correctness** (extend `test_equivalence.py`):
```python
@pytest.mark.parametrize("mode", ["python", "rust"])
async def test_branch_with_rust_resolution(mode):
    """Branch routing is identical when Rust resolves conditions."""

@pytest.mark.parametrize("mode", ["python", "rust"])
async def test_builtin_ops_in_workflow(mode):
    """Built-in Rust ops produce same results as Python equivalents."""
```

### 4.4 Benchmarks

#### 4.4.1 Manual benchmark scripts

**New file**: `hush-runtime/benches/bench_branch.py` — branch resolution profiling:
```python
# Pattern 1: Deep branch chain (N = 5, 10, 20, 50)
# Pattern 2: Branch + merge fan-out (N = 4, 8, 16, 32)
# Pattern 3: Nested branches (Depth = 2, 3, 4)
```

**New file**: `hush-runtime/benches/bench_builtin_ops.py` — built-in ops vs Python equivalents:
```python
# Pattern 1: Built-in Rust ops vs Python @op (N = 10, 50, 100)
```

**Expected improvement**: 3-10x on branch-heavy workflows; 10-50x on built-in ops vs Python equivalents.

### 4.5 Pytest benchmark tests (`hush-runtime/tests/test_benchmark.py`)

Extend with branch and built-in op assertions:

| Test | Graph | What it asserts |
|---|---|---|
| `test_bench_branch_resolution` | 20-stage if_() routing | Rust ≥ 3x vs Python (no Python callback) |
| `test_bench_builtin_string_ops` | 50-op chain of rust_string_concat | Rust ≥ 10x vs Python @op equivalent |
| `test_bench_builtin_json_ops` | 50-op chain of rust_json_extract | Rust ≥ 10x vs Python @op equivalent |

```bash
cd hush-runtime && uv run -m pytest tests/test_benchmark.py -v
```

### 4.6 Verification Steps

```bash
# 1. Rust tests
cd hush-runtime && cargo test

# 2. Build
cd hush-runtime && uv run maturin develop --release

# 3. Python tests (includes benchmark assertions + equivalence + logging)
cd hush-runtime && uv run -m pytest tests/ -v

# 4. Core regression
cd hush-core && uv run -m pytest

# 5. Manual benchmarks (optional, for detailed profiling)
cd hush-runtime && uv run python benches/bench_branch.py
cd hush-runtime && uv run python benches/bench_builtin_ops.py
cd hush-runtime && uv run python benches/bench_e2e.py
```

---

## Phase 5: Full Rust Pipeline — Inline Op Execution ✅ COMPLETE

**Goal**: Execute the entire op lifecycle (get_inputs → execute → store_result) in Rust for Rust-native ops, bypassing Python's `BaseOp.run()`.

**Status**: Complete. Three execution tiers implemented: (1) `run_sync()` for all-Rust graphs (zero Python callbacks), (2) `try_execute_inline()` hybrid for mixed graphs, (3) Python fallback. 158 hush-runtime tests passed, 581 hush-core tests passed.

### Implementation Summary

**OpMeta struct** — Pre-compiled per-op metadata extracted at `from_graph()` time:
- `InputMeta`: var_name, uses_parent_ctx, literal_value, default_value
- `OpMeta`: full_name, rust_op_name, inputs, output_var_names, is_rust_executable, parent_full_name
- Stored in `op_meta: AHashMap<String, OpMeta>` field on CompiledGraph

**RustMemoryState pub(crate) helpers** (`state.rs`):
- `get_value()`: Reads PyObject from state with pull-ref resolution (replicates `__getitem__`)
- `set_value()`: Writes PyObject to state with push-ref support (replicates `__setitem__`)
- `record_execution_internal()`: Records execution order without FFI crossing

**RustFuncRegistry pub(crate) helpers** (`func_op.rs`):
- `execute_internal()`: Execute Rust op without FFI boundary crossing
- `has_internal()`: Check if op is registered without FFI

**CompiledGraph new methods** (`graph_op.rs`):
- `try_execute_inline(op_name, state, ctx, parent_ctx) → bool`: Full op lifecycle in Rust — reads inputs from state, calls registry, writes outputs back, records execution. Returns true on success, false for Python fallback.
- `all_rust_executable() → bool`: Checks if all ops have `is_rust_executable=true` AND `can_inline=true`
- `run_sync(state, ctx, parent_ctx)`: Full scheduling+execution loop in Rust — zero Python callbacks

**Python scheduling updated** (`graph_op.py`, `iteration/base.py`):
- Three-tier execution: (1) `run_sync` fast path for all-Rust graphs, (2) `try_execute_inline` hybrid path, (3) Python fallback
- Both `_run_rust_scheduled()` and `_run_graph_rust()` updated

**DoubleOp/AddOp type handling** — Accept both i64 and f64 inputs for type flexibility when chaining ops

**Bug fix**: `@rust_op` decorator now sets `_rust_op_name` on both the wrapper AND `func.__wrapped__` (the inner function), since `FuncOp.core` is the original function. This also retroactively fixed Phase 3 Rust dispatch which was silently falling back to Python.

**Test results**: 20 new inline execution tests + 138 existing = 158 total. hush-core: 581 passed.

### 5.1 Final folder structure

```
hush-runtime/src/
├── lib.rs                      # PyO3 module entry
├── engine.rs                   # Hush.run() in Rust (maps to engine.py)
├── ops/
│   ├── mod.rs
│   ├── base.rs                 # BaseOp (maps to ops/base.py)
│   ├── graph/
│   │   ├── mod.rs
│   │   └── graph_op.rs         # CompiledGraph, RunState, run() (maps to ops/graph/graph_op.py)
│   ├── transform/
│   │   ├── mod.rs
│   │   └── func_op.rs          # FuncOp + RustFuncRegistry (maps to ops/transform/func_op.py)
│   ├── flow/
│   │   ├── mod.rs
│   │   └── branch_op.rs        # Branch resolution (maps to ops/flow/branch_op.py)
│   └── iteration/
│       ├── mod.rs
│       └── base.rs             # BaseIterationOp._run_graph() (maps to ops/iteration/base.py)
├── states/
│   ├── mod.rs
│   ├── cell.rs                 # Cell (maps to states/cell.py)
│   ├── schema.rs               # StateSchema (maps to states/schema.py)
│   ├── state.rs                # MemoryState (maps to states/state.py)
│   └── ref_index.rs            # Ref types (maps to states/ref.py)
├── loggings/
│   ├── mod.rs                  # LOGGER (maps to loggings/__init__.py)
│   ├── config.rs               # LogConfig (maps to loggings/config.py)
│   └── formatters.rs           # format_log_data (maps to loggings/formatters.py)
└── utils/
    ├── mod.rs
    ├── common.rs               # Param, helpers (maps to utils/common.py)
    └── auto_name.rs            # auto_name (maps to utils/auto_name.py)
```

### 5.2 What changes

- `engine.rs`: `Hush.run()` — single Rust entry point. Checks if graph is all-Rust, then executes entirely in Rust
- `graph_op.rs`: `CompiledGraph.run()` — full scheduling + op execution loop in Rust
- `iteration/base.rs`: `BaseIterationOp._run_graph()` — iteration scheduling in Rust
- **Delete** from Python: `_run_rust_scheduled()` — no longer needed, `CompiledGraph.run()` handles everything
- **Mixed graphs**: If some ops are Python-only, fall back to Phase 3 hybrid mode for those ops

**The hybrid evolution completes**:

| Phase | graph_op.py (Python) | graph_op.rs (Rust) |
|---|---|---|
| 1 | `_run_python_scheduled()` + `_run_rust_scheduled()` | `activate_successors()` only |
| 2 | Same hybrid, Rust-backed state | + `MemoryState` |
| 3 | Hybrid shrinks — Rust ops skip Python | + `execute_op()` for Rust ops |
| 4 | Branch resolution in Rust | + `get_target()` |
| **5** | **`_run_rust_scheduled()` deleted** | **`run()` — full pipeline** |

### 5.3 Test Cases

**Rust unit tests** (`engine.rs`):

| Test | What it verifies |
|---|---|
| `test_full_rust_linear` | All-Rust linear graph runs entirely in Rust |
| `test_full_rust_parallel` | All-Rust parallel graph with fork-join |
| `test_full_rust_branch` | All-Rust branch graph with condition evaluation |
| `test_full_rust_iteration` | All-Rust MapOp/ForOp |
| `test_mixed_fallback` | Graph with Python ops falls back to hybrid |
| `test_nested_full_rust` | Nested @graph, all Rust ops |

**Python integration tests** (`hush-runtime/tests/test_full_pipeline.py`):

| Test | What it verifies |
|---|---|
| `test_full_rust_end_to_end` | `Hush(graph, mode="rust").run()` with all Rust ops |
| `test_mixed_graph_fallback` | Graph with mix of Rust/Python ops works correctly |
| `test_full_rust_vs_python_equivalence` | Same graph, all Rust ops, mode="rust" vs mode="python" — identical results |
| `test_full_rust_with_tracing` | Rust pipeline integrates with hush-core tracing |
| `test_error_propagation` | Rust op error → Python OpError with correct traceback |

**Comprehensive cross-mode correctness** (`test_equivalence.py` — final version):
```python
# Run ALL tutorial examples in both modes
@pytest.mark.parametrize("mode", ["python", "rust"])
@pytest.mark.parametrize("example", [
    "01_hello.py", "02_chain.py", "05_flow.py",
    "13_parallel.py", "16_graph.py"
])
async def test_tutorial_example(mode, example):
    """Every tutorial example produces identical output in both modes."""
```

### 5.4 Benchmarks

#### 5.4.1 Pytest benchmark tests (`hush-runtime/tests/test_benchmark.py`)

Final benchmark assertions — full Rust pipeline:

| Test | Graph | What it asserts |
|---|---|---|
| `test_bench_full_rust_linear` | 100-op chain, all Rust ops | Rust ≥ 20x vs Python |
| `test_bench_full_rust_parallel` | 100 parallel Rust ops → join | Rust ≥ 20x vs Python |
| `test_bench_full_rust_branch` | 50-stage branch, all Rust | Rust ≥ 30x vs Python |
| `test_bench_full_rust_iteration` | MapOp 100×50, all Rust | Rust ≥ 20x vs Python |
| `test_bench_throughput` | 100-op graph, 1000 sequential runs | ≥ 10x ops/sec improvement |
| `test_bench_memory` | 1000-op graph, measure peak RSS | Rust ≤ 50% of Python memory |

**Expected improvement**: 20-100x for all-Rust graphs; memory 2-5x lower.

```bash
cd hush-runtime && uv run -m pytest tests/test_benchmark.py -v
```

#### 5.4.2 Manual benchmark scripts

**New file**: `hush-runtime/benches/bench_full_pipeline.py` — detailed profiling:
```python
# Pattern 1: Full Rust vs Python baseline (all E2E patterns, all-Rust ops)
# Pattern 2: Scaling test (N = 10, 100, 1000, 10000)
# Pattern 3: Throughput test (M = 100, 1000 sequential runs)
# Pattern 4: Memory comparison (tracemalloc vs /proc/self/status)
```

### 5.5 Verification Steps

```bash
# 1. Rust tests
cd hush-runtime && cargo test

# 2. Build
cd hush-runtime && uv run maturin develop --release

# 3. Python tests (includes ALL benchmark assertions + equivalence)
cd hush-runtime && uv run -m pytest tests/ -v

# 4. Core regression
cd hush-core && uv run -m pytest

# 5. Tutorial examples
cd hush-tutorial && uv run python examples/16_graph.py
cd hush-tutorial && uv run python examples/05_flow.py
cd hush-tutorial && uv run python examples/13_parallel.py

# 6. Manual benchmarks (optional, for detailed profiling)
cd hush-runtime && uv run python benches/bench_full_pipeline.py
cd hush-runtime && uv run python benches/bench_e2e.py
```

---

## Complete Python ↔ Rust Mapping Table

| Python file (hush-core/hush/core/) | Rust file (hush-runtime/src/) | Phase | Python class → Rust struct |
|---|---|---|---|
| `engine.py` | `engine.rs` | 5 | `Hush` → `Hush` |
| `ops/graph/graph_op.py` | `ops/graph/graph_op.rs` | 1 | GraphOp attrs → `CompiledGraph`; per-run locals → `RunState` |
| `ops/base.py` | `ops/base.rs` | 3 | `BaseOp.run()` dispatch |
| `ops/transform/func_op.py` | `ops/transform/func_op.rs` | 3 | `FuncOp` → `RustFuncRegistry` |
| `ops/flow/branch_op.py` | `ops/flow/branch_op.rs` | 4 | `Branch.get_target()` |
| `ops/iteration/base.py` | `ops/iteration/base.rs` | 5 | `BaseIterationOp._run_graph()` |
| `states/cell.py` | `states/cell.rs` | 2 | `Cell` → `Cell` |
| `states/state.py` | `states/state.rs` | 2 | `MemoryState` → `MemoryState` |
| `states/schema.py` | `states/schema.rs` | 2 | `StateSchema` → `StateSchema` |
| `states/ref.py` | `states/ref_index.rs` | 2 | Ref index types only |
| `loggings/__init__.py` | `loggings/mod.rs` | 4 | `LOGGER`, `setup_logger()` |
| `loggings/config.py` | `loggings/config.rs` | 4 | `LogConfig`, `HandlerConfig` |
| `loggings/formatters.py` | `loggings/formatters.rs` | 4 | `format_log_data()` |
| `utils/common.py` | `utils/common.rs` | 3 | `Param` struct |
| `utils/auto_name.py` | `utils/auto_name.rs` | 4 | `auto_name()` (if needed) |

---

## Cumulative Latency Improvement Expectations

| Phase | What's accelerated | Expected speedup vs pure Python |
|---|---|---|
| 1 (done) | Scheduling decisions only | 1.1-2.5x |
| 2 (done) | + State read/write | 1.1-1.7x latency, 5-12x less memory |
| 3 (done) | + Rust op execution (GIL-free) | Bridge ready; built-in ops in Phase 4 |
| 4 (done) | + Branch resolution + 10 built-in ops | Branch callback eliminated; ops ready |
| 5 (done) | + Inline op execution + run_sync | Zero Python for all-Rust graphs |

Each phase is independently shippable and benchmarked. Later phases build on earlier ones — if Phase 2 is delayed, Phase 1 still provides value.
