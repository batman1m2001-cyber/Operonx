# Index System Internals

## Overview

StateSchema sử dụng index-based storage để đạt O(1) access thay vì hash map lookup mỗi lần đọc/ghi.

## Index Structure

```
_var_to_idx: {(node, var): idx}
┌─────────────────────────────────┐
│ ("graph.a", "input")  → 0       │
│ ("graph.a", "result") → 1       │
│ ("graph.b", "data")   → 2       │
│ ("graph.b", "output") → 3       │
│ ...                             │
└─────────────────────────────────┘

_cells: [Cell0, Cell1, Cell2, Cell3, ...]
         ↑      ↑      ↑      ↑
         │      │      │      │
         0      1      2      3
```

## Index Assignment

Indices được assign tuần tự khi register variables:

```python
def _register(self, node, var, value):
    key = (node, var)
    if key not in self._var_to_idx:
        idx = len(self._defaults)  # Next available index
        self._var_to_idx[key] = idx
        self._defaults.append(value)
        self._pull_refs.append(None)
        self._push_refs.append(None)
```

## Ref Index Resolution

Refs được resolve sang index lúc build:

```python
def _build(self):
    for key, idx in self._var_to_idx.items():
        value = self._defaults[idx]
        if isinstance(value, Ref):
            # Find source index
            source_key = (value.node, value.var)
            source_idx = self._var_to_idx.get(source_key, -1)

            # Store index on Ref
            value.idx = source_idx
            self._pull_refs[idx] = value
            self._defaults[idx] = None
```

### Before _build()

```
_defaults[2] = Ref("graph.a", "result")  # Ref object
_pull_refs[2] = None
```

### After _build()

```
_defaults[2] = None  # Cleared
_pull_refs[2] = Ref(idx=1)  # Ref with resolved index
```

## Runtime Access Pattern

### Read

```python
# 1. Get index from key
idx = schema.get_index("graph.b", "data")  # O(1) hash lookup

# 2. Access cell directly
cell = _cells[idx]  # O(1) array access

# 3. Get value from cell
value = cell[context_id]  # O(1) dict lookup
```

### Write

```python
# 1. Get index
idx = schema.get_index("graph.a", "result")

# 2. Write to cell
_cells[idx][context_id] = value

# 3. Check push ref (already has resolved index)
push_ref = _push_refs[idx]
if push_ref:
    target_idx = push_ref.idx  # Pre-computed!
    _cells[target_idx][context_id] = push_ref._fn(value)
```

## Pre-computed vs Dynamic

| Operation | Pre-computed (build) | Runtime |
|-----------|---------------------|---------|
| (node, var) → idx | ✓ (_var_to_idx) | O(1) lookup |
| pull source idx | ✓ (Ref.idx) | Direct use |
| push target idx | ✓ (Ref.idx) | Direct use |
| transform fn | ✓ (Ref._fn) | Direct call |

## Iteration Context Indexing

Iteration nodes tạo multiple contexts:

```python
# Context ID format
"[0]", "[1]", "[2]", ...

# Nested context
"parent.[0]", "parent.[1]", ...

# Pre-computed suffixes (0-999) for performance
_CTX_SUFFIXES = tuple("[" + str(i) + "]" for i in range(1000))

def get_iter_context(prefix: str, i: int) -> str:
    if i < 1000:
        return prefix + _CTX_SUFFIXES[i]  # No string concat
    return prefix + "[" + str(i) + "]"
```

## Memory Layout

```
MemoryState:
  schema ──────────────┐
  _cells ──────────┐   │
                   │   │
                   ▼   ▼
StateSchema:       Cell Array:
  _var_to_idx      [Cell, Cell, Cell, ...]
  _defaults            │     │     │
  _pull_refs           ▼     ▼     ▼
  _push_refs       contexts: contexts: contexts:
                   {"main":v} {"[0]":v} {"main":v}
                             {"[1]":v}
```

## Performance Considerations

### Avoid

```python
# String concatenation in hot path
key = f"{node}.{var}"  # Creates new string

# Repeated hash lookup
for i in range(1000):
    value = state[node, var, f"[{i}]"]  # 1000 hash lookups
```

### Prefer

```python
# Pre-lookup index
idx = schema.get_index(node, var)

# Direct cell access
for i in range(1000):
    ctx = get_iter_context("", i)  # Pre-computed for i < 1000
    value = state._cells[idx][ctx]
```
