# BaseNode Anatomy

## Overview

`BaseNode` là base class cho tất cả nodes trong Hush. Mọi node đều kế thừa từ class này.

Location: `hush-core/hush/core/nodes/base.py`

## Slots

```python
class BaseNode(ABC):
    __slots__ = [
        'id',           # UUID của node
        'name',         # Tên node (unique trong graph)
        'description',  # Mô tả
        'type',         # NodeType (code, graph, branch, for, map, while, ...)
        'stream',       # Có stream output không
        'start',        # Là entry node của graph
        'end',          # Là exit node của graph
        'verbose',      # Log execution
        'sources',      # Tên các predecessor nodes
        'targets',      # Tên các successor nodes
        'inputs',       # Dict[str, Param] - input parameters
        'outputs',      # Dict[str, Param] - output parameters
        'core',         # Callable - function thực thi logic chính
        'father',       # Parent GraphNode
        'contain_generation',  # Có chứa LLM generation không
    ]
```

## Constructor

```python
def __init__(
    self,
    id: str = None,
    name: str = None,
    description: str = "",
    inputs: Dict[str, Any] = None,
    outputs: Dict[str, Any] = None,
    sources: List[str] = None,
    targets: List[str] = None,
    stream: bool = False,
    start: bool = False,
    end: bool = False,
    contain_generation: bool = False,
    verbose: bool = True
):
```

### Auto-registration

Khi một node được khởi tạo, nó tự động đăng ký với parent graph hiện tại:

```python
# Trong __init__
self.father = get_current()  # Lấy graph hiện tại từ context
add_node = getattr(self.father, "add_node", None)
if add_node is not None:
    add_node(self)
```

`get_current()` sử dụng `contextvars.ContextVar` để lưu trữ graph hiện tại:

```python
# hush/core/utils/context.py
_current_graph = contextvars.ContextVar("current_graph")

def get_current():
    try:
        return _current_graph.get()
    except LookupError:
        return None
```

## Input/Output System

### Param Class

```python
# hush/core/utils/common.py
@dataclass
class Param:
    type: Type = None        # Kiểu dữ liệu (auto-inferred nếu None)
    required: bool = False   # Có bắt buộc không
    default: Any = None      # Giá trị mặc định
    description: str = ""    # Mô tả
    value: Any = None        # Ref hoặc literal value
```

### Normalize Parameters

Inputs/outputs được chuẩn hóa từ Dict[str, Any] thành Dict[str, Param]:

```python
# Các format được hỗ trợ:
inputs = {
    "x": 10,                    # Literal → Param(value=10, type=int)
    "y": other_node,            # Node ref → Param(value=Ref(other_node, "y"))
    "z": other_node["result"],  # Node["var"] → Param(value=Ref(other_node, "result"))
    "w": PARENT["input"],       # PARENT["var"] → Param(value=Ref(father, "input"))
    "*": PARENT,                # Wildcard → forward tất cả keys từ PARENT
}
```

### Wildcard Forwarding

```python
# Forward tất cả inputs từ PARENT, trừ những key đã specify
inputs = {
    "custom": 10,   # Override
    "*": PARENT     # Forward còn lại
}
```

## Edge Operators

### Hard Edge (>>)

```python
def __rshift__(self, other):
    """node >> other: kết nối hard edge."""
    edge_type = "condition" if self.type == "branch" else "normal"
    add_edge = getattr(self.father, "add_edge", None)

    if isinstance(other, SoftEdge):
        # a >> ~b: soft edge
        if add_edge is not None:
            add_edge(self.name, other.node.name, edge_type, soft=True)
        return other.node

    if isinstance(other, list):
        # a >> [b, c]: multiple edges
        for node in other:
            if add_edge is not None:
                add_edge(self.name, node.name, edge_type)
        return other

    if add_edge is not None:
        add_edge(self.name, other.name, edge_type)
    return other
```

### Soft Edge (~)

```python
def __invert__(self) -> 'SoftEdge':
    """~node: Đánh dấu soft edge."""
    return SoftEdge(self)

class SoftEdge:
    """Wrapper cho soft edge connection."""
    def __init__(self, node: 'BaseNode'):
        self.node = node
```

Sử dụng:
```python
# Soft edge (chỉ cần 1 predecessor hoàn thành)
branch >> ~case_a >> merge
branch >> ~case_b >> merge

# Hoặc với list
[case_a, case_b] >> ~merge
```

## Execution

### run() Method

```python
async def run(
    self,
    state: 'MemoryState',
    context_id: Optional[str] = None,
    parent_context: Optional[str] = None
) -> Dict[str, Any]:
```

Flow:
1. Record execution với state
2. Lấy inputs từ state qua `get_inputs()`
3. Thực thi `self.core(**inputs)`
4. Lưu outputs vào state qua `store_result()`
5. Log và record trace metadata

### get_inputs()

```python
def get_inputs(self, state, context_id, parent_context=None):
    result = {}
    for var_name, param in self.inputs.items():
        # Xác định context để lookup
        if parent_context and isinstance(param.value, Ref) and param.value.raw_node is self.father:
            lookup_ctx = parent_context  # PARENT ref
        else:
            lookup_ctx = context_id

        # Đọc từ state (tự động resolve Ref)
        value = state[self.full_name, var_name, lookup_ctx]

        if value is not None:
            result[var_name] = value
        elif param.value is not None and not isinstance(param.value, Ref):
            result[var_name] = param.value  # Literal fallback
        elif param.default is not None:
            result[var_name] = param.default  # Default fallback

    return result
```

### store_result()

```python
def store_result(self, state, result, context_id):
    if not result:
        return

    # Extract $tags nếu có
    tags = result.pop("$tags", None)
    if tags:
        state.add_tags(tags)

    # Lưu từng key vào state
    for key, value in result.items():
        state[self.full_name, key, context_id] = value
```

## Properties

### full_name

```python
@property
def full_name(self) -> str:
    """Đường dẫn phân cấp đầy đủ: parent.child.node"""
    if self.father:
        return f"{self.father.full_name}.{self.name}"
    return self.name
```

### Node Subscript

```python
def __getitem__(self, item) -> 'Ref':
    """node["var"] → Ref đến output của node."""
    return Ref(self, item)
```

## Special Markers

### START / END / PARENT

```python
class DummyNode(BaseNode):
    """Dummy node cho các marker."""
    type: NodeType = "dummy"

START = DummyNode("__START__")
END = DummyNode("__END__")
PARENT = DummyNode("__PARENT__")
```

Sử dụng:
```python
START >> node_a >> node_b >> END

# PARENT trong inputs
inputs = {"data": PARENT["input_data"]}
```

## Metadata

```python
def metadata(self) -> Dict[str, Any]:
    return {
        "id": self.id,
        "name": self.full_name,
        "type": self.type,
        "description": self.description,
        "input_connects": {...},
        "output_connects": {...},
        ...
    }
```
