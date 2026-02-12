# Data Flow Through Ops

## Overview

Data flows qua ops thong qua Pull va Push refs. Document nay giai thich cach data di chuyen trong workflow.

## Ref Class

```python
class Ref:
    _source: Union[BaseOp, str]  # Source op
    var: str                      # Source variable name
    idx: int                      # Resolved storage index
    _ops: List[Tuple]            # Chained operations
    _fn: Callable                # Compiled transform function
    is_output: bool              # True for output refs
```

## Pull Refs (Input)

### Definition

```python
processor = FuncOp(
    name="processor",
    inputs={
        "data": PARENT["input"],           # Pull from PARENT.input
        "config": other_op["result"],      # Pull from other_op.result
    }
)
```

### Execution Flow

```
1. Schema build:
   - Detect Ref in inputs
   - Resolve source index
   - Store in _pull_refs

2. Runtime (node reads input):
   state[processor, data, ctx]
     ↓
   Check cache → not found
     ↓
   Check pull_ref → found, idx=5
     ↓
   Read source: _cells[5][ctx]
     ↓
   Apply _fn (transforms)
     ↓
   Cache result → return
```

### Transform Chain

```python
# Ref với operations
PARENT["data"]["key"].upper()

# Compiled function
_fn = lambda x: x["key"].upper()

# Execution
source_value = {"key": "hello"}
result = _fn(source_value)  # "HELLO"
```

## Push Refs (Output)

### Definition

```python
processor = FuncOp(
    name="processor",
    outputs={
        "result": PARENT,              # Push to PARENT.result
        "status": consumer["input"],   # Push to consumer.input
    }
)
```

### Execution Flow

```
1. Schema build:
   - Detect Ref in outputs
   - Resolve target index
   - Store in _push_refs

2. Runtime (node writes output):
   state[processor, result, ctx] = value
     ↓
   Write to local cell: _cells[idx][ctx] = value
     ↓
   Check push_ref → found, target_idx=10
     ↓
   Apply _fn (if any)
     ↓
   Push to target: _cells[10][ctx] = transformed_value
```

## Ref Operators

### Access

```python
PARENT["key"]              # getitem
PARENT["data"].name        # getattr
PARENT["func"](arg)        # call
```

### Arithmetic

```python
PARENT["x"] + 10           # add
PARENT["y"] * 2            # mul
PARENT["z"] / 5            # truediv
```

### Comparison

```python
PARENT["score"] >= 90      # ge
PARENT["status"] == "ok"   # eq
PARENT["count"] > 0        # gt
```

### Apply

```python
PARENT["items"].apply(len)              # len(items)
PARENT["text"].apply(str.split, ",")    # text.split(",")
```

## Single-hop Rule

Refs chỉ resolve 1 hop:

```
A.output → B.input → C.input  ❌ (2 hops)

A.output → B.input            ✓ (1 hop)
B.output → C.input            ✓ (1 hop)
```

Lý do:
- Predictable performance
- Easy debugging
- No circular dependency risks

## Context Propagation

### Normal Context

```python
# Same context for all ops in chain
state[op_a, result, "main"]
state[op_b, input, "main"]  # Pulls from op_a.result["main"]
```

### Iteration Context

```python
# Parent context → child context
state[loop, item, "[0]"]      # Parent sets item
state[child, input, "[0]"]    # Child reads from same context
state[child, result, "[0]"]   # Child writes to same context
```

### Context Resolution

```python
def get_inputs(self, state, context_id, parent_context=None):
    for var_name, param in self.inputs.items():
        # PARENT ref → use parent_context
        if parent_context and isinstance(param.value, Ref) and param.value.raw_source is self.father:
            lookup_ctx = parent_context
        else:
            # Sibling/other → use context_id
            lookup_ctx = context_id

        value = state[self.full_name, var_name, lookup_ctx]
```

## Output Mapping Syntax

### Using >> operator

```python
# Map output to PARENT
node["result"] >> PARENT["output"]

# Map output to another node
producer["data"] >> consumer["input"]
```

### Behind the scenes

```python
def __rshift__(self, other):
    # self = producer["output"]
    # other = PARENT["dest"] or consumer["input"]

    # Set producer.outputs[output].value = Ref(target, dest)
    source_op.outputs[self.var] = Param(value=Ref(target_op, other.var))
```

## Example Flow

```python
with GraphOp(name="workflow") as g:
    a = FuncOp(
        name="a",
        code_fn=lambda x: {"y": x * 2},
        inputs={"x": PARENT["input"]},
    )
    a["y"] >> PARENT["output"]

    START >> a >> END

# Data flow:
# 1. PARENT.input = 5 (initial)
# 2. a reads: pull PARENT.input → x = 5
# 3. a executes: y = 5 * 2 = 10
# 4. a writes: push a.y → PARENT.output = 10
```

## Debug

```python
# Show schema refs
schema.show()
# op.var [idx] <- pull source[src_idx] transforms
# op.var [idx] -> push target[tgt_idx]

# Show state values
state.show()
# op.var [ctx] = value
```
