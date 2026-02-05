# State System Overview

## Mục đích

State system quản lý data flow giữa các nodes trong workflow với **O(1) lookup** sử dụng index-based storage.

## Components

```
┌─────────────────────────────────────────┐
│              StateSchema                │
│  ┌───────────────────────────────────┐  │
│  │ _var_to_idx: {(node,var): index}  │  │
│  │ _defaults: [default_values]       │  │
│  │ _pull_refs: [Ref or None]         │  │
│  │ _push_refs: [Ref or None]         │  │
│  └───────────────────────────────────┘  │
└─────────────────────────────────────────┘
                    │
                    ▼
┌─────────────────────────────────────────┐
│              MemoryState                │
│  ┌─────┐ ┌─────┐ ┌─────┐ ┌─────┐       │
│  │Cell │ │Cell │ │Cell │ │Cell │ ...   │
│  │ [0] │ │ [1] │ │ [2] │ │ [3] │       │
│  └─────┘ └─────┘ └─────┘ └─────┘       │
└─────────────────────────────────────────┘
```

## Files

| File | Mô tả |
|------|-------|
| `schema.py` | StateSchema - định nghĩa cấu trúc state |
| `state.py` | MemoryState - lưu trữ giá trị runtime |
| `ref.py` | Ref - tham chiếu với chain operations |
| `cell.py` | Cell - lưu trữ multi-context values |

## Design Principles

### 1. Index-based O(1) Access

Thay vì hash map lookup mỗi lần, pre-compute indices lúc build:

```python
# Build time: map (node, var) → index
_var_to_idx = {("node_a", "result"): 0, ("node_b", "input"): 1, ...}

# Runtime: O(1) access by index
value = self._cells[0][context_id]
```

### 2. Single-hop References

Pull/push refs chỉ resolve 1 hop:
- Tránh recursive resolution complexity
- Easy to debug data flow
- Predictable performance

```python
# Pull: A reads from B (1 hop)
A.input ← B.output

# Push: A writes to B (1 hop)
A.output → B.input
```

### 3. Lazy Pull

Giá trị chỉ được pull khi thực sự cần đọc:

```python
def __getitem__(self, key):
    # Return cached if exists
    if ctx in cell:
        return cell[ctx]

    # Pull only when needed
    if pull_ref:
        result = pull_ref._fn(source_cell[ctx])
        cell[ctx] = result  # Cache
        return result
```

### 4. Cell-based Multi-context

Mỗi variable có thể có nhiều values trong các contexts khác nhau (cho iteration nodes):

```python
# Normal execution
state["node", "var", None]  # context = "main"

# Iteration execution
state["loop.inner", "result", "[0]"]
state["loop.inner", "result", "[1]"]
```

## Data Flow

### Pull vs Push

```
Pull ref (trong inputs):
  inputs={"data": PARENT["input"]}
  Khi node đọc "data", pull từ PARENT["input"]

Push ref (trong outputs):
  outputs={"result": PARENT}
  Khi node ghi "result", push đến PARENT["result"]
```

### Example Flow

```
PARENT["input"] ──pull──> A["data"]
                              │
                           execute
                              │
                         A["result"] ──push──> PARENT["output"]
```

## Workflow

1. **Build StateSchema từ graph**
   - Traverse graph tree
   - Collect tất cả variables
   - Map (node, var) → index
   - Build pull_refs và push_refs

2. **Create MemoryState**
   - Allocate cells theo schema
   - Set initial inputs

3. **Runtime**
   - Nodes đọc inputs (auto pull)
   - Nodes ghi outputs (auto push)
   - Values cached in cells
