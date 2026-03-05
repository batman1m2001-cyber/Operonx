# Streaming Architecture — Implementation Status

## Overview

This document tracks the implementation progress across the 3-phase streaming plan.
Last updated: 2026-03-06 (after major codebase refactor).

## Phase 1 — Streaming Scheduler ✅ COMPLETE

All Phase 1 changes are implemented and passing (609 tests, 0 failures).

### Change 0: Tuple Context Migration ✅

- `DEFAULT_CONTEXT = ("main",)` in `states/cell.py`
- `_unpack_key` updated in `states/state.py` for tuple normalization
- `get_iter_context()` returns tuples in `ops/iteration/base.py`
- ForOp/WhileOp/MapOp/AIterOp use tuple contexts
- `identity()` in `BaseOp` formats tuples for display
- `tracing/collector.py` adapted for tuple context display
- All existing tests updated and passing

### Change 1: ast.Yield in extract_return_schema ✅

- `ops/transform/func_op.py` handles `ast.Yield` alongside `ast.Return`
- Generator ops get correct output schema detection

### Change 2: Stream Depth Computation ✅

- `GraphOp.build()` computes `_stream_depths` via topological traversal
- `_has_streaming_ops` flag computed
- `_stream_predecrements` computed per generator for O(1) context creation
- `_max_stream_concurrent = 64` (backpressure cap)
- Stream depths stored on child ops for `get_inputs()` access

### Change 3: Unified Event-Queue Scheduler ✅

- Extracted to `ops/graph/scheduler.py` as `Scheduler` class
- Event types: `done`, `yield`, `exhausted`
- Per-context `ready_counts` with pre-decremented streaming contexts
- Generator driving via `_drive_generator()` with metrics/logging
- Backpressure via `asyncio.Semaphore`
- Result collection: aggregates streaming outputs into lists
- Inline optimization for sync non-generator ops

### New streaming tests ✅

- `tests/ops/test_streaming.py` — comprehensive streaming test suite

---

## Phase 2 — Iteration Op Removal ✅ COMPLETE

### Iteration ops deleted ✅

- `ops/iteration/__init__.py` — deleted
- `ops/iteration/base.py` — deleted (BaseIterationOp, Each, Broadcast, get_iter_context)
- `ops/iteration/for_op.py` — deleted
- `ops/iteration/map_op.py` — deleted
- `ops/iteration/aiter_op.py` — deleted
- `ops/iteration/while_op.py` — deleted

### Tests migrated ✅

- `test_for_op.py` — rewritten using generator ops + GraphOp
- `test_map_op.py` — rewritten using generator ops + GraphOp
- `test_aiter_op.py` — rewritten using async generator ops + GraphOp
- `test_while_op.py` — rewritten using `GraphOp.loop()`

---

## Phase 3 — GraphOp.loop() ✅ COMPLETE

### GraphOp.loop() classmethod ✅

- `GraphOp.loop(name, until, max_iterations, **initial_state)` creates looping graphs
- Feedback loop via `_run_loop()` method in `graph_op.py`
- Supports string expressions and callable conditions
- `max_iterations` safety limit (default 100)

### @graph.loop() decorator ✅

- `ops/graph/_decorators.py` — `@graph.loop()` decorator for modular loop definitions

### Loop tests ✅

- `tests/ops/graph/test_graph_loop.py` — loop test suite

---

## Codebase Refactor (Beyond Phase Plan)

In addition to the 3-phase streaming work, a major structural refactor was performed:

### File Splits — Large Files Decomposed

| Before | After | What was extracted |
|--------|-------|--------------------|
| `base.py` (1100+ lines) | `base.py` (644 lines) | `_edges.py` (144), `_params.py` (169), `_shortcuts.py` (92) |
| `graph_op.py` (900+ lines) | `graph_op.py` (617 lines) | `scheduler.py` (269), `validation.py` (316), `_algo.py` (104), `_decorators.py` (93) |

### Function Splits — Long Methods Decomposed

| Original | Extracted helpers |
|----------|-------------------|
| `GraphOp.build()` | `_build_ready_counts()`, `_build_adj()`, `_build_streaming()` |
| `GraphOp.run()` | `_run_loop()` (feedback loop logic) |
| `BaseOp.get_inputs()` | `_resolve_ctx()` (context resolution) |
| `BaseOp.run()` | `_exec_core()` (dispatch logic) |

### Graph Algorithm Library — `ops/graph/_algo.py`

Extracted duplicated graph walk algorithms into reusable module:

| Algorithm | Was duplicated in | Now in `_algo.py` |
|-----------|-------------------|---------------------|
| Kahn's topo sort | `graph_op.py` `_build_streaming()` | `topo_sort()` |
| DFS cycle detection | `validation.py` `_validate_cycles()` | `find_cycles()` |
| Forward/backward DFS reachability | `validation.py` `_validate_reachability()` | `reachable()` |

### Scheduler Class — `ops/graph/scheduler.py`

Converted from 230-line function with 7 nested closures into a proper class:

- **Init**: Takes graph topology only (`__init__(graph)`)
- **Run**: Takes runtime params (`run(state, context_id, parent_context, request_id)`)
- **Reusable**: Created once in `GraphOp.build()`, called for every `run()` and `_run_loop()`
- **10 focused methods**: `_reset`, `_can_inline`, `_get_successors`, `_activate_successors`, `_create_stream_context`, `_run_op`, `_drive_generator`, `_schedule_op`, `_collect_outputs`, `_drain_ready`, `run`

### Validation Module — `ops/graph/validation.py`

Extracted from `graph_op.py`:

- `validate_graph()` — entry point
- `_validate_branch_targets()` — branch ops reference existing targets
- `_validate_cycles()` — DFS cycle detection (uses `_algo.find_cycles`)
- `_validate_reachability()` — orphans, unreachable, dead-ends (uses `_algo.reachable`)
- `_validate_refs()` — Ref references point to existing ops
- `ValidationIssue`, `ValidationResult`, `GraphValidationError` — structured error types

---

## Final File Layout

```
hush-core/hush/core/ops/
├── __init__.py
├── base.py              (644 lines — core BaseOp, END sentinel)
├── _edges.py            (144 lines — Edge, SoftEdge, edge operators)
├── _params.py           (169 lines — Param, InputParam, OutputParam)
├── _shortcuts.py        (92 lines — START/END shortcuts, @graph decorator)
├── transform/
│   └── func_op.py       (@op decorator, FuncOp)
├── branch/
│   └── branch_op.py     (BranchOp, if_/switch)
└── graph/
    ├── __init__.py
    ├── graph_op.py      (617 lines — GraphOp container, build, run, loop)
    ├── scheduler.py     (269 lines — Scheduler class, event-queue)
    ├── validation.py    (316 lines — graph validation, error types)
    ├── _algo.py         (104 lines — topo_sort, find_cycles, reachable)
    └── _decorators.py   (93 lines — @graph, @graph.loop decorators)
```

The `ops/iteration/` module is fully deleted. All iteration patterns are now handled by:
- **Streaming/fan-out**: generator `@op` inside `GraphOp` (scheduler drives per-yield)
- **Feedback loops**: `GraphOp.loop(until="...")` or `@graph.loop()`

---

## Verification

```bash
cd hush-core && uv run -m pytest tests/ -x   # 609 passed, 1 skipped
cd hush-core && uv run ruff check .           # clean
cd hush-core && uv run ruff format --check .  # clean
```

## Net Impact

- **Deleted**: ~2,800 lines (iteration module + duplicated code)
- **Added**: ~1,200 lines (streaming, scheduler, algo, validation, decorators)
- **Net reduction**: ~1,600 lines
- **Test count**: 609 (all passing)
