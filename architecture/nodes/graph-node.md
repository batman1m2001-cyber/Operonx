# GraphNode - Nested Graphs & Scoping

## Overview

`GraphNode` là container node chứa một subgraph các nodes. Nó cho phép tổ chức workflow thành các module có thể tái sử dụng.

Location: `hush-core/hush/core/nodes/graph/graph_node.py`

## Class Definition

```python
class GraphNode(BaseNode):
    type: NodeType = "graph"

    __slots__ = [
        '_token',           # Context token để restore
        '_nodes',           # Dict[str, BaseNode] - child nodes
        'entries',          # List entry node names
        'exits',            # List exit node names
        'prevs',            # Dict[node_name, List[predecessor_names]]
        'nexts',            # Dict[node_name, List[successor_names]]
        'ready_count',      # Dict[node_name, int] - số predecessors cần chờ
        'has_soft_preds',   # Set các nodes có soft predecessor
        'flowtype_map',     # BiMap[node_name, NodeFlowType]
        '_edges',           # List[EdgeConfig]
        '_edges_lookup',    # Dict[(source, target), EdgeConfig]
        '_is_building'      # Flag đang trong quá trình build
    ]
```

## Context Manager

GraphNode sử dụng context manager để tự động đăng ký child nodes:

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
with GraphNode(name="my_graph") as graph:
    # Tất cả nodes tạo trong block này tự động đăng ký vào graph
    node_a = CodeNode(name="a", ...)
    node_b = CodeNode(name="b", ...)
    START >> node_a >> node_b >> END
```

## Node & Edge Management

### add_node()

```python
def add_node(self, node: BaseNode) -> BaseNode:
    """Thêm node vào graph."""
    if not self._is_building:
        raise RuntimeError("Không thể thêm node sau khi graph đã build")

    if node in [START, END]:
        return node

    self._nodes[node.name] = node

    # Track start/end nodes
    if node.start:
        self.entries.append(node.name)
    if node.end:
        self.exits.append(node.name)

    return node
```

### add_edge()

```python
def add_edge(self, source: str, target: str, type: EdgeType = "normal", soft: bool = False):
    """Thêm edge giữa hai nodes."""
    if not self._is_building:
        raise RuntimeError("Không thể thêm edge sau khi graph đã build")

    # Handle START edge
    if source == START.name:
        self._nodes[target].start = True
        self.entries.append(target)
        return

    # Handle END edge
    if target == END.name:
        self._nodes[source].end = True
        self.exits.append(source)
        return

    # Normal edge
    new_edge = EdgeConfig(from_node=source, to_node=target, type=type, soft=soft)
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
    # 1. Build tất cả child nodes trước
    for node in self._nodes.values():
        if hasattr(node, 'build'):
            node.build()

    # 2. Setup schema từ child nodes
    self._setup_schema()

    # 3. Xác định flow type của mỗi node
    self._build_flow_type()

    # 4. Setup entry/exit endpoints
    self._setup_endpoints()

    # 5. Tính ready_count cho mỗi node
    self._compute_ready_counts()

    self._is_building = False
    self._post_build()
```

### _setup_schema()

Scan child nodes để tìm PARENT refs - đó chính là inputs/outputs của graph:

```python
def _setup_schema(self):
    graph_inputs = {}
    graph_outputs = {}

    for _, node in self._nodes.items():
        # Input refs đến PARENT → graph input
        for var, param in node.inputs.items():
            if isinstance(param.value, Ref) and param.value.raw_node is self:
                graph_inputs[param.value.var] = Param(...)

        # Output refs đến PARENT → graph output
        for var, param in node.outputs.items():
            if isinstance(param.value, Ref) and param.value.raw_node is self:
                graph_outputs[param.value.var] = Param(...)

    self.inputs = self._merge_params(graph_inputs, self.inputs)
    self.outputs = self._merge_params(graph_outputs, self.outputs)
```

### Ready Count

```python
# Hard edges: đếm từng predecessor
# Soft edges: đếm chung tất cả soft predecessors là 1

ready_count = {}
for name in self._nodes:
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

    # 2. Khởi tạo tasks cho entry nodes
    active_tasks = {}
    ready_count = self.ready_count.copy()
    soft_satisfied = set()

    for entry in self.entries:
        task = asyncio.create_task(
            name=entry,
            coro=self._nodes[entry].run(state, context_id, parent_context)
        )
        active_tasks[entry] = task

    # 3. Execute loop
    while active_tasks:
        done_tasks, _ = await asyncio.wait(
            active_tasks.values(),
            return_when=asyncio.FIRST_COMPLETED
        )

        for task in done_tasks:
            node_name = task.get_name()
            active_tasks.pop(node_name)
            node = self._nodes[node_name]

            # Xác định next nodes (branch có logic riêng)
            if node.type == "branch":
                branch_target = node.get_target(state, context_id)
                next_nodes = [branch_target] if branch_target != END.name else []
            else:
                next_nodes = self.nexts[node_name]

            # Update ready counts và schedule next nodes
            for next_node in next_nodes:
                edge = self._edges_lookup.get((node_name, next_node))
                is_soft = edge and edge.soft

                if is_soft:
                    if next_node in soft_satisfied:
                        continue  # Đã có soft pred hoàn thành
                    soft_satisfied.add(next_node)

                ready_count[next_node] -= 1

                if ready_count[next_node] == 0:
                    task = asyncio.create_task(
                        name=next_node,
                        coro=self._nodes[next_node].run(state, context_id, parent_context)
                    )
                    active_tasks[next_node] = task

    # 4. Collect outputs
    _outputs = self.get_outputs(state, context_id, parent_context)
    self.store_result(state, _outputs, context_id)
    return _outputs
```

## Flow Types

```python
NodeFlowType = Literal["MERGE", "FORK", "BLOOM", "BRANCH", "NORMAL", "OTHER"]

# MERGE: nhiều inputs, 1 output (prev > 1, next = 1)
# FORK: 1 input, nhiều outputs (prev = 1, next > 1)
# BLOOM: nhiều inputs, nhiều outputs (prev > 1, next > 1)
# BRANCH: BranchNode
# NORMAL: 1 input, 1 output
# OTHER: entry/exit nodes
```

## Scoping

### Nested Graphs

GraphNode có thể nest trong GraphNode khác:

```python
with GraphNode(name="outer") as outer:
    with GraphNode(name="inner") as inner:
        a = CodeNode(name="a", ...)
        START >> a >> END

    b = CodeNode(name="b", ...)
    START >> inner >> b >> END

# Node paths:
# - outer.inner.a
# - outer.b
```

### PARENT Reference

Nodes trong nested graph truy cập parent qua PARENT:

```python
with GraphNode(name="outer", inputs={"data": some_source}) as outer:
    with GraphNode(name="inner") as inner:
        process = CodeNode(
            name="process",
            inputs={"x": PARENT["data"]}  # Lấy từ inner graph
        )
        START >> process >> END

    # inner graph nhận data từ outer
    inner_node = inner  # inner graph như một node
    inner_node.inputs = {"data": PARENT["data"]}  # Từ outer graph
```

## Debug

```python
def show(self, indent=0):
    """In cấu trúc graph (debug)."""
    prefix = "  " * indent
    print(f"{prefix}Graph: {self.name}")
    print(f"{prefix}Nodes:", list(self._nodes.keys()))
    print(f"{prefix}Edges:")
    for edge in self._edges:
        soft_marker = " (soft)" if edge.soft else ""
        print(f"{prefix}  {edge.from_node} -> {edge.to_node}{soft_marker}")
    print(f"{prefix}Ready count:", dict(self.ready_count))

    # Recursively show nested graphs
    for node in self._nodes.values():
        if isinstance(node, GraphNode):
            node.show(indent + 1)
```
