# Graph Compilation Process

## Overview

Graph compilation xảy ra trong 2 phases:
1. `graph.build()` - Build graph structure
2. `StateSchema(graph)` - Build state schema

## Phase 1: Graph Build

### GraphNode.build()

```python
def build(self):
    # 1. Build tất cả child nodes trước (recursive)
    for node in self._nodes.values():
        if hasattr(node, 'build'):
            node.build()

    # 2. Setup inputs/outputs schema từ child nodes
    self._setup_schema()

    # 3. Xác định flow type của mỗi node
    self._build_flow_type()

    # 4. Setup entry/exit endpoints
    self._setup_endpoints()

    # 5. Tính ready_count
    self._compute_ready_counts()

    self._is_building = False
    self._post_build()
```

### _setup_schema()

Scan child nodes để tìm PARENT refs:

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

### _build_flow_type()

Xác định pattern của mỗi node:

```python
def _build_flow_type(self):
    for name, node in self._nodes.items():
        prev_count = len(self.prevs[name])
        next_count = len(self.nexts[name])

        if node.type == "branch":
            flow_type = "BRANCH"
        elif prev_count > 1 and next_count > 1:
            flow_type = "BLOOM"
        elif prev_count > 1:
            flow_type = "MERGE"
        elif next_count > 1:
            flow_type = "FORK"
        else:
            flow_type = "NORMAL"

        self.flowtype_map[name] = flow_type
```

### _setup_endpoints()

```python
def _setup_endpoints(self):
    # Entry nodes: không có predecessor
    if not self.entries:
        self.entries = [n for n in self._nodes if not self.prevs[n]]

    # Exit nodes: không có successor
    if not self.exits:
        self.exits = [n for n in self._nodes if not self.nexts[n]]

    # Validate
    if not self.entries:
        raise ValueError("Graph phải có ít nhất một entry node")
    if not self.exits:
        raise ValueError("Graph phải có ít nhất một exit node")
```

### Ready Count Calculation

```python
# Hard edges: đếm từng predecessor
# Soft edges: đếm chung tất cả soft predecessors là 1

self.ready_count = {}
for name in self._nodes:
    hard_pred_count = 0
    has_soft = False

    for pred in self.prevs[name]:
        edge = self._edges_lookup.get((pred, name))
        if edge and edge.soft:
            has_soft = True
        else:
            hard_pred_count += 1

    if has_soft:
        self.has_soft_preds.add(name)
        hard_pred_count += 1  # Soft group counts as 1

    self.ready_count[name] = hard_pred_count
```

## Phase 2: Schema Build

### StateSchema.__init__()

```python
def __init__(self, node=None):
    self._var_to_idx = {}
    self._defaults = []
    self._pull_refs = []
    self._push_refs = []

    if node:
        self._load_from(node)  # Collect variables
        self._build()          # Resolve refs
```

### _load_from() - Recursive Collection

```python
def _load_from(self, node):
    node_name = node.full_name

    # Register input variables
    for var_name, param in node.inputs.items():
        self._register(node_name, var_name, param.value or param.default)

    # Register output variables
    for var_name, param in node.outputs.items():
        if isinstance(param.value, Ref):
            self._register_push_ref(node_name, var_name, param.value)
        else:
            self._register(node_name, var_name, param.default)

    # Register metadata
    for meta_var in ("start_time", "end_time", "error"):
        self._register(node_name, meta_var, None)

    # Recurse into child nodes
    if hasattr(node, '_nodes'):
        for child in node._nodes.values():
            self._load_from(child)
```

### _build() - Ref Resolution

```python
def _build(self):
    for key, idx in self._var_to_idx.items():
        value = self._defaults[idx]

        # Resolve pull refs
        if isinstance(value, Ref):
            source_key = (value.node, value.var)
            source_idx = self._var_to_idx.get(source_key, -1)
            value.idx = source_idx
            self._pull_refs[idx] = value
            self._defaults[idx] = None

        # Resolve push refs
        push_ref = self._push_refs[idx]
        if push_ref:
            target_key = (push_ref.node, push_ref.var)
            target_idx = self._var_to_idx.get(target_key, -1)
            push_ref.idx = target_idx
```

## Compilation Output

### Graph Structure

```
graph._nodes: {name: BaseNode}
graph._edges: [EdgeConfig]
graph._edges_lookup: {(src, dst): EdgeConfig}
graph.prevs: {name: [predecessors]}
graph.nexts: {name: [successors]}
graph.entries: [entry_names]
graph.exits: [exit_names]
graph.ready_count: {name: int}
```

### State Structure

```
schema._var_to_idx: {(node, var): idx}
schema._defaults: [default_values]
schema._pull_refs: [Ref with resolved idx]
schema._push_refs: [Ref with resolved idx]
```

## Nested Graph Compilation

```python
with GraphNode(name="outer") as outer:
    with GraphNode(name="inner") as inner:
        a = CodeNode(name="a", ...)
        START >> a >> END

    b = CodeNode(name="b", ...)
    START >> inner >> b >> END

# Build order:
# 1. inner.build() (recursive from outer.build())
#    - inner._setup_schema()
#    - inner.ready_count computed
# 2. outer.build()
#    - outer._setup_schema()
#    - outer.ready_count computed

# Schema build order:
# 1. schema._load_from(outer)
#    - Register outer variables
#    - Register inner variables (recursive)
#    - Register inner.a variables
# 2. schema._build() - resolve all refs
```
