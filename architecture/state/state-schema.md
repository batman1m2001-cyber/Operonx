# StateSchema Design

## Overview

`StateSchema` định nghĩa cấu trúc state của workflow với độ phân giải O(1).

Location: `hush-core/hush/core/states/schema.py`

## Class Definition

```python
class StateSchema:
    __slots__ = ("name", "_var_to_idx", "_defaults", "_pull_refs", "_push_refs")

    # Map (op, var) → storage index
    _var_to_idx: Dict[Tuple[str, str], int]

    # Default values theo index
    _defaults: List[Any]

    # Pull refs: khi đọc biến, pull từ source (1 hop)
    _pull_refs: List[Optional[Ref]]

    # Push refs: khi ghi biến, push đến target (1 hop)
    _push_refs: List[Optional[Ref]]
```

## Construction

### From Op

```python
schema = StateSchema(op=my_graph, name="my_workflow")
```

Internally:
1. `_load_from(op)` - traverse graph, collect variables
2. `_build()` - resolve refs to indices

### Manual

```python
schema = StateSchema(name="manual")
schema.set("node_a", "var", default_value)
```

## Loading Variables

### _load_from()

Recursive traversal cua op tree:

```python
def _load_from(self, op):
    op_name = op.full_name

    # 1. Register input variables
    for var_name, param in op.inputs.items():
        if param.value is not None:
            self._register(op_name, var_name, param.value)
        else:
            self._register(op_name, var_name, param.default)

    # 2. Register output variables
    for var_name, param in op.outputs.items():
        if isinstance(param.value, Ref):
            # Push ref: op.var -> target.var
            self._register_push_ref(op_name, var_name, Ref(...))
        else:
            self._register(op_name, var_name, param.default)

    # 3. Register metadata variables
    for meta_var in ("start_time", "end_time", "error"):
        self._register(op_name, meta_var, None)

    # 4. Recursively load child ops
    if hasattr(op, '_ops'):
        for child in op._ops.values():
            self._load_from(child)
```

### _register()

```python
def _register(self, op: str, var: str, value: Any):
    key = (op, var)

    if key in self._var_to_idx:
        # Already registered - update if value is Ref or current is None
        idx = self._var_to_idx[key]
        current = self._defaults[idx]
        if isinstance(value, Ref) or (current is None and value is not None):
            self._defaults[idx] = value
        return

    # New variable - assign index
    idx = len(self._defaults)
    self._var_to_idx[key] = idx
    self._defaults.append(value)  # May be Ref, resolved in _build()
    self._pull_refs.append(None)
    self._push_refs.append(None)
```

## Building Index

### _build()

Resolve all Refs to indices:

```python
def _build(self):
    for key, idx in self._var_to_idx.items():
        # Resolve pull refs (Refs in _defaults)
        value = self._defaults[idx]
        if isinstance(value, Ref):
            source_key = (value.source, value.var)
            source_idx = self._var_to_idx.get(source_key, -1)
            value.idx = source_idx  # Set source index on Ref
            self._pull_refs[idx] = value
            self._defaults[idx] = None  # Clear Ref, value comes from source

        # Resolve push refs
        push_ref = self._push_refs[idx]
        if push_ref is not None:
            target_key = (push_ref.source, push_ref.var)
            target_idx = self._var_to_idx.get(target_key, -1)
            push_ref.idx = target_idx
```

## Core Methods

### get_index()

```python
def get_index(self, op: str, var: str) -> int:
    """O(1) lookup. Returns -1 if not found."""
    return self._var_to_idx.get((op, var), -1)
```

### get_pull_ref() / get_push_ref()

```python
def get_pull_ref(self, idx: int) -> Optional[Ref]:
    if 0 <= idx < len(self._pull_refs):
        return self._pull_refs[idx]
    return None

def get_push_ref(self, idx: int) -> Optional[Ref]:
    if 0 <= idx < len(self._push_refs):
        return self._push_refs[idx]
    return None
```

## Debug

### show()

```python
schema.show()

# Output:
# === StateSchema: my_workflow ===
# my_graph.op_a.input [0] <- pull my_graph.input[1]
# my_graph.op_a.result [2] -> push my_graph.output[3]
# ...
```

## Collection Interface

```python
# Iterate over (op, var) pairs
for op, var in schema:
    print(f"{op}.{var}")

# Check if variable exists
if ("node_a", "result") in schema:
    ...

# Get index (raises KeyError if not found)
idx = schema["node_a", "result"]

# Length
num_vars = len(schema)
```
