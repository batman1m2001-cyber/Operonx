# Hush Rust Mode — Builder-Executor Architecture Refactor Plan

## Why This Refactor

The current Rust mode is a **hybrid accelerator** — Rust code is sprinkled throughout Python ops (`_run_rust_scheduled`, `_run_graph_rust`, `_try_rust_execute`, `_compiled_graph_rs`) creating messy conditional branching everywhere. Every Python op has `if rust: ... else: python ...` branches. This is hard to maintain, hard to reason about, and couples hush-core tightly to rush-core internals.

The new architecture is a clean **Builder-Executor** separation:

- **Python = Builder** — constructs the workflow graph using the rich DSL (`>>`, `@op`, `PARENT`, etc.)
- **Rust = Executor** — receives a serialized config dict, reconstructs the workflow internally, runs everything natively in Rust
- **Python callbacks** — only for ops without Rust implementations (unavoidable FFI)

After this refactor, **Python ops have ZERO Rust-related code**. The mode decision happens once at the engine level in `Hush(graph, mode="rust")`.

---

## Project Context

```
Hush-ai/
├── hush-core/          # Core engine: ops, state, scheduling, tracing
├── rush-core/          # Rust backend (PyO3) — THIS PACKAGE, to be rewritten
├── hush-providers/     # LLM, embedding, reranking integrations
├── hush-tutorial/      # Docs + examples
└── hush-eyes/          # Rust trace visualization server
```

### How Hush Scheduling Works (Python)

`GraphOp` in `hush-core/hush/core/ops/graph/graph_op.py` is the core container. It holds child ops in a DAG and executes them with a ready-count scheduler:

```python
class GraphOp(BaseOp):
    # Build-time (set once in build()):
    self._compiled_adj = {}        # {op_name: [(successor, is_soft), ...]}
    self.initial_ready_count = {}  # {op_name: int}
    self.entries = []              # ops with ready_count == 0
    self.has_soft_preds = set()    # ops with soft predecessors

    # Per-run scheduling:
    async def _run_python_scheduled(self, state, context_id, parent_context):
        ready_count = self.initial_ready_count.copy()
        soft_satisfied = set()
        # Schedule loop: activate successors when ready_count hits 0
        # Inline sync leaf ops, create asyncio.Task for async/graph ops
```

`BaseIterationOp` in `hush-core/hush/core/ops/iteration/base.py` has a duplicate scheduler in `_run_graph_python()`.

### Key Classes

| Class | File | Purpose |
|-------|------|---------|
| `BaseOp` | `ops/base.py` | Base for all ops — `run()`, `get_inputs()`, `store_result()` |
| `GraphOp` | `ops/graph/graph_op.py` | DAG container with ready-count scheduler |
| `FuncOp` | `ops/transform/func_op.py` | Wraps a Python function as an op (`@op` decorator) |
| `BranchOp` | `ops/flow/branch_op.py` | Conditional routing with `if_()` conditions |
| `ForOp` | `ops/iteration/for_op.py` | Sequential iteration |
| `MapOp` | `ops/iteration/map_op.py` | Parallel iteration with concurrency |
| `WhileOp` | `ops/iteration/while_op.py` | Conditional loop |
| `AIterOp` | `ops/iteration/aiter_op.py` | Async iterator processing |
| `Ref` | `states/ref.py` | Zero-copy variable reference with chainable operations |
| `StateSchema` | `states/schema.py` | Compile-time state structure with indexed access |
| `MemoryState` | `states/state.py` | Runtime state storage (Cell-based, multi-context) |
| `Hush` | `engine.py` | Workflow execution engine entry point |

### Ref System (Critical for Serialization)

`Ref` in `states/ref.py` has:
- `_source`: Node reference (BaseOp or string)
- `var`: Variable name
- `_ops`: List of operation tuples — **SERIALIZABLE** (e.g., `[("ge", (90,)), ("and_", (other_ref,))]`)
- `_fn`: Compiled lambda chain — **NOT SERIALIZABLE** (rebuilt from `_ops`)
- `idx`: Storage index (set by `StateSchema._build()`)

The `_ops` list is the serializable representation. Rust can rebuild the equivalent of `_fn` from `_ops` using a Ref operation interpreter.

### Current Rust Hybrid Code (TO BE REMOVED)

| File | Rust Code | Lines |
|------|-----------|-------|
| `ops/base.py` | `_try_rust_execute()` method | 718-727 |
| `ops/base.py` | `_rust_op_name` dispatch in `run()` | 759-771 |
| `ops/graph/graph_op.py` | `_compiled_graph_rs` in `__slots__` | 176 |
| `ops/graph/graph_op.py` | `_build_compiled_graph_rs()` | 330-351 |
| `ops/graph/graph_op.py` | `_run_rust_scheduled()` | 436-520 (~85 lines) |
| `ops/graph/graph_op.py` | Rust/Python branching in `run()` | 932-939 |
| `ops/iteration/base.py` | `_run_graph()` dispatcher | 124-133 |
| `ops/iteration/base.py` | `_run_graph_rust()` | 135-211 (~77 lines) |
| `engine.py` | `_build_compiled_graph_rs` call in `__init__` | 94-101 |
| `engine.py` | `_create_rust_state()` method | 196-217 |
| `engine.py` | mode check in `run()` | 163-171 |

**Total: ~300 lines of Rust hybrid code to remove.**

---

## Phase 1: Clean hush-core — Remove All Rust Hybrid Code ✅ COMPLETE

**Goal**: Revert all Python ops to pure Python execution. No `_compiled_graph_rs`, no `_run_rust_*`, no `_try_rust_execute`. Zero semantic changes to Python execution path.

**Status**: Complete. ~300 lines of Rust hybrid code removed. 581 hush-core tests pass, 0 failures.

### 1.1 — `hush-core/hush/core/ops/base.py`

**Remove** `_try_rust_execute()` method (lines 718-727):

```python
# DELETE THIS ENTIRE METHOD:
def _try_rust_execute(self, rust_name: str, inputs: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Try to execute via Rust registry. Returns None if not available."""
    try:
        from rush_core import RustFuncRegistry
        if RustFuncRegistry is None or not RustFuncRegistry.has(rust_name):
            return None
        return RustFuncRegistry.execute(rust_name, inputs)
    except (ImportError, Exception):
        return None
```

**Simplify** `run()` method (lines 759-771) — remove `_rust_op_name` dispatch:

```python
# BEFORE (lines 759-771):
_rust_name = getattr(self.core, "_rust_op_name", None)
if _rust_name:
    _outputs = self._try_rust_execute(_rust_name, _inputs)
if _rust_name and _outputs is not None:
    pass  # Rust execution succeeded
elif asyncio.iscoroutinefunction(self.core):
    _outputs = await self.core(**_inputs)
elif self.executor == "thread":
    _outputs = await asyncio.to_thread(self.core, **_inputs)
else:
    _outputs = self.core(**_inputs)

# AFTER:
if asyncio.iscoroutinefunction(self.core):
    _outputs = await self.core(**_inputs)
elif self.executor == "thread":
    _outputs = await asyncio.to_thread(self.core, **_inputs)
else:
    _outputs = self.core(**_inputs)
```

### 1.2 — `hush-core/hush/core/ops/graph/graph_op.py`

**Remove** from `__slots__` (line 176):
```python
"_compiled_graph_rs",  # → REMOVE THIS LINE
```

**Remove** initialization in `__init__` (around line 194):
```python
self._compiled_graph_rs = None  # → REMOVE THIS LINE
```

**Delete** `_build_compiled_graph_rs()` method entirely (lines 330-351):
```python
# DELETE THIS ENTIRE METHOD (22 lines)
def _build_compiled_graph_rs(self):
    """Attempt to build a CompiledGraph from this graph's compiled topology."""
    ...
```

**Delete** `_run_rust_scheduled()` method entirely (lines 436-520):
```python
# DELETE THIS ENTIRE METHOD (85 lines)
async def _run_rust_scheduled(self, state, context_id, parent_context):
    """Scheduling loop driven by CompiledGraph."""
    ...
```

**Delete** `_run_python_scheduled()` as a separate method (lines 353-434) and **inline** its scheduling logic directly into `run()`. There should be no `_run_scheduled()` or `_run_python_scheduled()` — just `run()`.

**Rewrite** `run()` method — inline scheduling directly (lines 924-939 become):
```python
# BEFORE:
try:
    _inputs = self.get_inputs(state, context_id=context_id, parent_context=parent_context)
    if self._is_building:
        raise ValueError(...)
    # Rust-accelerated scheduling
    if self._compiled_graph_rs is not None:
        _outputs = await self._run_rust_scheduled(state, context_id, parent_context)
        self.store_result(state, _outputs, context_id)
    else:
        _outputs = await self._run_python_scheduled(state, context_id, parent_context)
        self.store_result(state, _outputs, context_id)

# AFTER — scheduling logic inlined into run():
try:
    _inputs = self.get_inputs(state, context_id=context_id, parent_context=parent_context)
    if self._is_building:
        raise ValueError(...)

    # --- Scheduling loop (inlined, no separate method) ---
    active_tasks: Dict[str, asyncio.Task] = {}
    ready_count: Dict[str, int] = self.initial_ready_count.copy()
    soft_satisfied: set = set()
    nodes = self._ops
    compiled_adj = self._compiled_adj

    def _can_inline(op_obj: BaseOp) -> bool:
        return (
            not isinstance(op_obj, GraphOp)
            and not asyncio.iscoroutinefunction(getattr(op_obj, "core", None))
            and getattr(op_obj, "executor", None) is None
        )

    def _get_successors(op_name: str) -> list:
        current_op = nodes[op_name]
        if current_op.type == "branch":
            branch_target = current_op.get_target(state, context_id)
            if branch_target == END.name:
                return []
            for tgt, soft in compiled_adj[op_name]:
                if tgt == branch_target:
                    return [(tgt, soft)]
            raise KeyError(...)
        return compiled_adj[op_name]

    def _activate_successors(op_name: str) -> list:
        newly_ready = []
        for next_op, is_soft in _get_successors(op_name):
            if is_soft:
                if next_op in soft_satisfied:
                    continue
                soft_satisfied.add(next_op)
            ready_count[next_op] -= 1
            if ready_count[next_op] == 0:
                newly_ready.append(next_op)
        return newly_ready

    async def _schedule_ops(names: list):
        queue = list(names)
        while queue:
            name = queue.pop(0)
            op_obj = nodes[name]
            if _can_inline(op_obj):
                await op_obj.run(state, context_id, parent_context)
                queue.extend(_activate_successors(name))
            else:
                active_tasks[name] = asyncio.create_task(
                    name=name, coro=op_obj.run(state, context_id, parent_context)
                )

    await _schedule_ops(self.entries)
    while active_tasks:
        done_tasks, _ = await asyncio.wait(
            active_tasks.values(), return_when=asyncio.FIRST_COMPLETED
        )
        for task in done_tasks:
            op_name = task.get_name()
            active_tasks.pop(op_name)
            await _schedule_ops(_activate_successors(op_name))

    _outputs = self.get_outputs(state, context_id=context_id, parent_context=parent_context)
    self.store_result(state, _outputs, context_id)
```

This eliminates the `_run_python_scheduled` / `_run_rust_scheduled` indirection entirely. GraphOp has one `run()` method that does everything.

### 1.3 — `hush-core/hush/core/ops/iteration/base.py`

**Delete** `_run_graph_rust()` method entirely (lines 135-211, ~77 lines):
```python
# DELETE THIS ENTIRE METHOD
async def _run_graph_rust(self, state, context_id, parent_context):
    """Run child nodes using CompiledGraph."""
    ...
```

**Rename** `_run_graph_python()` → `_run_graph()` (lines 213-270).

**Remove** the old `_run_graph()` dispatcher (lines 124-133):
```python
# DELETE THIS DISPATCHER — _run_graph_python is renamed to _run_graph directly
async def _run_graph(self, state, context_id, parent_context):
    if self._compiled_graph_rs is not None:
        return await self._run_graph_rust(state, context_id, parent_context)
    return await self._run_graph_python(state, context_id, parent_context)
```

The 4 subclass calls (`for_op.py`, `map_op.py`, `while_op.py`, `aiter_op.py`) all call `self._run_graph(...)` — they will still work after the rename since the renamed method takes its place.

### 1.4 — `hush-core/hush/core/engine.py`

**Simplify** `__init__` — remove Rust init logic (lines 83-101):
```python
# BEFORE:
if mode not in ("python", "rust"):
    raise ValueError(...)
self._mode = mode
...
if mode == "rust":
    self.graph._build_compiled_graph_rs()
    if self.graph._compiled_graph_rs is None:
        LOGGER.warning(...)
        self._mode = "python"

# AFTER: Keep mode parameter but don't do any Rust setup.
# The mode will be used in Phase 4 to delegate to Rush.
if mode not in ("python", "rust"):
    raise ValueError(...)
self._mode = mode
self.graph = graph
self.name = graph.name
self.graph.build()
self._schema = StateSchema(self.graph)
```

**Delete** `_create_rust_state()` method entirely (lines 196-217).

**Simplify** `run()` — remove mode check (lines 163-171):
```python
# BEFORE:
if self._mode == "rust":
    state = self._create_rust_state(...)
else:
    state = self._schema.create_state(...)

# AFTER:
state = self._schema.create_state(
    inputs=inputs,
    user_id=user_id,
    session_id=session_id,
    request_id=request_id,
)
```

### 1.5 — Verification

```bash
# All existing hush-core tests must pass unchanged
cd hush-core && uv run -m pytest
```

**Results**:
- `cd hush-core && uv run -m pytest` → 581 passed, 1 skipped in 5.24s
- Zero semantic changes to Python execution path

**What was removed**:

| File | Removed | Lines saved |
|------|---------|-------------|
| `ops/base.py` | `_try_rust_execute()`, `_rust_op_name` dispatch in `run()` | ~17 |
| `ops/graph/graph_op.py` | `_compiled_graph_rs` slot/init, `_build_compiled_graph_rs()`, `_run_rust_scheduled()`, `_run_python_scheduled()` (inlined into `run()`) | ~190 |
| `ops/iteration/base.py` | `_run_graph()` dispatcher, `_run_graph_rust()`, renamed `_run_graph_python` → `_run_graph` | ~90 |
| `engine.py` | `_create_rust_state()`, Rust init block in `__init__`, mode check in `run()` | ~30 |

---

## Phase 2: Add `serialize()` to Python Ops ✅ COMPLETE

**Goal**: Python ops can serialize themselves into a config dict that Rust can load. The config format is a **Python dict** (not pure JSON) passed via PyO3. It can include `PyObject` references for Python callables that Rust cannot natively implement.

**Status**: Complete. `serialize()` methods added to Ref, BaseOp, GraphOp, BranchOp, BaseIterationOp, ForOp, MapOp, WhileOp, AIterOp. 23 new serialization tests pass, 604 total tests pass (581 existing + 23 new), 0 failures.

### 2.1 — Config Format

```python
{
    "name": "workflow",
    "full_name": "workflow",
    "ops": {
        "step1": {
            "type": "code",                       # OpType
            "full_name": "workflow.step1",
            "rust_op": "rust_double",             # str | None — name in RustFuncRegistry
            "python_callable": <function>,        # PyObject | None — the op's core function
            "is_async": False,
            "executor": None,                     # None | "thread"
            "inputs": {
                "x": {
                    "ref": {                      # Ref config (or None if literal)
                        "source": "workflow",     # op full_name
                        "var": "input",
                        "ops": [],                # list of [op_name, [args...]]
                        "is_output": False
                    },
                    "literal": None,
                    "default": None,
                    "required": True
                }
            },
            "outputs": {
                "result": {
                    "ref": {                      # push ref (output mapping) or None
                        "source": "workflow",
                        "var": "result",
                        "ops": [],
                        "is_output": True
                    },
                    "default": None
                }
            }
        },
        "router": {
            "type": "branch",
            "full_name": "workflow.router",
            "python_callable": <function>,
            "cases": [
                {
                    "condition": {                # Ref serialized
                        "source": "workflow",
                        "var": "score",
                        "ops": [["ge", [90]]]
                    },
                    "target": "excellent"
                }
            ],
            "default": "fail",
            "candidates": ["excellent", "fail"]
        },
        "loop": {
            "type": "for",
            "full_name": "workflow.loop",
            "fail_fast": False,
            "each": {
                "x": {
                    "ref": {"source": "...", "var": "items", "ops": []},
                    "literal": None
                }
            },
            "broadcast": {
                "m": {"ref": None, "literal": 10}
            },
            "inner_graph": { ... }                # recursive graph config
        }
    },
    "edges": [
        {"from": "step1", "to": "router", "soft": False}
    ],
    "entries": ["step1"],
    "exits": ["step1"],
    "initial_ready_count": {"step1": 0, "router": 1},
    "has_soft_preds": ["merge_op"],
    "compiled_adj": {
        "step1": [["router", false]],
        "router": [["excellent", true], ["fail", true]]
    }
}
```

### 2.2 — `Ref.serialize()` in `hush-core/hush/core/states/ref.py`

```python
def serialize(self) -> dict:
    """Serialize Ref to dict for Rust backend."""
    return {
        "source": self.source,        # full_name string
        "var": self.var,
        "ops": self._serialize_ops(),
        "is_output": self.is_output,
    }

def _serialize_ops(self) -> list:
    """Serialize _ops list, handling nested Refs in compound booleans."""
    result = []
    for op_name, args in self._ops:
        serialized_args = []
        for arg in args:
            if isinstance(arg, Ref):
                serialized_args.append({"__ref__": arg.serialize()})
            elif callable(arg) and op_name == "apply":
                # Python callable — Rust will need to call back into Python
                serialized_args.append({"__callable__": arg})
            else:
                serialized_args.append(arg)
        result.append([op_name, serialized_args])
    return result
```

Key points:
- `_ops` is already a list of serializable tuples — just convert to nested lists
- `_fn` (compiled lambda) is NOT serialized — Rust rebuilds it from `_ops`
- Nested `Ref` objects in compound booleans (`and_`, `or_`) are recursively serialized
- `apply` ops with Python callables store the callable as a `PyObject` reference

### 2.3 — `BaseOp.serialize()` in `hush-core/hush/core/ops/base.py`

```python
def serialize(self) -> dict:
    """Serialize this op to a config dict for the Rust backend."""
    return {
        "type": self.type,
        "full_name": self.full_name,
        "name": self.name,
        "rust_op": getattr(self.core, "_rust_op_name", None),
        "python_callable": self.core,
        "is_async": asyncio.iscoroutinefunction(self.core),
        "executor": self.executor,
        "enabled": self.enabled,
        "verbose": self.verbose,
        "inputs": self._serialize_params(self.inputs),
        "outputs": self._serialize_params(self.outputs),
    }

def _serialize_params(self, params: Dict[str, Param]) -> dict:
    """Serialize a dict of Params."""
    if not params:
        return {}
    result = {}
    for name, param in params.items():
        entry = {
            "default": param.default,
            "required": param.required,
            "ref": None,
            "literal": None,
        }
        if isinstance(param.value, Ref):
            entry["ref"] = param.value.serialize()
        elif param.value is not None:
            entry["literal"] = param.value
        result[name] = entry
    return result
```

### 2.4 — `GraphOp.serialize()` in `hush-core/hush/core/ops/graph/graph_op.py`

```python
def serialize(self) -> dict:
    """Serialize full graph to config dict."""
    base = super().serialize()
    base.update({
        "ops": {name: op.serialize() for name, op in self._ops.items()},
        "edges": [
            {"from": src, "to": dst, "soft": edge.soft}
            for (src, dst), edge in self._edges.items()
        ],
        "entries": list(self.entries),
        "exits": list(self.exits),
        "initial_ready_count": dict(self.initial_ready_count),
        "has_soft_preds": list(self.has_soft_preds),
        "compiled_adj": {
            op: [[succ, soft] for succ, soft in successors]
            for op, successors in self._compiled_adj.items()
        },
    })
    return base
```

### 2.5 — `BranchOp.serialize()` in `hush-core/hush/core/ops/flow/branch_op.py`

```python
def serialize(self) -> dict:
    """Serialize branch op with conditions."""
    base = super().serialize()
    base.update({
        "cases": [
            {"condition": ref.serialize(), "target": target}
            for ref, target in self.cases
        ],
        "default": self.default,
        "candidates": self.given_candidates,
    })
    return base
```

### 2.6 — Iteration Ops `.serialize()`

**`BaseIterationOp.serialize()`** in `hush-core/hush/core/ops/iteration/base.py`:

```python
def serialize(self) -> dict:
    """Serialize iteration op with child graph."""
    base = super().serialize()  # Gets GraphOp.serialize() with inner ops/edges
    base.update({
        "each": self._serialize_each(),
        "broadcast": self._serialize_broadcast(),
    })
    return base
```

**Subclass overrides**:
- `ForOp.serialize()` → adds `fail_fast`
- `MapOp.serialize()` → adds `max_concurrency`, `fail_fast`
- `WhileOp.serialize()` → adds `until` (serialized Ref), `max_iterations`
- `AIterOp.serialize()` → adds `max_concurrency`, `callback` (PyObject ref), `batch_fn` (PyObject ref)

### 2.7 — StateSchema

`StateSchema` does NOT need serialization. Rust can recompute it from op definitions using the same algorithm as `_load_from()`:
1. Walk the op tree from config
2. Register (op, var) pairs with sequential indices
3. Resolve Refs to indices

### 2.8 — Verification

```bash
# New test file: hush-core/tests/ops/test_serialize.py
cd hush-core && uv run -m pytest tests/ops/test_serialize.py -v

# Existing tests still pass
cd hush-core && uv run -m pytest
```

**Results**:
- `cd hush-core && uv run -m pytest tests/ops/test_serialize.py -v` → 23 passed in 0.06s
- `cd hush-core && uv run -m pytest` → 604 passed, 1 skipped in 5.50s

**Test coverage** (23 tests in `tests/ops/test_serialize.py`):
- `TestRefSerialize` (4 tests): simple ref, comparison ops, compound boolean refs, op-sourced refs
- `TestBaseOpSerialize` (5 tests): FuncOp type/name/callable, input refs to parent, chained op refs, enabled/verbose, literal inputs
- `TestGraphOpSerialize` (5 tests): basic graph structure, multi-node, nested recursive, edge structure, soft_preds
- `TestBranchOpSerialize` (2 tests): basic branch with cases/default, candidates serialization
- `TestForOpSerialize` (3 tests): each/broadcast/fail_fast, literal each values
- `TestMapOpSerialize` (1 test): max_concurrency + fail_fast
- `TestWhileOpSerialize` (1 test): until + max_iterations
- `TestSerializeStructure` (2 tests): full workflow structure, idempotency

**Files modified** (Phase 2):

| File | Changes |
|------|---------|
| `hush-core/hush/core/states/ref.py` | Added `serialize()`, `_serialize_ops()` |
| `hush-core/hush/core/ops/base.py` | Added `serialize()`, `_serialize_params()` |
| `hush-core/hush/core/ops/graph/graph_op.py` | Added `serialize()` (extends BaseOp) |
| `hush-core/hush/core/ops/flow/branch_op.py` | Added `serialize()` (extends BaseOp) |
| `hush-core/hush/core/ops/iteration/base.py` | Added `serialize()` (extends GraphOp) |
| `hush-core/hush/core/ops/iteration/for_op.py` | Added `serialize()` (extends BaseIterationOp) |
| `hush-core/hush/core/ops/iteration/map_op.py` | Added `serialize()` (extends BaseIterationOp) |
| `hush-core/hush/core/ops/iteration/while_op.py` | Added `serialize()` (extends BaseIterationOp) |
| `hush-core/hush/core/ops/iteration/aiter_op.py` | Added `serialize()` (extends BaseIterationOp) |
| `hush-core/tests/ops/test_serialize.py` | **NEW**: 23 serialization tests |

---

## Phase 3: Rewrite rush-core as Standalone Engine

**Goal**: rush-core becomes a complete Rust execution engine that loads a config dict and runs everything. Replace current hybrid structs with a standalone `Rush` engine.

**Approach**: Incremental slices — build minimal working engine first, then add features.

### Phase 3a: Minimal Vertical Slice ✅ COMPLETE

Config parsing + sync leaf op execution + flat graph scheduling. Proves the Builder-Executor architecture end-to-end.

**Files created:**
- `src/config.rs` — Config parsing (`GraphConfig`, `OpConfig`, `ParamConfig`, `RefConfig`, `RefOp`, `RefArg`)
- `src/engine.rs` — `Rush` #[pyclass] with `new(config)` + `run(inputs)`, `EngineState` (flat hashmap)
- `src/refs/mod.rs` + `src/refs/interpreter.rs` — Ref ops evaluation (`getitem`, `getattr` + full set stubbed)
- `tests/test_engine.py` — 15 end-to-end tests (single op, chains, fork-join, mixed Rust/Python, output forwarding, data types, engine reuse)

**Files modified:**
- `src/lib.rs` — Added `mod config/engine/refs`, exposed `Rush`
- `python/rush_core/__init__.py` — Exported `Rush`

**What works:**
- `Rush(graph.serialize()).run({"x": 5})` → full execution → result dict
- Rust-native ops via `RustFuncRegistry`, Python callback ops, mixed chains
- Fork-join parallelism, ref resolution, output forwarding (`>> END`, `op["key"] >> PARENT["dest"]`)
- Literal inputs, defaults, engine reuse, all Python data types

**What's NOT supported yet (Phase 3b+):** Branch evaluation, iteration ops, nested GraphOps, async ops.

### Phase 3b: Branch Ops + Nested GraphOps + Soft Edges ✅ COMPLETE

Branch routing, soft edge deduplication, and nested graph execution. These three features are tightly coupled: branch patterns use soft edges, and branch targets are often nested GraphOps.

**Files modified:**
- `src/config.rs` — Added `BranchConfig`, `BranchCase` structs, `parse_branch_config()` helper, `branch_config: Option<BranchConfig>` and `inner_graph: Option<Box<GraphConfig>>` on OpConfig, `has_soft_preds: AHashSet<String>` on GraphConfig. Nested GraphOps parse recursively (a graph op's serialized dict IS a `GraphConfig`).
- `src/engine.rs` — Refactored into composable methods:
  - `run_graph()` — Reusable scheduling loop (top-level + nested)
  - `activate_successors()` — Branch-aware target filtering + soft edge dedup
  - `execute_leaf_op()` — Resolve inputs → call op → store outputs → push refs
  - `execute_nested_graph()` — Resolve inputs → run inner loop → collect outputs → push refs
  - `store_result()`, `push_output_refs()`, `collect_outputs()` — Extracted helpers
- `tests/test_engine.py` — 8 new tests (23 total):
  - `TestBranchOps` (4): simple if/else both paths, multi-condition (3 cases), branch with merge
  - `TestNestedGraphOps` (4): simple nested, chained, output mapping, multi-op inner chain

**Key design decisions:**
- `EngineState` with `(full_name, var_name)` flat keys naturally handles nesting — no `context_id` needed because nested ops have unique full_name prefixes (e.g., `"main.nested.step"`)
- Branch routing: after branch op executes (stores `{"target": "name"}` in state), `activate_successors()` filters compiled_adj to only the matching target
- Soft edges: `soft_satisfied: AHashSet<String>` tracks which targets have already been activated by a soft edge, preventing double-activation in merge patterns
- Nested graphs reuse the same `EngineState` — inner graph's scheduling loop just reads/writes to namespaced keys

**What's NOT supported yet (Phase 3c+):** Iteration ops (ForOp, MapOp, WhileOp), async ops, old code cleanup.

### Phase 3c: Iteration Ops (ForOp + WhileOp) ✅ COMPLETE

Context-based iteration support. ForOp (sequential iteration) and WhileOp (conditional loops). MapOp deferred (requires async/concurrency).

**Files modified:**
- `src/config.rs` — Added `IterationConfig` struct (each, broadcast, fail_fast, until, max_iterations), `IterParamConfig` struct, `parse_iteration_config()` + `parse_iter_params()` helpers. Extended `inner_graph` parsing from "graph"-only to "graph"/"for"/"while". Added `iteration_config: Option<IterationConfig>` on `OpConfig`.
- `src/engine.rs` — Major changes:
  - **EngineState 3-tuple**: Changed from `(full_name, var_name)` to `(full_name, var_name, context_id)`. Default context is `""` for non-iteration code. Iteration contexts: `"[0]"`, `"[1]"`, nested: `"[0].[0]"`.
  - **Context threading**: All methods now take `context: &str` — `run_graph()`, `collect_outputs()`, `activate_successors()`, `execute_leaf_op()`, `execute_nested_graph()`, `store_result()`, `push_output_refs()`, `resolve_param()`, `resolve_ref()`.
  - **`execute_for_op()`**: Resolve each/broadcast → validate equal lengths → iterate with `[i]` contexts → run inner graph per iteration → transpose results to column format → store iteration_metrics.
  - **`execute_while_op()`**: Resolve initial inputs → evaluate `until` condition via `py.eval_bound()` → loop with `[step]` contexts → merge outputs into step_inputs between iterations → store final state + iteration_metrics.
  - **New helpers**: `evaluate_until()` (safe Python expression eval with restricted builtins), `resolve_iter_param()` (resolve ref or literal for iteration params).
- `tests/test_engine.py` — 10 new tests (33 total):
  - `TestForOp` (5): literal Each, broadcast, multiple Each zip, empty list, upstream ref
  - `TestWhileOp` (5): simple counter, max_iterations safety, accumulator, Fibonacci, upstream ref

**Key design decisions:**
- **3-tuple state key**: `(String, String, String)` — simple flat hashmap with empty string as default context. No nested maps, excellent cache locality.
- **No context fallback**: Each `run_graph()` call operates entirely within one context. ForOp/WhileOp explicitly store inputs into iter_context before running inner graph.
- **`py.eval_bound()` for WhileOp until**: Mirrors Python's `eval(compiled, {"__builtins__": {}}, inputs)`. Simple and correct.
- **WhileOp output merging**: `step_inputs = {**step_inputs, **outputs}` allows loop variables to update between iterations via `step["new_counter"] >> PARENT["counter"]`.

**What's NOT supported yet:** MapOp (parallel iteration needs async), AIterOp (streaming), async ops.

### Phase 3d: Old Code Cleanup ✅ COMPLETE

Removed the old Rust hybrid code that predated the Builder-Executor architecture.

**Files deleted:**
- `src/ops/graph/graph_op.rs` (931 lines) — Old `CompiledGraph`, `RunState`. Replaced by `config.rs` + `engine.rs`.
- `src/states/state.rs` (661 lines) — Old `RustMemoryState`. Replaced by `EngineState` in engine.rs.
- `src/states/cell.rs` (48 lines) — Old `Cell` type. No longer needed.
- Legacy test files: `test_scheduler.py`, `test_branch_resolution.py`, `test_inline_execution.py`, `test_state.py`, old `test_equivalence.py`

**Files modified:**
- `src/lib.rs` — Removed `CompiledGraph`, `RunState`, `RustMemoryState` exports. Only `Rush` + `RustFuncRegistry` remain.
- `src/config.rs` — Moved `AdjEntry` struct here (was in graph_op.rs).
- `python/rush_core/__init__.py` — Removed legacy exports.
- `python/rush_core/_mode.py` — Changed availability check from `CompiledGraph` to `Rush`.
- `tests/test_mode.py` — Updated import test to use `Rush`.

**Files kept:**
- `src/ops/transform/func_op.rs` — `RustFuncRegistry` and built-in ops (actively used by engine.rs).

**Total removed:** ~1,640 lines of legacy Rust + Python test code.

### Phase 3b+ (old section): Module Structure Reference

### 3.1 — New Module Structure

```
rush-core/src/
├── lib.rs                          # PyO3 module: expose Rush + RustFuncRegistry
├── engine.rs                       # NEW: Rush engine (main entry point)
├── config.rs                       # NEW: Config parsing (Python dict → Rust structs)
├── ops/
│   ├── mod.rs
│   ├── graph/
│   │   ├── mod.rs
│   │   └── graph_op.rs             # REFACTOR: CompiledGraph from config (not from_graph)
│   ├── flow/
│   │   ├── mod.rs
│   │   └── branch_op.rs            # NEW: Branch condition evaluation in Rust
│   ├── iteration/
│   │   ├── mod.rs
│   │   ├── for_op.rs               # NEW: Sequential iteration in Rust
│   │   ├── map_op.rs               # NEW: Parallel iteration in Rust
│   │   └── while_op.rs             # NEW: Conditional loop in Rust
│   └── transform/
│       ├── mod.rs
│       └── func_op.rs              # KEEP: RustFuncRegistry + built-in ops
├── refs/
│   ├── mod.rs
│   └── interpreter.rs              # NEW: Ref operation interpreter in Rust
├── states/
│   ├── mod.rs
│   ├── cell.rs                     # KEEP as-is
│   └── state.rs                    # REFACTOR: from_config() instead of from_schema()
└── callback.rs                     # NEW: Python callback mechanism
```

### 3.2 — `engine.rs` — Rush (Main Entry Point)

```rust
#[pyclass]
pub struct Rush {
    graph: CompiledGraph,
    state_config: StateConfig,
    python_callables: AHashMap<String, PyObject>,  // op_full_name → callable
    name: String,
}

#[pymethods]
impl Rush {
    #[new]
    fn new(config: &Bound<'_, PyDict>) -> PyResult<Self> {
        // 1. Parse config dict → GraphConfig
        // 2. Build CompiledGraph from GraphConfig
        // 3. Extract Python callables for ops without Rust implementations
        // 4. Build StateConfig for creating fresh state per run
    }

    fn run<'py>(&self, py: Python<'py>, inputs: &Bound<'_, PyDict>) -> PyResult<PyObject> {
        // Sync execution for all-sync graphs
    }

    fn run_async<'py>(&self, py: Python<'py>, inputs: &Bound<'_, PyDict>) -> PyResult<PyObject> {
        // Async execution — return a Python awaitable coroutine
    }
}
```

- `new(config)`: Parses config dict → builds `CompiledGraph`, `StateConfig`, extracts Python callables.
- `run(inputs)`: Creates fresh `RustMemoryState` → runs scheduling loop → returns results dict.
- `run_async(inputs)`: Same but wraps in a Python coroutine for async op support.

### 3.3 — `config.rs` — Config Parsing

Parses the Python dict config (from Phase 2) into Rust structs:

```rust
pub struct GraphConfig {
    pub name: String,
    pub full_name: String,
    pub ops: AHashMap<String, OpConfig>,
    pub edges: Vec<EdgeConfig>,
    pub entries: Vec<String>,
    pub exits: Vec<String>,
    pub initial_ready_count: AHashMap<String, i32>,
    pub compiled_adj: AHashMap<String, SmallVec<[AdjEntry; 4]>>,
    pub has_soft_preds: AHashSet<String>,
    pub inputs: Vec<ParamConfig>,
    pub outputs: Vec<ParamConfig>,
}

pub struct OpConfig {
    pub op_type: String,
    pub full_name: String,
    pub rust_op: Option<String>,
    pub python_callable: Option<PyObject>,
    pub is_async: bool,
    pub executor: Option<String>,
    pub inputs: Vec<ParamConfig>,
    pub outputs: Vec<ParamConfig>,
    // Type-specific
    pub branch_config: Option<BranchConfig>,
    pub iteration_config: Option<IterationConfig>,
    pub inner_graph: Option<Box<GraphConfig>>,
}

pub struct ParamConfig {
    pub var_name: String,
    pub ref_config: Option<RefConfig>,
    pub literal: Option<PyObject>,
    pub default: Option<PyObject>,
    pub required: bool,
}

pub struct RefConfig {
    pub source: String,
    pub var: String,
    pub ops: Vec<RefOp>,
    pub is_output: bool,
}

pub struct RefOp {
    pub name: String,
    pub args: Vec<RefArg>,    // RefArg = Literal(PyObject) | NestedRef(RefConfig) | Callable(PyObject)
}

pub struct BranchConfig {
    pub cases: Vec<(RefConfig, String)>,  // (condition, target_name)
    pub default: Option<String>,
    pub candidates: Vec<String>,
}

pub struct IterationConfig {
    pub each: AHashMap<String, ParamConfig>,
    pub broadcast: AHashMap<String, ParamConfig>,
    pub fail_fast: bool,
    pub max_concurrency: Option<usize>,
    pub max_iterations: Option<usize>,
    pub until: Option<RefConfig>,
}
```

### 3.4 — `refs/interpreter.rs` — Ref Operation Interpreter

Evaluates `Ref._ops` chains in Rust. This replaces Python's `Ref._fn` lambda chains:

```rust
pub fn evaluate_ref_ops(
    py: Python,
    value: PyObject,
    ops: &[RefOp],
    context: &Bound<'_, PyDict>,
) -> PyResult<PyObject> {
    let mut result = value;
    for op in ops {
        result = match op.name.as_str() {
            "getitem" => result.as_ref(py).get_item(&op.args[0])?.into(),
            "getattr" => result.as_ref(py).getattr(&op.args[0].as_str()?)?.into(),
            "add" => result.as_ref(py).call_method1("__add__", (&op.args[0],))?.into(),
            "sub" => result.as_ref(py).call_method1("__sub__", (&op.args[0],))?.into(),
            "mul" => result.as_ref(py).call_method1("__mul__", (&op.args[0],))?.into(),
            "truediv" => result.as_ref(py).call_method1("__truediv__", (&op.args[0],))?.into(),
            "eq" => result.as_ref(py).rich_compare(&op.args[0], CompareOp::Eq)?.into(),
            "ne" => result.as_ref(py).rich_compare(&op.args[0], CompareOp::Ne)?.into(),
            "lt" => result.as_ref(py).rich_compare(&op.args[0], CompareOp::Lt)?.into(),
            "le" => result.as_ref(py).rich_compare(&op.args[0], CompareOp::Le)?.into(),
            "gt" => result.as_ref(py).rich_compare(&op.args[0], CompareOp::Gt)?.into(),
            "ge" => result.as_ref(py).rich_compare(&op.args[0], CompareOp::Ge)?.into(),
            "and_" => {
                // Resolve nested Ref for compound boolean
                let other = resolve_ref_arg(py, &op.args[0], state, ctx)?;
                let a = result.as_ref(py).is_truthy()?;
                let b = other.as_ref(py).is_truthy()?;
                (a && b).into_py(py)
            },
            "or_" => { /* similar */ },
            "not_" => {
                let v = result.as_ref(py).is_truthy()?;
                (!v).into_py(py)
            },
            "apply" => {
                // Call Python callable from args
                let callable = &op.args[0].as_callable()?;
                callable.call1(py, (result,))?
            },
            "neg" => result.as_ref(py).call_method0("__neg__")?.into(),
            "call" => {
                let (pos_args, kw_args) = &op.args;
                result.as_ref(py).call(pos_args, kw_args)?.into()
            },
            _ => return Err(PyValueError::new_err(format!("Unknown ref op: {}", op.name)))
        };
    }
    Ok(result)
}
```

This uses PyO3's `PyAny` operations to mirror exactly what Python's `Ref._wrap` lambdas do. All operations go through PyO3 so any Python object type is supported.

### 3.5 — `ops/graph/graph_op.rs` — CompiledGraph Refactor

**Remove**: `from_graph()` (which introspects Python objects via PyO3)

**Add**: `from_config(config: &GraphConfig)` (which loads from parsed config)

**Keep**: `activate_successors()`, `run_sync()`, `RunState`

**Add**: General `run()` method with central op dispatch:

```rust
impl CompiledGraph {
    pub fn run(
        &self,
        py: Python,
        state: &RustMemoryState,
        ctx: Option<&str>,
        parent_ctx: Option<&str>,
    ) -> PyResult<()> {
        let mut run_state = self.new_run_state();
        let mut queue: VecDeque<String> = self.entries.iter().cloned().collect();

        while let Some(op_name) = queue.pop_front() {
            self.execute_op(py, &op_name, state, ctx, parent_ctx)?;
            let (newly_ready, _) = self.activate_successors_with_state(
                &op_name, &mut run_state, state, ctx
            );
            for (name, _) in newly_ready {
                queue.push_back(name);
            }
        }
        Ok(())
    }

    fn execute_op(&self, py: Python, op_name: &str, ...) -> PyResult<()> {
        let op = &self.ops[op_name];
        match op.op_type.as_str() {
            "code" | "llm" | "embedding" | "reranker" | "chain" | "parser" => {
                self.execute_leaf_op(py, op, state, ctx, parent_ctx)
            },
            "branch" => self.execute_branch_op(py, op, state, ctx),
            "graph" => self.execute_graph_op(py, op, state, ctx, parent_ctx),
            "for" => execute_for(py, op, state, ctx),
            "map" => execute_map(py, op, state, ctx),
            "while" => execute_while(py, op, state, ctx),
            _ => self.execute_python_callback(py, op, state, ctx, parent_ctx)
        }
    }
}
```

**`execute_leaf_op`** — The core dispatch:
1. Read inputs from state (resolve Refs via interpreter)
2. Check if `rust_op` is set → call `RustFuncRegistry` (GIL released, no Python)
3. Else call `python_callable` (sync or async via callback mechanism)
4. Write outputs to state

### 3.6 — `ops/flow/branch_op.rs` — Branch Evaluation

```rust
pub fn evaluate_branch(
    py: Python,
    cases: &[(RefConfig, String)],
    default: Option<&str>,
    state: &RustMemoryState,
    ctx: Option<&str>,
) -> PyResult<String> {
    for (condition, target) in cases {
        // 1. Read source value from state
        let value = state.get_value(py, &condition.source, &condition.var, ctx)?;
        // 2. Evaluate Ref ops chain
        let result = evaluate_ref_ops(py, value, &condition.ops, ...)?;
        // 3. Check truthiness
        if result.as_ref(py).is_truthy()? {
            return Ok(target.clone());
        }
    }
    Ok(default.unwrap_or("__END__").to_string())
}
```

### 3.7 — `ops/iteration/` — Iteration Ops in Rust

**`for_op.rs`** — Sequential iteration:
```rust
pub fn execute_for(
    py: Python,
    config: &OpConfig,
    inner_graph: &CompiledGraph,
    state: &RustMemoryState,
    ctx: Option<&str>,
) -> PyResult<PyObject> {
    // 1. Resolve each/broadcast values from state
    // 2. For each item:
    //    a. Create context "ctx[i]"
    //    b. Store values in state under iter context
    //    c. Run inner_graph.run(state, iter_ctx, iter_ctx)
    //    d. Collect results
    // 3. Transpose to column format
    // 4. Return {key: [values], iteration_metrics: {...}}
}
```

**`map_op.rs`** — Parallel iteration:
- Same structure as ForOp but with concurrency via `rayon` or `tokio::spawn`
- For Python callbacks within iterations: GIL management is critical
- Semaphore-limited concurrency matching Python's `asyncio.Semaphore`

**`while_op.rs`** — Conditional loop:
- Evaluate `until` condition (a serialized Ref) each iteration
- Max iterations guard

### 3.8 — `callback.rs` — Python Callback Mechanism

For ops without Rust implementations (most hush-providers ops like LLMOp, EmbeddingOp):

```rust
pub fn call_python_op(
    py: Python,
    callable: &PyObject,
    inputs: &Bound<'_, PyDict>,
    is_async: bool,
) -> PyResult<PyObject> {
    if is_async {
        // Strategy: use pyo3-asyncio to bridge async Python from Rust
        // Alternative: call through a helper that runs the coroutine on the existing event loop
        pyo3_asyncio::tokio::into_future(py, callable.call(py, (), Some(inputs))?)
    } else {
        callable.call(py, (), Some(inputs))
    }
}
```

**Async strategy for V1**: Use `pyo3-asyncio` crate with tokio runtime. This handles the tricky part of scheduling Python coroutines from Rust.

Add to `Cargo.toml`:
```toml
[dependencies]
pyo3-asyncio = { version = "0.22", features = ["tokio-runtime"] }
tokio = { version = "1", features = ["rt-multi-thread"] }
```

### 3.9 — `states/state.rs` — RustMemoryState Refactor

**Remove**: `from_schema(py_schema: &PyAny)` (introspects Python `StateSchema`)

**Add**: `from_config(config: &StateConfig, inputs: &PyDict)`:
1. Walk op configs to build `var_to_idx` (same algorithm as Python `StateSchema._load_from`)
2. Build cells with defaults
3. Build pull_refs and push_refs from `RefConfig`
4. Apply initial inputs

**Keep**: All existing cell access methods — `get_value()`, `set_value()`, `get_string()`, `record_execution_internal()`

### 3.10 — `lib.rs` — Updated PyO3 Module

```rust
#[pymodule]
fn _native(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<Rush>()?;           // NEW: main engine
    m.add_class::<RustFuncRegistry>()?;   // KEEP: op registry
    // CompiledGraph and RustMemoryState become internal (not exposed to Python)
    Ok(())
}
```

### 3.11 — Updated `Cargo.toml`

```toml
[dependencies]
pyo3 = { version = "0.22", features = ["extension-module"] }
ahash = "0.8"
smallvec = "1"
pyo3-asyncio = { version = "0.22", features = ["tokio-runtime"] }
tokio = { version = "1", features = ["rt-multi-thread"] }
```

### 3.12 — Updated Python Wrapper

`rush-core/python/rush_core/__init__.py`:

```python
from rush_core._native import Rush, RustFuncRegistry

# Keep @rust_op decorator for registering Rust-native op implementations
def rust_op(name: str):
    """Link a Python @op to its Rust implementation."""
    def decorator(func):
        func._rust_op_name = name
        if hasattr(func, "__wrapped__"):
            func.__wrapped__._rust_op_name = name
        return func
    return decorator

__all__ = ["Rush", "RustFuncRegistry", "rust_op"]
```

### 3.13 — Verification

```bash
cd rush-core && cargo test
cd rush-core && uv run maturin develop --release
cd rush-core && uv run -m pytest tests/ -v
```

---

## Phase 4: Wire `Hush(graph, mode="rust")` to Rush ✅ COMPLETE

**Goal**: When `mode="rust"`, `Hush` serializes the graph and delegates entirely to `Rush`.

**Status**: Complete. `Hush(graph, mode="rust")` creates a `Rush` instance from the serialized graph config and dispatches `run()` to it. Falls back to Python if rush-core is not installed. Verified: both modes produce identical results for flat graphs, branches, nested graphs, ForOp, and WhileOp.

### 4.1 — `hush-core/hush/core/engine.py`

```python
class Hush:
    __slots__ = ["graph", "name", "_schema", "_mode", "_rust_engine"]

    def __init__(self, graph: GraphOp, mode: str = "python"):
        if mode not in ("python", "rust"):
            raise ValueError(f"Invalid mode: {mode!r}. Must be 'python' or 'rust'.")

        self._mode = mode
        self._rust_engine = None
        self.graph = graph
        self.name = graph.name
        self.graph.build()

        if mode == "rust":
            self._init_rust_engine()

        # Always build Python schema (needed for Python fallback + tracing)
        self._schema = StateSchema(self.graph)

    def _init_rust_engine(self):
        """Initialize Rust backend from serialized graph config."""
        try:
            from rush_core import Rush
            config = self.graph.serialize()
            self._rust_engine = Rush(config)
        except ImportError:
            LOGGER.warning(
                "rush-core not installed. Falling back to Python mode for %s",
                self.name,
            )
            self._mode = "python"
        except Exception as e:
            LOGGER.warning(
                "Failed to initialize Rush for %s: %s. Falling back to Python.",
                self.name, e,
            )
            self._mode = "python"

    async def run(self, inputs, *, user_id=None, session_id=None,
                  request_id=None, tracer=None):
        # ... ID generation, tracer normalization ...

        if self._mode == "rust" and self._rust_engine is not None:
            result = await self._rust_engine.run_async(inputs)
        else:
            state = self._schema.create_state(
                inputs=inputs, user_id=user_id,
                session_id=session_id, request_id=request_id,
            )
            result = await self.graph.run(state)
            result["$state"] = state

        # ... stream cleanup, tracing ...
        return result
```

**Key difference from the old design**: The `_run_rust` path does NOT touch any Python ops. It calls `Rush.run_async()` which handles **everything** in Rust. Python ops' `run()` methods are never called in Rust mode.

### 4.2 — Verification

```bash
# Integration test: same workflows in both modes
cd rush-core && uv run -m pytest tests/test_equivalence.py -v

# Full test suite
cd hush-core && uv run -m pytest
cd rush-core && uv run -m pytest

# Tutorial examples in both modes
cd hush-tutorial && uv run python examples/16_graph.py
```

---

## Phase 5: Testing Strategy ✅ COMPLETE

### 5.1 — hush-core Tests (Phase 1 Verification)

604 passed, 1 skipped. All existing tests pass unchanged after removing Rust hybrid code.

### 5.2 — Serialization Tests (Phase 2)

23 tests in `hush-core/tests/ops/test_serialize.py`. All pass.

### 5.3 — rush-core Engine Tests (Phase 3)

`rush-core/tests/test_engine.py` — 33 tests covering all op types via `Rush(config).run()`:
- TestSingleOp (3): rust op, python callback, literal input
- TestLinearChain (3): two ops, three ops, mixed rust/python
- TestOutputForwarding (2): auto-forward via END, explicit mapping
- TestParallelOps (1): fork-join
- TestEngineReuse (2): run twice, different inputs
- TestDataTypes (4): float, string, list, dict passthrough
- TestBranchOps (4): simple if/else both paths, multi-condition, branch with merge
- TestNestedGraphOps (4): simple nested, chained, output mapping, multi-op inner chain
- TestForOp (5): literal Each, broadcast, multiple Each zip, empty list, upstream ref
- TestWhileOp (5): simple counter, max_iterations safety, accumulator, Fibonacci, upstream ref

### 5.4 — Equivalence Tests (Phase 4+5) ✅ COMPLETE

`rush-core/tests/test_equivalence.py` — 23 tests. Each test runs the same workflow through `Hush(graph, mode="python")` and `Hush(graph, mode="rust")`, asserting identical outputs.

| Test Class | Tests | What it verifies |
|------------|-------|-----------------|
| TestLinearChain | 4 | Single op, 2-chain, 3-chain, string data |
| TestForkJoin | 1 | Diamond graph (A → B,C → D) |
| TestBranch | 3 | True path, false path, multi-condition (Branch fluent API) |
| TestNestedGraph | 3 | Simple nested, chained, output mapping (@graph decorator) |
| TestForOp | 5 | Literal Each, broadcast, zip, empty, upstream ref |
| TestWhileOp | 4 | Counter, max_iterations, accumulator, upstream ref |
| TestGraphDecorator | 2 | @graph factory, chained @graph |
| TestEngineReuse | 1 | Same engine, multiple runs, both modes |

### 5.5 — Additional Tests

- `test_builtin_ops.py` — 34 tests for RustFuncRegistry built-in ops (string, JSON, math)
- `test_rust_op.py` — 15 tests for @rust_op decorator and workflow integration
- `test_mode.py` — 5 tests for ExecutionMode enum and availability check

### 5.5 — Verification Commands

```bash
# Phase 1: hush-core cleanup
cd hush-core && uv run -m pytest

# Phase 2: serialization
cd hush-core && uv run -m pytest tests/test_serialize.py -v

# Phase 3: rush-core engine
cd rush-core && cargo test
cd rush-core && uv run maturin develop --release
cd rush-core && uv run -m pytest tests/ -v

# Phase 4: integration + equivalence
cd rush-core && uv run -m pytest tests/test_equivalence.py -v

# Full regression
cd hush-core && uv run -m pytest && cd ../rush-core && uv run -m pytest

# Tutorial end-to-end
cd hush-tutorial && uv run python examples/16_graph.py
cd hush-tutorial && uv run python examples/05_flow.py
cd hush-tutorial && uv run python examples/13_parallel.py
```

---

## Implementation Order & Risk Assessment

| Step | Package | What | Risk | Lines |
|------|---------|------|------|-------|
| 1.1-1.4 | hush-core | Remove Rust hybrid code | Low (pure removal) | -300 |
| 2.1-2.7 | hush-core | Add `serialize()` methods | Low (new code, no changes to existing) | +200 |
| 3.1-3.2 | rush-core | Rush engine skeleton + config parsing | Medium | +500 |
| 3.3-3.4 | rush-core | Ref interpreter + state refactor | Medium | +400 |
| 3.5 | rush-core | CompiledGraph refactor (`from_config`) | Medium | +300 |
| 3.6 | rush-core | Branch evaluation in Rust | Low | +100 |
| 3.7 | rush-core | Iteration ops in Rust | **High** (complex) | +600 |
| 3.8 | rush-core | Python callback + async bridge | **High** (tricky) | +200 |
| 4.1 | hush-core | Wire Hush engine to Rush | Low | +50 |
| 5.x | both | Testing | Medium | +500 |

---

## Critical Files Summary

### hush-core (modify)

| File | Changes |
|------|---------|
| `hush/core/ops/base.py` | Remove `_try_rust_execute`, simplify `run()`, add `serialize()` |
| `hush/core/ops/graph/graph_op.py` | Remove Rust hybrid, rename scheduler, add `serialize()` |
| `hush/core/ops/iteration/base.py` | Remove `_run_graph_rust`, rename, add `serialize()` |
| `hush/core/ops/flow/branch_op.py` | Add `serialize()` |
| `hush/core/ops/transform/func_op.py` | (inherits BaseOp.serialize, no changes needed) |
| `hush/core/ops/iteration/for_op.py` | Add `serialize()` override |
| `hush/core/ops/iteration/map_op.py` | Add `serialize()` override |
| `hush/core/ops/iteration/while_op.py` | Add `serialize()` override |
| `hush/core/ops/iteration/aiter_op.py` | Add `serialize()` override |
| `hush/core/states/ref.py` | Add `serialize()` |
| `hush/core/engine.py` | Delegate to Rush when mode="rust" |

### rush-core (rewrite)

| File | Status |
|------|--------|
| `src/lib.rs` | REFACTOR: new PyO3 exports |
| `src/engine.rs` | **NEW**: Rush engine |
| `src/config.rs` | **NEW**: Config parsing |
| `src/refs/interpreter.rs` | **NEW**: Ref evaluation |
| `src/callback.rs` | **NEW**: Python callback |
| `src/ops/graph/graph_op.rs` | REFACTOR: `from_config` instead of `from_graph` |
| `src/ops/flow/branch_op.rs` | **NEW**: Branch in Rust |
| `src/ops/iteration/for_op.rs` | **NEW**: ForOp in Rust |
| `src/ops/iteration/map_op.rs` | **NEW**: MapOp in Rust |
| `src/ops/iteration/while_op.rs` | **NEW**: WhileOp in Rust |
| `src/ops/transform/func_op.rs` | KEEP: RustFuncRegistry + built-in ops |
| `src/states/state.rs` | REFACTOR: `from_config` instead of `from_schema` |
| `src/states/cell.rs` | KEEP as-is |
