# BaseOp Anatomy

## Overview

`BaseOp` là base class cho tất cả ops trong Hush. Mọi op đều kế thừa từ class này.

Location: `hush-core/hush/core/ops/base.py`

## Slots

```python
class BaseOp(ABC):
    __slots__ = [
        'id',           # UUID của op
        'name',         # Tên op (unique trong graph)
        'description',  # Mô tả
        'type',         # OpType (code, graph, branch, for, map, while, ...)
        'stream',       # Có stream output không
        'start',        # Là entry op của graph
        'end',          # Là exit op của graph
        'verbose',      # Log execution
        'sources',      # Tên các predecessor ops
        'targets',      # Tên các successor ops
        'inputs',       # Dict[str, Param] - input parameters
        'outputs',      # Dict[str, Param] - output parameters
        'core',         # Callable - function thực thi logic chính
        'father',       # Parent GraphOp
        'contain_generation',  # Có chứa LLM generation không
        'enabled',      # Op có được thực thi hay không (default True)
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
    verbose: bool = True,
    enabled: bool = True  # Có thực thi op hay không
):
```

### Auto-registration

Khi một op được khởi tạo, nó tự động đăng ký với parent graph hiện tại:

```python
# Trong __init__
self.father = get_current()  # Lấy graph hiện tại từ context
add_op = getattr(self.father, "add_op", None)
if add_op is not None:
    add_op(self)
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
    "x": 10,                    # Literal -> Param(value=10, type=int)
    "y": other_op,              # Op ref -> Param(value=Ref(other_op, "y"))
    "z": other_op["result"],    # Op["var"] -> Param(value=Ref(other_op, "result"))
    "w": PARENT["input"],       # PARENT["var"] -> Param(value=Ref(father, "input"))
    "*": PARENT,                # Wildcard -> forward tất cả keys từ PARENT
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
    """op >> other: kết nối hard edge."""
    edge_type = "condition" if self.type == "branch" else "normal"
    add_edge = getattr(self.father, "add_edge", None)

    if isinstance(other, SoftEdge):
        # a >> ~b: soft edge
        if add_edge is not None:
            add_edge(self.name, other.op.name, edge_type, soft=True)
        return other.op

    if isinstance(other, list):
        # a >> [b, c]: multiple edges
        for op in other:
            if add_edge is not None:
                add_edge(self.name, op.name, edge_type)
        return other

    if add_edge is not None:
        add_edge(self.name, other.name, edge_type)
    return other
```

### Soft Edge (~)

```python
def __invert__(self) -> 'SoftEdge':
    """~op: Đánh dấu soft edge."""
    return SoftEdge(self)

class SoftEdge:
    """Wrapper cho soft edge connection."""
    def __init__(self, op: 'BaseOp'):
        self.op = op
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
2. **Skip nếu `enabled=False`** (return {} ngay)
3. Lấy inputs từ state qua `get_inputs()`
4. Thực thi `self.core(**inputs)`
5. Lưu outputs vào state qua `store_result()`
6. Log và record trace metadata

### get_inputs()

```python
def get_inputs(self, state, context_id, parent_context=None):
    result = {}
    for var_name, param in self.inputs.items():
        # Xác định context để lookup
        if parent_context and isinstance(param.value, Ref) and param.value.raw_source is self.father:
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
    """Đường dẫn phân cấp đầy đủ: parent.child.op"""
    if self.father:
        return f"{self.father.full_name}.{self.name}"
    return self.name
```

### Op Subscript

```python
def __getitem__(self, item) -> 'Ref':
    """op["var"] -> Ref đến output của op."""
    return Ref(self, item)
```

## Special Markers

### START / END / PARENT

```python
class DummyOp(BaseOp):
    """Dummy op cho các marker."""
    type: OpType = "dummy"

START = DummyOp("__START__")
END = DummyOp("__END__")
PARENT = DummyOp("__PARENT__")
```

Sử dụng:
```python
START >> op_a >> op_b >> END

# PARENT trong inputs
inputs = {"data": PARENT["input_data"]}
```

### Auto-Output Mapping với >> END

Khi một op kết nối trực tiếp đến END mà không có outputs định nghĩa sẵn, tất cả auto-parsed output keys sẽ tự động forward lên parent graph.

**Helper function** kiểm tra op có outputs explicit:

```python
def _has_explicit_outputs(op) -> bool:
    """Kiểm tra op có outputs được user định nghĩa explicit hay không.

    Returns False nếu:
    - outputs là None
    - outputs rỗng
    - outputs chỉ có các Param với value=None (auto-parsed từ function)

    Returns True nếu có bất kỳ output nào có value != None (user set).
    """
    if not hasattr(op, "outputs") or op.outputs is None:
        return False
    if len(op.outputs) == 0:
        return False
    for param in op.outputs.values():
        if hasattr(param, "value") and param.value is not None:
            return True
    return False
```

**Trong `BaseOp.__rshift__`**:

```python
def __rshift__(self, other):
    # ... existing logic ...

    # Check if other is END - auto-set wildcard outputs
    if getattr(other, "name", None) == "__END__":
        if not _has_explicit_outputs(self):
            if self.outputs is None:
                self.outputs = {}
            father = getattr(self, "father", None) or PARENT
            for key in self.outputs:
                param = self.outputs[key]
                if hasattr(param, "value") and param.value is None:
                    param.value = Ref(father, key)
    # ... continue with edge creation ...
```

**Ví dụ**:

```python
with GraphOp(name="demo") as graph:
    # Không cần định nghĩa outputs
    op = FuncOp(
        name="compute",
        code_fn=lambda: {"a": 1, "b": 2}
    )
    START >> op >> END  # Tự động: outputs = {"a": PARENT, "b": PARENT}

result = await engine.run(inputs={})
# result["a"] == 1, result["b"] == 2
```

**Lưu ý quan trọng**:
- Chỉ áp dụng khi op không có explicit outputs
- Ops với `outputs={"key": PARENT}` đã định nghĩa sẵn sẽ không bị thay đổi
- Hoạt động với cả `[op1, op2] >> END`

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
