# GraphOp - Nested Graphs & Scoping

## Overview

`GraphOp` là container op chứa một graph các ops. Nó cho phép tổ chức workflow thành các module có thể tái sử dụng.

Location: `hush-core/hush/core/ops/graph/graph_op.py`

## Class Definition

```python
class GraphOp(BaseOp):
    type: OpType = "graph"

    __slots__ = [
        '_token',           # Context token để restore
        '_ops',             # Dict[str, BaseOp] - child ops
        'entries',          # List entry op names
        'exits',            # List exit op names
        'prevs',            # Dict[op_name, List[predecessor_names]]
        'nexts',            # Dict[op_name, List[successor_names]]
        'ready_count',      # Dict[op_name, int] - số predecessors cần chờ
        'has_soft_preds',   # Set các ops có soft predecessor
        'flowtype_map',     # BiMap[op_name, OpFlowType]
        '_edges',           # List[EdgeConfig]
        '_edges_lookup',    # Dict[(source, target), EdgeConfig]
        '_is_building',     # Flag đang trong quá trình build
        '_compiled_adj'     # Compiled adjacency data for fast execution
    ]
```

## Context Manager

GraphOp sử dụng context manager để tự động đăng ký child ops:

```python
def __enter__(self):
    """Vào context - set graph này làm current."""
    self._token = _current_graph.set(self)
    return self

def __exit__(self, exc_type, exc_val, exc_tb):
    """Thoát context - restore graph trước đó."""
    _current_graph.reset(self._token)
```

Sử dụng:

```python
with GraphOp(name="my_graph") as graph:
    # Tất cả ops tạo trong block này tự động đăng ký vào graph
    op_a = FuncOp(name="a", ...)
    op_b = FuncOp(name="b", ...)
    START >> op_a >> op_b >> END
```

## Op & Edge Management

### add_op()

```python
def add_op(self, op: BaseOp) -> BaseOp:
    """Thêm op vào graph."""
    if not self._is_building:
        raise RuntimeError("Không thể thêm op sau khi graph đã build")

    if op in [START, END]:
        return op

    self._ops[op.name] = op

    # Track start/end ops
    if op.start:
        self.entries.append(op.name)
    if op.end:
        self.exits.append(op.name)

    return op
```

### add_edge()

```python
def add_edge(self, source: str, target: str, type: EdgeType = "normal", soft: bool = False):
    """Thêm edge giữa hai ops."""
    if not self._is_building:
        raise RuntimeError("Không thể thêm edge sau khi graph đã build")

    # Handle START edge
    if source == START.name:
        self._ops[target].start = True
        self.entries.append(target)
        return

    # Handle END edge
    if target == END.name:
        self._ops[source].end = True
        self.exits.append(source)
        return

    # Normal edge
    new_edge = EdgeConfig(from_op=source, to_op=target, type=type, soft=soft)
    self._edges.append(new_edge)
    self._edges_lookup[source, target] = new_edge
    self.nexts[source].append(target)
    self.prevs[target].append(source)
```

## Build Process

### build()

```python
def build(self):
    """Build graph - phải gọi trước khi execute."""
    # 1. Build tất cả child ops trước
    for op in self._ops.values():
        if hasattr(op, 'build'):
            op.build()

    # 2. Setup schema từ child ops
    self._setup_schema()

    # 3. Xác định flow type của mỗi op
    self._build_flow_type()

    # 4. Setup entry/exit endpoints
    self._setup_endpoints()

    # 5. Tính ready_count cho mỗi op
    self._compute_ready_counts()

    self._is_building = False
    self._post_build()
```

### _setup_schema()

Scan child ops để tìm PARENT refs - đó chính là inputs/outputs của graph:

```python
def _setup_schema(self):
    graph_inputs = {}
    graph_outputs = {}

    for _, op in self._ops.items():
        # Input refs đến PARENT -> graph input
        for var, param in op.inputs.items():
            if isinstance(param.value, Ref) and param.value.raw_source is self:
                graph_inputs[param.value.var] = Param(...)

        # Output refs đến PARENT -> graph output
        for var, param in op.outputs.items():
            if isinstance(param.value, Ref) and param.value.raw_source is self:
                graph_outputs[param.value.var] = Param(...)

    self.inputs = self._merge_params(graph_inputs, self.inputs)
    self.outputs = self._merge_params(graph_outputs, self.outputs)
```

### Ready Count

```python
# Hard edges: đếm từng predecessor
# Soft edges: đếm chung tất cả soft predecessors là 1

ready_count = {}
for name in self._ops:
    hard_pred_count = 0
    has_soft = False

    for pred in self.prevs[name]:
        edge = self._edges_lookup.get((pred, name))
        if edge and edge.soft:
            has_soft = True
        else:
            hard_pred_count += 1

    # Soft edges đếm chung là 1
    if has_soft:
        self.has_soft_preds.add(name)
        hard_pred_count += 1

    ready_count[name] = hard_pred_count
```

## Execution

### run()

```python
async def run(self, state, context_id=None, parent_context=None):
    # 1. Lấy inputs
    _inputs = self.get_inputs(state, context_id, parent_context)

    # 2. Khởi tạo tasks cho entry ops
    active_tasks = {}
    ready_count = self.ready_count.copy()
    soft_satisfied = set()

    for entry in self.entries:
        task = asyncio.create_task(
            name=entry,
            coro=self._ops[entry].run(state, context_id, parent_context)
        )
        active_tasks[entry] = task

    # 3. Execute loop
    while active_tasks:
        done_tasks, _ = await asyncio.wait(
            active_tasks.values(),
            return_when=asyncio.FIRST_COMPLETED
        )

        for task in done_tasks:
            op_name = task.get_name()
            active_tasks.pop(op_name)
            op = self._ops[op_name]

            # Xác định next ops (branch có logic riêng)
            if op.type == "branch":
                branch_target = op.get_target(state, context_id)
                next_ops = [branch_target] if branch_target != END.name else []
            else:
                next_ops = self.nexts[op_name]

            # Update ready counts và schedule next ops
            for next_op in next_ops:
                edge = self._edges_lookup.get((op_name, next_op))
                is_soft = edge and edge.soft

                if is_soft:
                    if next_op in soft_satisfied:
                        continue  # Đã có soft pred hoàn thành
                    soft_satisfied.add(next_op)

                ready_count[next_op] -= 1

                if ready_count[next_op] == 0:
                    task = asyncio.create_task(
                        name=next_op,
                        coro=self._ops[next_op].run(state, context_id, parent_context)
                    )
                    active_tasks[next_op] = task

    # 4. Collect outputs
    _outputs = self.get_outputs(state, context_id, parent_context)
    self.store_result(state, _outputs, context_id)
    return _outputs
```

## Flow Types

```python
OpFlowType = Literal["MERGE", "FORK", "BLOOM", "BRANCH", "NORMAL", "OTHER"]

# MERGE: nhiều inputs, 1 output (prev > 1, next = 1)
# FORK: 1 input, nhiều outputs (prev = 1, next > 1)
# BLOOM: nhiều inputs, nhiều outputs (prev > 1, next > 1)
# BRANCH: BranchOp
# NORMAL: 1 input, 1 output
# OTHER: entry/exit ops
```

## Scoping

### Nested Graphs

GraphOp có thể nest trong GraphOp khác:

```python
with GraphOp(name="outer") as outer:
    with GraphOp(name="inner") as inner:
        a = FuncOp(name="a", ...)
        START >> a >> END

    b = FuncOp(name="b", ...)
    START >> inner >> b >> END

# Op paths:
# - outer.inner.a
# - outer.b
```

### PARENT Reference

Ops trong nested graph truy cập parent qua PARENT:

```python
with GraphOp(name="outer", inputs={"data": some_source}) as outer:
    with GraphOp(name="inner") as inner:
        process = FuncOp(
            name="process",
            inputs={"x": PARENT["data"]}  # Lấy từ inner graph
        )
        START >> process >> END

    # inner graph nhận data từ outer
    inner_op = inner  # inner graph như một op
    inner_op.inputs = {"data": PARENT["data"]}  # Từ outer graph
```

## @graph Decorator

`@graph` biến một builder function thành factory tạo GraphOp có thể tái sử dụng, hỗ trợ auto-naming.

Location: cuối `graph_op.py`, sau class `GraphOp`.

### Cách hoạt động

```python
from hush.core import graph, op, START, END, PARENT, GraphOp

@op
def double(x: int):
    return {"result": x * 2}

@graph
def double_flow(val):
    step = double(x=val)      # val = PARENT["val"] (injected)
    START >> step >> END

# Sử dụng:
with GraphOp(name="main") as main:
    d = double_flow(val=PARENT["input"])  # d.name == "d"
    START >> d >> END
```

### Implementation

```python
def graph(fn):
    sig = inspect.signature(fn)
    param_names = set(sig.parameters.keys())

    @wraps(fn)
    def wrapper(**kwargs):
        # 1. Tách input mappings và init kwargs (name, outputs, ...)
        input_mappings, init_kwargs = split_shorthand_kwargs(kwargs)

        # 2. Tạo GraphOp với inputs
        op = GraphOp(inputs=input_mappings or None, **init_kwargs)

        # 3. Chạy builder function trong context
        # __exit__ tự động gọi _setup_schema() -> outputs được populate
        with op:
            parent_refs = {key: PARENT[key] for key in input_mappings if key in param_names}
            fn(**parent_refs)

        return op

    register_skip(wrapper)  # auto-naming skip qua wrapper
    wrapper.__wrapped__ = fn
    return wrapper
```

### `__exit__` và `_setup_schema()`

`GraphOp.__exit__` tự động gọi `_setup_schema()` khi thoát context manager. Điều này đảm bảo `op.outputs` được populate trước khi op được dùng trong parent graph (`op >> END` auto-forwarding).

### split_shorthand_kwargs

`wrapper` dùng `split_shorthand_kwargs(kwargs)` để tách:
- **Init kwargs**: `name`, `outputs`, `description`, ... -> truyền vào `GraphOp(**init_kwargs)`
- **Input mappings**: tất cả còn lại -> truyền vào `inputs=input_mappings`

### param_names Filtering

Chỉ các key nằm trong function signature mới được inject thành PARENT refs:

```python
@graph
def static_flow():       # Không nhận params
    step = double(x=PARENT["val"])
    START >> step >> END

g = static_flow(val=10)  # val chỉ là input mapping, không inject vào fn
```

## Debug

```python
def show(self, indent=0):
    """In cấu trúc graph (debug)."""
    prefix = "  " * indent
    print(f"{prefix}Graph: {self.name}")
    print(f"{prefix}Ops:", list(self._ops.keys()))
    print(f"{prefix}Edges:")
    for edge in self._edges:
        soft_marker = " (soft)" if edge.soft else ""
        print(f"{prefix}  {edge.from_op} -> {edge.to_op}{soft_marker}")
    print(f"{prefix}Ready count:", dict(self.ready_count))

    # Recursively show nested graphs
    for op in self._ops.values():
        if isinstance(op, GraphOp):
            op.show(indent + 1)
```
