# hush-core

Core workflow engine providing ops, state management, tracing, and the execution engine.

## Module Structure

```
hush/core/
├── engine.py           # Hush engine - compiles and runs workflows
├── background/         # Background trace process (SQLite writes + flush)
│   ├── __init__.py     # Re-exports public API
│   ├── db.py           # SQLite operations with fallback chain
│   ├── flush.py        # Trace reconstruction and dispatch
│   ├── process.py      # BackgroundProcess, subprocess fallback, _PipeQueue
│   └── worker.py       # Worker loop + subprocess entry point
├── exceptions.py       # Unified exception hierarchy (OpError, etc.)
├── ops/                # Op types (BaseOp, FuncOp, GraphOp, etc.)
├── states/             # State management (StateSchema, MemoryState, Cell, Ref)
├── configs/            # Configuration classes (OpConfig, EdgeConfig)
├── registry/           # Resource management (ResourceHub, plugins)
├── tracers/            # Local tracing (BaseTracer, SQLite storage)
├── streams/            # Data streaming
├── loggings/           # Logging configuration with Rich
└── utils/              # Utilities (context vars, common helpers)
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
   - `iteration/` - Loop ops (for, map, while, async_iter)
   - `graph/` - Container ops

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

### Op Lifecycle

1. **Definition**: Op created inside `with GraphOp(...) as graph:` context
2. **Registration**: Auto-registered to parent graph via `get_current()`
3. **Compilation**: `StateSchema` resolves all Refs and builds index
4. **Execution**: Engine calls `op.run(state, context_id)` → `op.core(**inputs)`

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

### Plugin Pattern

Plugins register handlers for resource types:
```python
from hush.core.registry import REGISTRY

@REGISTRY.register("llm")
def llm_plugin(config: dict) -> LLMConfig:
    return LLMConfig(**config)
```

## Tracer System

### BaseTracer Interface

Tracers use subprocess-based flushing — traces are written to SQLite during execution,
then flushed to external services in the background.

```python
from hush.core.tracers import BaseTracer, register_tracer

@register_tracer
class MyTracer(BaseTracer):
    def __init__(self, resource_key=None, tags=None):
        super().__init__(tags=tags)
        self._resource_key = resource_key

    def _get_tracer_config(self) -> dict:
        """Return config for serialization (passed to flush())."""
        return {"resource_key": self._resource_key}

    @staticmethod
    def flush(flush_data: dict) -> None:
        """Called by background process. Re-import deps here."""
        # flush_data contains: ops, tracer_config, tags, etc.
        config = flush_data["tracer_config"]
        # Send traces to your platform
        pass
```

### Registration

`@register_tracer` is a **decorator** that registers tracer classes for subprocess dispatch:

```python
from hush.core.tracers import register_tracer

@register_tracer
class MyTracer(BaseTracer):
    ...
```

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
llm = LLMOp.of(resource_key="gpt-4o", messages=msgs)
# llm.name == "llm" — extracted via bytecode analysis

router = if_(PARENT["x"] > 0, "pos").else_("neg")
# router.name == "router"
```

How it works: `auto_name()` in `utils/auto_name.py` walks the call stack (skipping `__init__`, `register_skip`'d frames), then:
1. **Bytecode analysis** (primary): finds `STORE_FAST`/`STORE_NAME` after the call site
2. **AST source parsing** (fallback): parses `var = expr` from source lines
3. **UUID fallback**: 8-char hex if both fail

Use `register_skip(fn)` to skip your factory function's frame during auto-naming.

### Shorthand via `Op.of()`

For concise op creation, use the `.of()` classmethod:
```python
from hush.core import op, ForOp, MapOp, WhileOp

@op
def process(x: int) -> dict:
    return {"result": x * 2}

# Iteration ops use .of()
with ForOp.of(x=Each([1, 2, 3])) as loop:
    step = process(x=PARENT["x"])
    START >> step >> END
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
5. **Async core**: If `op.core` is async, engine awaits it automatically
