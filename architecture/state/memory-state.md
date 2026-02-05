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
        "_execution_order", # List[Dict] - execution tracking
        "_trace_metadata",  # Dict - trace data
        "_user_id",
        "_session_id",
        "_request_id",
        "_trace_store",     # Optional SQLite store
        "_execution_count",
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
    trace_store=None  # Or SQLite TraceStore
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
    node, var, ctx = key
    idx = self.schema.get_index(node, var)
    if idx < 0:
        raise KeyError(f"({node}, {var}) không có trong schema")

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
    node, var, ctx = key
    idx = self.schema.get_index(node, var)
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

## Execution Tracking

### record_execution()

```python
def record_execution(self, node_name, parent, context_id):
    """Track node execution order."""
    if self._execution_order is not None:
        self._execution_order.append({
            "node": node_name,
            "parent": parent,
            "context_id": context_id
        })
```

### record_trace_metadata()

```python
def record_trace_metadata(
    self,
    node_name: str,
    context_id: Optional[str],
    name: str,
    input_vars: List[str],
    output_vars: List[str],
    parent_name: Optional[str] = None,
    start_time: Any = None,
    end_time: Any = None,
    duration_ms: Optional[float] = None,
    contain_generation: bool = False,
    model: Optional[str] = None,
    usage: Optional[Dict[str, int]] = None,
    cost: Optional[float] = None,
    metadata: Optional[Dict[str, Any]] = None,
):
    """Store trace data."""
    if self._trace_store is not None:
        # Write directly to SQLite
        self._trace_store.insert_node_trace(...)
    else:
        # Store in memory
        key = f"{node_name}:{context_id}" if context_id else node_name
        self._trace_metadata[key] = {...}
```

## Dynamic Tags

```python
# Add single tag
state.add_tag("cache-hit")

# Add multiple tags
state.add_tags(["processed", "validated"])

# From node output
return {"result": data, "$tags": ["success"]}
```

## Properties

```python
# Identifiers
state.user_id
state.session_id
state.request_id

# Execution data (legacy mode)
state.execution_order  # List of execution records
state.trace_metadata   # Dict of trace data

# Tags
state.tags  # List of dynamic tags
```

## Tracing Modes

### Legacy Mode (in-memory)

```python
state = MemoryState(schema, trace_store=None)
# Traces stored in _execution_order and _trace_metadata
```

### SQLite Mode (incremental)

```python
state = MemoryState(schema, trace_store=trace_store)
# Traces written directly to SQLite
# Reduces memory, provides crash resilience
```

## Debug

```python
state.show()

# Output:
# === MemoryState: my_workflow ===
# my_graph.node_a.input [main] = "hello"
# my_graph.node_a.result [main] = "HELLO"
# my_graph.loop.inner.item:
#   [[0]] = 1
#   [[1]] = 2
```
