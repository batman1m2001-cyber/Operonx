# MemoryState Implementation

## Overview

`MemoryState` lưu trữ giá trị runtime với Cell-based storage và O(1) access.

Location: `hush-core/hush/core/states/state.py`

## Class Definition

```python
class MemoryState:
    __slots__ = (
        "schema",           # StateSchema
        "_cells",           # List[Cell] - storage
        "_user_id",
        "_session_id",
        "_request_id",
        "_tags"             # Dynamic tags
    )
```

## Construction

```python
state = MemoryState(
    schema=schema,
    inputs={"query": "hello"},  # Initial inputs
    user_id="user_123",
    session_id="session_456",
    request_id="req_789",
)
```

### Cell Initialization

```python
def __init__(self, schema, inputs=None, ...):
    self.schema = schema
    # Create cells from schema defaults
    self._cells = [Cell(v) for v in schema._defaults]

    # Apply initial inputs
    if inputs:
        for var, value in inputs.items():
            idx = schema.get_index(schema.name, var)
            if idx >= 0:
                self._cells[idx][None] = value
```

## Core API

### __setitem__

```python
def __setitem__(self, key: Tuple[str, str, Optional[str]], value: Any):
    """Store value. Push to target if push_ref exists (1 hop)."""
    op, var, ctx = key
    idx = self.schema.get_index(op, var)
    if idx < 0:
        raise KeyError(f"({op}, {var}) không có trong schema")

    ctx_key = ctx if ctx is not None else "main"
    self._cells[idx][ctx_key] = value

    # Push ref? Push 1 hop to target
    push_ref = self.schema._push_refs[idx]
    if push_ref and push_ref.idx >= 0:
        self._cells[push_ref.idx][ctx_key] = push_ref._fn(value)
```

### __getitem__

```python
def __getitem__(self, key: Tuple[str, str, Optional[str]]) -> Any:
    """Get value. Pull from source if pull_ref exists (1 hop)."""
    op, var, ctx = key
    idx = self.schema.get_index(op, var)
    if idx < 0:
        return None

    ctx_key = ctx if ctx is not None else "main"
    cell = self._cells[idx]

    # Has cached value? Return it
    if ctx_key in cell:
        return cell[ctx_key]

    # Pull ref? Pull 1 hop from source and cache
    pull_ref = self.schema._pull_refs[idx]
    if pull_ref and not pull_ref.is_output and pull_ref.idx >= 0:
        source_cell = self._cells[pull_ref.idx]
        if ctx_key in source_cell or source_cell.default_value is not None:
            result = pull_ref._fn(source_cell[ctx_key])
            cell[ctx_key] = result  # Cache
            return result

    # No value - return default
    return cell.default_value
```

## Index-based Access

Bypass ref resolution:

```python
# Direct cell access
value = state.get_by_index(idx, ctx)
state.set_by_index(idx, value, ctx)
```

## Execution History (Derived)

### iter_executed()

Derives execution history from `start_time` cells — no separate recording or storage needed. `TraceCollector` calls this after `engine.run()` completes to discover which ops ran and in which contexts.

```python
def iter_executed(self, op_name: str):
    """Yield (context_id, start_time) for each execution of op_name.

    Derives execution history from start_time cells — no separate
    recording needed. Used by TraceCollector post-execution.
    """
    idx = self.schema.get_index(op_name, "start_time")
    if idx < 0:
        return
    for ctx, value in self._cells[idx].items():
        if value is not None:
            yield ctx, value
```

**How it works:** Every op stores a `start_time` into its state cell when it begins execution. `iter_executed()` looks up the `start_time` cell for the given `op_name` and yields all `(context_id, start_time)` pairs where the value is not `None`. For loop ops, each iteration has a different context, so the same op can yield multiple entries.

**Example:**

```python
# After engine.run() completes:
state = result["$state"]

# Non-loop op: one execution
list(state.iter_executed("workflow.step1"))
# → [("main", datetime(2025, 1, 15, 10, 30, 0, 100000))]

# Loop op: multiple iterations
list(state.iter_executed("workflow.loop.step"))
# → [("[0]", datetime(...)), ("[1]", datetime(...)), ("[2]", datetime(...))]

# Unknown or unexecuted op: empty
list(state.iter_executed("unknown_op"))
# → []
```

## Dynamic Tags

```python
# Add single tag
state.add_tag("cache-hit")

# Add multiple tags
state.add_tags(["processed", "validated"])

# From op output
return {"result": data, "$tags": ["success"]}
```

## Properties

```python
# Identifiers
state.user_id
state.session_id
state.request_id

# Tags
state.tags  # List of dynamic tags

# Execution history (derived, not stored)
state.iter_executed(op_name)  # Yields (context_id, start_time) tuples
```

## Debug

```python
state.show()

# Output:
# === MemoryState: my_workflow ===
# my_graph.op_a.input [main] = "hello"
# my_graph.op_a.result [main] = "HELLO"
# my_graph.loop.inner.item:
#   [[0]] = 1
#   [[1]] = 2
```
