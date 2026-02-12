# GraphOp - Nested Graphs & Scoping

## Overview

`GraphOp` la container op chua mot graph cac ops. No cho phep to chuc workflow thanh cac module co the tai su dung.

Location: `hush-core/hush/core/ops/graph/graph_op.py`

## Class Definition

```python
class GraphOp(BaseOp):
    type: OpType = "graph"

    __slots__ = [
        '_token',           # Context token de restore
        '_ops',             # Dict[str, BaseOp] - child ops
        'entries',          # List entry op names
        'exits',            # List exit op names
        'prevs',            # Dict[op_name, List[predecessor_names]]
        'nexts',            # Dict[op_name, List[successor_names]]
        'ready_count',      # Dict[op_name, int] - so predecessors can cho
        'has_soft_preds',   # Set cac ops co soft predecessor
        'flowtype_map',     # BiMap[op_name, OpFlowType]
        '_edges',           # List[EdgeConfig]
        '_edges_lookup',    # Dict[(source, target), EdgeConfig]
        '_is_building',     # Flag dang trong qua trinh build
        '_compiled_adj'     # Compiled adjacency data for fast execution
    ]
```

## Context Manager

GraphOp su dung context manager de tu dong dang ky child ops:

```python
def __enter__(self):
    """Vao context - set graph nay lam current."""
    self._token = _current_graph.set(self)
    return self

def __exit__(self, exc_type, exc_val, exc_tb):
    """Thoat context - restore graph truoc do."""
    _current_graph.reset(self._token)
```

Su dung:

```python
with GraphOp(name="my_graph") as graph:
    # Tat ca ops tao trong block nay tu dong dang ky vao graph
    op_a = FuncOp(name="a", ...)
    op_b = FuncOp(name="b", ...)
    START >> op_a >> op_b >> END
```

## Op & Edge Management

### add_op()

```python
def add_op(self, op: BaseOp) -> BaseOp:
    """Them op vao graph."""
    if not self._is_building:
        raise RuntimeError("Khong the them op sau khi graph da build")

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
    """Them edge giua hai ops."""
    if not self._is_building:
        raise RuntimeError("Khong the them edge sau khi graph da build")

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
    """Build graph - phai goi truoc khi execute."""
    # 1. Build tat ca child ops truoc
    for op in self._ops.values():
        if hasattr(op, 'build'):
            op.build()

    # 2. Setup schema tu child ops
    self._setup_schema()

    # 3. Xac dinh flow type cua moi op
    self._build_flow_type()

    # 4. Setup entry/exit endpoints
    self._setup_endpoints()

    # 5. Tinh ready_count cho moi op
    self._compute_ready_counts()

    self._is_building = False
    self._post_build()
```

### _setup_schema()

Scan child ops de tim PARENT refs - do chinh la inputs/outputs cua graph:

```python
def _setup_schema(self):
    graph_inputs = {}
    graph_outputs = {}

    for _, op in self._ops.items():
        # Input refs den PARENT -> graph input
        for var, param in op.inputs.items():
            if isinstance(param.value, Ref) and param.value.raw_source is self:
                graph_inputs[param.value.var] = Param(...)

        # Output refs den PARENT -> graph output
        for var, param in op.outputs.items():
            if isinstance(param.value, Ref) and param.value.raw_source is self:
                graph_outputs[param.value.var] = Param(...)

    self.inputs = self._merge_params(graph_inputs, self.inputs)
    self.outputs = self._merge_params(graph_outputs, self.outputs)
```

### Ready Count

```python
# Hard edges: dem tung predecessor
# Soft edges: dem chung tat ca soft predecessors la 1

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

    # Soft edges dem chung la 1
    if has_soft:
        self.has_soft_preds.add(name)
        hard_pred_count += 1

    ready_count[name] = hard_pred_count
```

## Execution

### run()

```python
async def run(self, state, context_id=None, parent_context=None):
    # 1. Lay inputs
    _inputs = self.get_inputs(state, context_id, parent_context)

    # 2. Khoi tao tasks cho entry ops
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

            # Xac dinh next ops (branch co logic rieng)
            if op.type == "branch":
                branch_target = op.get_target(state, context_id)
                next_ops = [branch_target] if branch_target != END.name else []
            else:
                next_ops = self.nexts[op_name]

            # Update ready counts va schedule next ops
            for next_op in next_ops:
                edge = self._edges_lookup.get((op_name, next_op))
                is_soft = edge and edge.soft

                if is_soft:
                    if next_op in soft_satisfied:
                        continue  # Da co soft pred hoan thanh
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

# MERGE: nhieu inputs, 1 output (prev > 1, next = 1)
# FORK: 1 input, nhieu outputs (prev = 1, next > 1)
# BLOOM: nhieu inputs, nhieu outputs (prev > 1, next > 1)
# BRANCH: BranchOp
# NORMAL: 1 input, 1 output
# OTHER: entry/exit ops
```

## Scoping

### Nested Graphs

GraphOp co the nest trong GraphOp khac:

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

Ops trong nested graph truy cap parent qua PARENT:

```python
with GraphOp(name="outer", inputs={"data": some_source}) as outer:
    with GraphOp(name="inner") as inner:
        process = FuncOp(
            name="process",
            inputs={"x": PARENT["data"]}  # Lay tu inner graph
        )
        START >> process >> END

    # inner graph nhan data tu outer
    inner_op = inner  # inner graph nhu mot op
    inner_op.inputs = {"data": PARENT["data"]}  # Tu outer graph
```

## @graph Decorator

`@graph` bien mot builder function thanh factory tao GraphOp co the tai su dung, ho tro auto-naming.

Location: cuoi `graph_op.py`, sau class `GraphOp`.

### Cach hoat dong

```python
from hush.core import graph, op, START, END, PARENT, GraphOp

@op
def double(x: int):
    return {"result": x * 2}

@graph
def double_flow(val):
    step = double(x=val)      # val = PARENT["val"] (injected)
    START >> step >> END

# Su dung:
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
        # 1. Tach input mappings va init kwargs (name, outputs, ...)
        input_mappings, init_kwargs = split_shorthand_kwargs(kwargs)

        # 2. Tao GraphOp voi inputs
        op = GraphOp(inputs=input_mappings or None, **init_kwargs)

        # 3. Chay builder function trong context
        # __exit__ tu dong goi _setup_schema() -> outputs duoc populate
        with op:
            parent_refs = {key: PARENT[key] for key in input_mappings if key in param_names}
            fn(**parent_refs)

        return op

    register_skip(wrapper)  # auto-naming skip qua wrapper
    wrapper.__wrapped__ = fn
    return wrapper
```

### `__exit__` va `_setup_schema()`

`GraphOp.__exit__` tu dong goi `_setup_schema()` khi thoat context manager. Dieu nay dam bao `op.outputs` duoc populate truoc khi op duoc dung trong parent graph (`op >> END` auto-forwarding).

### split_shorthand_kwargs

`wrapper` dung `split_shorthand_kwargs(kwargs)` de tach:
- **Init kwargs**: `name`, `outputs`, `description`, ... -> truyen vao `GraphOp(**init_kwargs)`
- **Input mappings**: tat ca con lai -> truyen vao `inputs=input_mappings`

### param_names Filtering

Chi cac key nam trong function signature moi duoc inject thanh PARENT refs:

```python
@graph
def static_flow():       # Khong nhan params
    step = double(x=PARENT["val"])
    START >> step >> END

g = static_flow(val=10)  # val chi la input mapping, khong inject vao fn
```

## Debug

```python
def show(self, indent=0):
    """In cau truc graph (debug)."""
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
