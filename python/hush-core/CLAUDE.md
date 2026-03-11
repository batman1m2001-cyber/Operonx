# hush-core

Core workflow engine providing ops, state management, tracing, and the execution engine.

## Module Structure

```
hush/core/
├── engine.py           # Hush engine - compiles and runs workflows
├── tracing/            # Tracing system (Tracer, TraceCollector, FlushWorker, TraceNode)
│   ├── __init__.py     # Exports: Tracer, TraceCollector, FlushWorker, LocalTracer, TraceNode
│   ├── base.py         # Tracer base class (tags, stream_trace_limit, flush interface)
│   ├── collector.py    # TraceCollector — collect_tree() builds pre-computed TraceNode tree
│   ├── flush_worker.py # FlushWorker — ThreadPoolExecutor, tag merging, stream sampling
│   ├── local.py        # LocalTracer — zero-dep JSON file tracer
│   └── models.py       # TraceNode, TraceSummary dataclasses
├── exceptions.py       # Unified exception hierarchy (OpError, etc.)
├── ops/                # Op types (BaseOp, FuncOp, GraphOp, etc.)
├── states/             # State management (StateSchema, MemoryState, Cell, Ref)
├── configs/            # Configuration classes (OpConfig, EdgeConfig)
├── registry/           # Resource management (ResourceHub, plugins)
├── loggings/           # Logging configuration with Rich
└── utils/              # Utilities (context vars, common helpers, shared algorithms)
    └── algo.py         # Graph algorithms (topo_sort, topo_rank, find_cycles, reachable)
                        # Tree algorithms (build_children, tree_walk, prune_leaves, collapse)
```

## Key Files to Read First

1. `ops/base.py` - BaseOp class, `>>` operator, input/output handling
2. `ops/graph/graph_op.py` - GraphOp for nested workflows
3. `states/schema.py` - StateSchema for compile-time state validation
4. `states/state.py` - MemoryState for runtime state access
5. `engine.py` - Hush engine execution flow

## Op System

### Creating a New Op Type

1. Create file in appropriate subdirectory under `ops/`:
   - `transform/` - Data transformation ops
   - `flow/` - Control flow ops (branch)
   - `graph/` - Container ops (GraphOp, loops via `GraphOp.loop()` / `@graph.loop()`)

2. Inherit from `BaseOp`:
```python
from hush.core.ops.base import BaseOp
from hush.core.configs.op_config import OpType

class MyOp(BaseOp):
    type: OpType = "my_type"  # Literal type for identification

    def __init__(self, name: str, my_param: str, **kwargs):
        super().__init__(name=name, **kwargs)
        self.my_param = my_param
        # Set self.core to the execution function
        self.core = self._execute

    def _execute(self, **inputs) -> dict:
        # Process inputs and return outputs dict
        return {"result": ...}
```

3. Export in `ops/__init__.py`

### Executor (Thread Pool for Sync Ops)

By default, sync `op.core` runs directly on the event loop (no overhead). For blocking I/O or CPU-bound sync ops, use `executor="thread"` to run in a `ThreadPoolExecutor`:

```python
@op(executor="thread")
def heavy_io(url: str):
    """Runs in a thread pool — won't block the event loop."""
    import requests
    resp = requests.get(url)
    return {"body": resp.text}

# Or override at call time:
step = my_sync_op(x=PARENT["x"], executor="thread")
```

Valid values: `None` (default, event loop), `"thread"` (ThreadPoolExecutor).
Async ops always run on the event loop regardless of `executor` setting.

### Op Lifecycle

1. **Definition**: Op created inside `with GraphOp(...) as graph:` context
2. **Registration**: Auto-registered to parent graph via `get_current()`
3. **Compilation**: `StateSchema` resolves all Refs and builds index
4. **Execution**: Engine calls `op.run(state, context_id)` → dispatches based on sync/async and `executor`

### Edge Operators

```python
# Hard edge (counts toward ready_count)
a >> b >> c

# Soft edge (for branch outputs - doesn't count toward ready_count)
branch >> ~case_a >> merge
branch >> ~case_b >> merge

# Fork to multiple
START >> [a, b, c]

# Join from multiple
[a, b, c] >> merge
```

## State Management

### StateSchema

Compile-time structure that:
- Resolves all `Ref` chains to canonical storage locations
- Builds O(1) lookup index for state access
- Validates input/output connections

### MemoryState

Runtime state storage:
```python
# Access pattern: state[op_name, var_name, context_id]
value = state["workflow.step1", "output", "ctx_0"]
state["workflow.step1", "result", "ctx_0"] = value
```

### Ref System

```python
from hush.core.states.ref import Ref

# Reference another op's output
ref = op["output_var"]  # Returns Ref(source=op, key="output_var")

# Reference with operations
ref = PARENT["items"].apply(len)  # Apply function to value

# PARENT marker resolves to parent GraphOp at build time

# Output mapping via >> operator (Ref.__rshift__)
op["src_key"] >> PARENT["dest_key"]  # Map op output to graph output
# Equivalent to: outputs={"src_key": PARENT["dest_key"]}

# Common in loops — update loop state or forward results
process["new_messages"] >> PARENT["messages"]
loop["final_answer"] >> PARENT["answer"]
```

### Cell System

Cells provide isolated contexts for iteration ops:
- Each loop iteration gets its own `context_id`
- Child ops access parent context via `parent_context` parameter

## Registry System

### ResourceHub

Central registry for configurations loaded from YAML/JSON:
```python
from hush.core.registry import get_hub, set_global_hub

hub = get_hub()
config = hub.get("llm", "gpt-4o")  # Get LLM config by key
```

**Error handling**: `get()` and `llm()` wrap factory/init failures in `KeyError` with descriptive messages. A failing resource (e.g., unreachable Keycloak) won't crash the entire hub — callers get a clear `KeyError` they can catch or let propagate.

### Plugin Pattern

Plugins register handlers for resource types:
```python
from hush.core.registry import REGISTRY

@REGISTRY.register("llm")
def llm_plugin(config: dict) -> LLMConfig:
    return LLMConfig(**config)
```

## Tracing System

### Overview

Ops do **not** know about tracing. After `engine.run()` completes, `TraceCollector` builds
a pre-computed tree of `TraceNode` objects, then `FlushWorker` sends it to tracers in background threads.

```
Op.run() → stores I/O, timing, cost to state (no tracing awareness)
engine.run() completes
  → FlushWorker.submit(tracers, graph, state)     ← returns immediately
    → ThreadPoolExecutor thread:
      → TraceCollector.collect_tree(graph, state)   ← builds TraceNode tree
      → _sample_stream_nodes(nodes, limit)          ← caps stream items per generator
      → tracer.flush(trace_data)                    ← I/O-bound (HTTP, SDK calls)
```

### TraceNode (Pre-computed Tree)

`collect_tree()` returns a flat list of `TraceNode` dicts with pre-computed `parent_trace_key`.
Tracers do a simple parent lookup — no heuristics needed.

Key fields:
- `trace_key`: Unique identifier (e.g. `"root.step:main"`, `"$ctx:root:main.s0"`)
- `parent_trace_key`: Parent's trace_key (None for root)
- `display_name`: Short UI label (e.g. `"step"`, `"[0]"`, `"[iter 0]"`)
- `node_type`: `"trace"` (root) | `"span"` (most things) | `"generation"` (LLM ops)
- `kind`: `"batch"` | `"generator"` | `"stream_context"` | `"stream_item"` | `"loop_iter"` | `"graph"`

Synthetic nodes (prefixed with `$ctx:`) group stream yields or loop iterations:
```
workflow (trace)
├── chunker (span, kind=generator, yields=5)
├── [0] (span, kind=stream_context)      ← synthetic grouping
│   └── analyze (span, kind=stream_item)
├── [1] (span, kind=stream_context)
│   └── analyze (span, kind=stream_item)
└── ...
```

### Tracer Base Class

```python
from hush.core.tracing import Tracer

class MyTracer(Tracer):
    def __init__(self, endpoint: str, tags=None, stream_trace_limit=100):
        super().__init__(tags=tags, stream_trace_limit=stream_trace_limit)
        self._endpoint = endpoint

    def flush(self, trace_data: dict) -> None:
        """Called by FlushWorker in a background thread."""
        # trace_data has: nodes (TraceNode list), tags, request_id, etc.
        import requests
        requests.post(self._endpoint, json=trace_data)
```

### Engine API

```python
from hush.core.tracing import LocalTracer

engine = Hush(graph)
result = await engine.run(
    inputs={"x": 5},
    tracer=LocalTracer(tags=["dev"]),  # single tracer or list
)
# For external tracers (HushEyesTracer, Langfuse, OTEL), see hush-telemetry
```

### Key Components

- **`Tracer`** (`tracing/base.py`): Base class — `__init__(tags, stream_trace_limit)` + `flush(trace_data)`
- **`TraceNode`** (`tracing/models.py`): Dataclass for pre-computed trace tree nodes
- **`TraceCollector`** (`tracing/collector.py`): `collect_tree()` builds TraceNode list with synthetic grouping nodes
- **`FlushWorker`** (`tracing/flush_worker.py`): `ThreadPoolExecutor(4)`, merges tags, samples stream nodes, calls `flush()`
- **`LocalTracer`** (`tracing/local.py`): Zero-dep JSON file tracer (writes to `~/.hush/traces/`)
- **`HushEyesTracer`**: Moved to `hush-telemetry` package (`hush.telemetry.tracers.hush_eyes`)

### Stream Sampling

`stream_trace_limit` (default 100) caps stream_item nodes per generator before flushing.
Orphaned stream_context nodes are also removed. Set to `None` to keep all.

### Tags

Two sources, merged at flush time:
- **Static**: `Tracer(tags=["prod"])` — set at construction
- **Dynamic**: `return {"result": x, "$tags": ["cache-hit"]}` — from op outputs via `state._tags`

## Testing Patterns

```python
import pytest
from hush.core import Hush, GraphOp, FuncOp, START, END, PARENT

@pytest.mark.asyncio
async def test_workflow():
    with GraphOp(name="test") as graph:
        step = FuncOp(
            name="step",
            code_fn=lambda x: {"y": x + 1},
            inputs={"x": PARENT["input"]},
            outputs={"y": PARENT["output"]}
        )
        START >> step >> END

    engine = Hush(graph)
    result = await engine.run(inputs={"input": 1})
    assert result["output"] == 2
```

## Common Patterns

### @graph — Reusable GraphOp Factory

Turn a builder function into a reusable, auto-named GraphOp:

```python
from hush.core import graph, op, START, END, PARENT, GraphOp, Hush

@op
def double(x: int):
    return {"result": x * 2}

@graph
def double_flow(val):
    step = double(x=val)        # val is injected as PARENT["val"]
    START >> step >> END

# Use in a parent graph
with GraphOp(name="main") as main:
    d1 = double_flow(val=PARENT["input"])       # d1.name == "d1"
    d2 = double_flow(val=d1["result"])           # reuse the same graph
    START >> d1 >> d2 >> END

result = await Hush(main).run(inputs={"input": 3})
# result["result"] == 12  (3 * 2 * 2)
```

Key points:
- Function params → `PARENT` refs (injected automatically)
- Supports `name=`, `outputs=`, `description=` kwargs via `split_shorthand_kwargs`
- `>> END` auto-forwarding works (decorator calls `_setup_schema()` after build)
- `register_skip(wrapper)` enables auto-naming through the decorator

### Auto-Naming

Ops automatically infer their name from the assignment variable:

```python
llm = LLMOp.of(resource="gpt-4o", messages=msgs)
# llm.name == "llm" — extracted via bytecode analysis

router = if_(PARENT["x"] > 0, "pos").else_("neg")
# router.name == "router"
```

How it works: `auto_name()` in `utils/auto_name.py` walks the call stack (skipping `__init__`, `register_skip`'d frames), then:
1. **Bytecode analysis** (primary): finds `STORE_FAST`/`STORE_NAME` after the call site
2. **AST source parsing** (fallback): parses `var = expr` from source lines
3. **UUID fallback**: 8-char hex if both fail

Use `register_skip(fn)` to skip your factory function's frame during auto-naming.

### Iteration Patterns (ForOp/MapOp/WhileOp removed)

The old `ForOp`, `MapOp`, `Each` classes were replaced. ForOp/MapOp were GraphOp-like containers
with child ops inside — now a regular `GraphOp` + generator op replaces them entirely.

**1. Generator ops inside GraphOp (replaces ForOp/MapOp)**

A generator `@op` with `yield` acts as the iterator source. Downstream ops in the same GraphOp
process each yielded item. The GraphOp serves as the container (the role ForOp/MapOp used to fill):

```python
@op
def each_item(items: list):
    for item in items:
        yield {"value": item}

@op
def double(value: int):
    return {"result": value * 2}

# GraphOp = container, generator op = iterator, downstream ops = per-item processing
with GraphOp(name="iterate") as graph:
    gen = each_item(items=PARENT["numbers"])
    step = double(value=gen["value"])
    START >> gen >> step >> END

result = await Hush(graph).run(inputs={"numbers": [1, 2, 3]})
# result["result"] == [2, 4, 6]
# Downstream ops run concurrently per yield (streaming scheduler default)
```

Broadcast (batch op value shared across all yields):
```python
with GraphOp(name="multiply_loop") as g:
    cfg = get_config()                                    # batch op → {"multiplier": 10}
    src = each_item(items=PARENT["items"])                # generator
    m = multiply(value=src["value"], multiplier=cfg["multiplier"])  # per-item
    START >> [cfg, src]
    [cfg, src] >> m >> END
```

**2. `GraphOp.loop()` / `@graph.loop()` (replaces WhileOp)** — feedback loops:

```python
# Class method style
with GraphOp.loop(until="count >= 5", max_iterations=100) as loop:
    inc = increment(counter=PARENT["count"])
    inc["counter"] >> PARENT["count"]
    START >> inc >> END

# Decorator style
@graph.loop(until="count >= 5")
def counter(count):
    inc = increment(counter=count)
    inc["counter"] >> PARENT["count"]
    START >> inc >> END

loop = counter(count=0)
```

### Wildcard Forwarding

Forward all inputs from parent:
```python
FuncOp(
    name="step",
    inputs={"specific": PARENT["x"], "*": PARENT},  # x explicit, rest forwarded
    ...
)
```

## Gotchas

1. **Op names**: Only alphanumeric, underscore, hyphen allowed
2. **Input/output overlap**: Same key cannot be in both inputs and outputs
3. **Soft edges**: Use `>>~` or `>` for branch outputs to avoid deadlocks
4. **PARENT resolution**: PARENT resolves at build time, not definition time
5. **Sync ops on event loop**: Sync `op.core` runs directly on the event loop by default (zero overhead). Use `executor="thread"` for blocking ops
