# Graph Compilation Process

## Overview

Graph compilation xay ra trong 2 phases:
1. `graph.build()` - Build graph structure
2. `StateSchema(graph)` - Build state schema

## Phase 1: Graph Build

### GraphOp.build()

```python
def build(self):
    # 1. Build tat ca child ops truoc (recursive)
    for op in self._ops.values():
        if hasattr(op, 'build'):
            op.build()

    # 2. Setup inputs/outputs schema tu child ops
    self._setup_schema()

    # 3. Xac dinh flow type cua moi op
    self._build_flow_type()

    # 4. Setup entry/exit endpoints
    self._setup_endpoints()

    # 5. Tinh ready_count
    self._compute_ready_counts()

    self._is_building = False
    self._post_build()
```

### _setup_schema()

Scan child ops de tim PARENT refs:

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

### _build_flow_type()

Xac dinh pattern cua moi op:

```python
def _build_flow_type(self):
    for name, op in self._ops.items():
        prev_count = len(self.prevs[name])
        next_count = len(self.nexts[name])

        if op.type == "branch":
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
    # Entry ops: khong co predecessor
    if not self.entries:
        self.entries = [n for n in self._ops if not self.prevs[n]]

    # Exit ops: khong co successor
    if not self.exits:
        self.exits = [n for n in self._ops if not self.nexts[n]]

    # Validate
    if not self.entries:
        raise ValueError("Graph phai co it nhat mot entry op")
    if not self.exits:
        raise ValueError("Graph phai co it nhat mot exit op")
```

### Ready Count Calculation

```python
# Hard edges: dem tung predecessor
# Soft edges: dem chung tat ca soft predecessors la 1

self.ready_count = {}
for name in self._ops:
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
def __init__(self, op=None):
    self._var_to_idx = {}
    self._defaults = []
    self._pull_refs = []
    self._push_refs = []

    if op:
        self._load_from(op)  # Collect variables
        self._build()          # Resolve refs
```

### _load_from() - Recursive Collection

```python
def _load_from(self, op):
    op_name = op.full_name

    # Register input variables
    for var_name, param in op.inputs.items():
        self._register(op_name, var_name, param.value or param.default)

    # Register output variables
    for var_name, param in op.outputs.items():
        if isinstance(param.value, Ref):
            self._register_push_ref(op_name, var_name, param.value)
        else:
            self._register(op_name, var_name, param.default)

    # Register metadata
    for meta_var in ("start_time", "end_time", "error"):
        self._register(op_name, meta_var, None)

    # Recurse into child ops
    if hasattr(op, '_ops'):
        for child in op._ops.values():
            self._load_from(child)
```

### _build() - Ref Resolution

```python
def _build(self):
    for key, idx in self._var_to_idx.items():
        value = self._defaults[idx]

        # Resolve pull refs
        if isinstance(value, Ref):
            source_key = (value.source, value.var)
            source_idx = self._var_to_idx.get(source_key, -1)
            value.idx = source_idx
            self._pull_refs[idx] = value
            self._defaults[idx] = None

        # Resolve push refs
        push_ref = self._push_refs[idx]
        if push_ref:
            target_key = (push_ref.source, push_ref.var)
            target_idx = self._var_to_idx.get(target_key, -1)
            push_ref.idx = target_idx
```

## Compilation Output

### Graph Structure

```
graph._ops: {name: BaseOp}
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
schema._var_to_idx: {(op, var): idx}
schema._defaults: [default_values]
schema._pull_refs: [Ref with resolved idx]
schema._push_refs: [Ref with resolved idx]
```

## Nested Graph Compilation

```python
with GraphOp(name="outer") as outer:
    with GraphOp(name="inner") as inner:
        a = FuncOp(name="a", ...)
        START >> a >> END

    b = FuncOp(name="b", ...)
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
