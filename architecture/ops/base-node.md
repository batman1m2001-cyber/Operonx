# BaseOp Anatomy

## Overview

`BaseOp` la base class cho tat ca ops trong Hush. Moi op deu ke thua tu class nay.

Location: `hush-core/hush/core/ops/base.py`

## Slots

```python
class BaseOp(ABC):
    __slots__ = [
        'id',           # UUID cua op
        'name',         # Ten op (unique trong graph)
        'description',  # Mo ta
        'type',         # OpType (code, graph, branch, for, map, while, ...)
        'stream',       # Co stream output khong
        'start',        # La entry op cua graph
        'end',          # La exit op cua graph
        'verbose',      # Log execution
        'sources',      # Ten cac predecessor ops
        'targets',      # Ten cac successor ops
        'inputs',       # Dict[str, Param] - input parameters
        'outputs',      # Dict[str, Param] - output parameters
        'core',         # Callable - function thuc thi logic chinh
        'father',       # Parent GraphOp
        'contain_generation',  # Co chua LLM generation khong
        'enabled',      # Op co duoc thuc thi hay khong (default True)
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
    enabled: bool = True  # Co thuc thi op hay khong
):
```

### Auto-registration

Khi mot op duoc khoi tao, no tu dong dang ky voi parent graph hien tai:

```python
# Trong __init__
self.father = get_current()  # Lay graph hien tai tu context
add_op = getattr(self.father, "add_op", None)
if add_op is not None:
    add_op(self)
```

`get_current()` su dung `contextvars.ContextVar` de luu tru graph hien tai:

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
    type: Type = None        # Kieu du lieu (auto-inferred neu None)
    required: bool = False   # Co bat buoc khong
    default: Any = None      # Gia tri mac dinh
    description: str = ""    # Mo ta
    value: Any = None        # Ref hoac literal value
```

### Normalize Parameters

Inputs/outputs duoc chuan hoa tu Dict[str, Any] thanh Dict[str, Param]:

```python
# Cac format duoc ho tro:
inputs = {
    "x": 10,                    # Literal -> Param(value=10, type=int)
    "y": other_op,              # Op ref -> Param(value=Ref(other_op, "y"))
    "z": other_op["result"],    # Op["var"] -> Param(value=Ref(other_op, "result"))
    "w": PARENT["input"],       # PARENT["var"] -> Param(value=Ref(father, "input"))
    "*": PARENT,                # Wildcard -> forward tat ca keys tu PARENT
}
```

### Wildcard Forwarding

```python
# Forward tat ca inputs tu PARENT, tru nhung key da specify
inputs = {
    "custom": 10,   # Override
    "*": PARENT     # Forward con lai
}
```

## Edge Operators

### Hard Edge (>>)

```python
def __rshift__(self, other):
    """op >> other: ket noi hard edge."""
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
    """~op: Danh dau soft edge."""
    return SoftEdge(self)

class SoftEdge:
    """Wrapper cho soft edge connection."""
    def __init__(self, op: 'BaseOp'):
        self.op = op
```

Su dung:
```python
# Soft edge (chi can 1 predecessor hoan thanh)
branch >> ~case_a >> merge
branch >> ~case_b >> merge

# Hoac voi list
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
1. Record execution voi state
2. **Skip neu `enabled=False`** (return {} ngay)
3. Lay inputs tu state qua `get_inputs()`
4. Thuc thi `self.core(**inputs)`
5. Luu outputs vao state qua `store_result()`
6. Log va record trace metadata

### get_inputs()

```python
def get_inputs(self, state, context_id, parent_context=None):
    result = {}
    for var_name, param in self.inputs.items():
        # Xac dinh context de lookup
        if parent_context and isinstance(param.value, Ref) and param.value.raw_source is self.father:
            lookup_ctx = parent_context  # PARENT ref
        else:
            lookup_ctx = context_id

        # Doc tu state (tu dong resolve Ref)
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

    # Extract $tags neu co
    tags = result.pop("$tags", None)
    if tags:
        state.add_tags(tags)

    # Luu tung key vao state
    for key, value in result.items():
        state[self.full_name, key, context_id] = value
```

## Properties

### full_name

```python
@property
def full_name(self) -> str:
    """Duong dan phan cap day du: parent.child.op"""
    if self.father:
        return f"{self.father.full_name}.{self.name}"
    return self.name
```

### Op Subscript

```python
def __getitem__(self, item) -> 'Ref':
    """op["var"] -> Ref den output cua op."""
    return Ref(self, item)
```

## Special Markers

### START / END / PARENT

```python
class DummyOp(BaseOp):
    """Dummy op cho cac marker."""
    type: OpType = "dummy"

START = DummyOp("__START__")
END = DummyOp("__END__")
PARENT = DummyOp("__PARENT__")
```

Su dung:
```python
START >> op_a >> op_b >> END

# PARENT trong inputs
inputs = {"data": PARENT["input_data"]}
```

### Auto-Output Mapping voi >> END

Khi mot op ket noi truc tiep den END ma khong co outputs dinh nghia san, tat ca auto-parsed output keys se tu dong forward len parent graph.

**Helper function** kiem tra op co outputs explicit:

```python
def _has_explicit_outputs(op) -> bool:
    """Kiem tra op co outputs duoc user dinh nghia explicit hay khong.

    Returns False neu:
    - outputs la None
    - outputs rong
    - outputs chi co cac Param voi value=None (auto-parsed tu function)

    Returns True neu co bat ky output nao co value != None (user set).
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

**Vi du**:

```python
with GraphOp(name="demo") as graph:
    # Khong can dinh nghia outputs
    op = FuncOp(
        name="compute",
        code_fn=lambda: {"a": 1, "b": 2}
    )
    START >> op >> END  # Tu dong: outputs = {"a": PARENT, "b": PARENT}

result = await engine.run(inputs={})
# result["a"] == 1, result["b"] == 2
```

**Luu y quan trong**:
- Chi ap dung khi op khong co explicit outputs
- Ops voi `outputs={"key": PARENT}` da dinh nghia san se khong bi thay doi
- Hoat dong voi ca `[op1, op2] >> END`

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
