---
paths: ["python/hush-icore/**"]
---

# hush-icore (Python)

Core workflow engine: ops, state management, tracing, execution engine.

## Module Structure

```
hush/core/
├── engine.py           # Hush engine — compiles and runs workflows
├── tracing/            # TraceCollector, FlushWorker, TraceNode, Tracer base
│   ├── base.py         # Tracer base class (tags, stream_trace_limit, flush)
│   ├── collector.py    # collect_tree() builds pre-computed TraceNode tree
│   ├── flush_worker.py # ThreadPoolExecutor, tag merging, stream sampling
│   ├── local.py        # LocalTracer — zero-dep JSON file tracer
│   └── models.py       # TraceNode, TraceSummary dataclasses
├── exceptions.py       # OpError hierarchy (ParserError, CodeError, BranchError, etc.)
├── ops/
│   ├── base.py         # BaseOp class, >> operator, input/output handling
│   ├── transform/      # FuncOp, ParserOp
│   ├── flow/           # BranchOp
│   └── graph/          # GraphOp, Scheduler, Loop, Decorators
├── states/
│   ├── schema.py       # StateSchema — compile-time Ref resolution + O(1) index
│   ├── state.py        # MemoryState — runtime state[op, var, ctx]
│   └── ref.py          # Ref system: op["key"], PARENT["key"], apply(), >> operator
├── configs/            # OpConfig, EdgeConfig
├── registry/           # ResourceHub, plugin registration (REGISTRY.register)
├── loggings/           # Rich logging, shared log_templates.json
└── utils/
    ├── algo.py         # Graph algorithms (topo_sort, find_cycles, reachable)
    └── auto_name.py    # Bytecode → AST → UUID auto-naming
```

## Op Lifecycle

1. **Definition**: Created inside `with GraphOp(...) as graph:` context
2. **Registration**: Auto-registered to parent graph via `get_current()`
3. **Compilation**: `StateSchema` resolves all Refs, builds O(1) index
4. **Execution**: `op.run(state, context_id)` dispatches based on sync/async + executor

## Creating a New Op

1. Create file in `ops/{transform,flow,graph}/`
2. Inherit from `BaseOp`, set `type: OpType = "my_type"`, assign `self.core`
3. Export in `ops/__init__.py`

## Executor

Sync `op.core` runs on event loop by default. For blocking ops: `@op(executor="thread")`.
Async ops always run on event loop regardless.

## State System

- **StateSchema**: Compile-time — resolves Ref chains, validates connections
- **MemoryState**: Runtime — `state[op_name, var_name, context_id]`
- **Ref**: `op["key"]` returns Ref, `PARENT["key"]` for external inputs
- **Cell**: Isolated contexts for iteration (each loop iter gets own context_id)

## Tracing

Ops don't know about tracing. After `engine.run()`:
1. `FlushWorker.submit()` returns immediately
2. Background thread: `TraceCollector.collect_tree()` → `TraceNode` list
3. `_sample_stream_nodes()` caps stream items per generator
4. `tracer.flush(trace_data)` sends to backend

### TraceNode kinds
- `batch` (normal), `generator`, `stream_context` (synthetic `[N]`), `stream_item`, `loop_iter`, `graph`

### Tags
- Static: `Tracer(tags=["prod"])` — at construction
- Dynamic: `return {"result": x, "$tags": ["cache-hit"]}` — from op outputs

## Gotchas

1. Op names: only alphanumeric, underscore, hyphen
2. Same key cannot be in both inputs and outputs
3. Soft edges (`>>~`) for branch outputs to avoid deadlocks
4. PARENT resolves at build time, not definition time
5. Sync ops on event loop by default — use `executor="thread"` for blocking ops
