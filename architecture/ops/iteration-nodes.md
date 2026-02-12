# Iteration Ops - ForLoop, Map, While Internals

## Overview

Iteration ops cho phep lap qua collections trong workflow. Tat ca deu ke thua tu `BaseIterationOp`.

Location: `hush-core/hush/core/ops/iteration/`

## BaseIterationOp

Base class chung cho tat ca iteration ops, ke thua tu GraphOp.

```python
class BaseIterationOp(GraphOp):
    __slots__ = [
        '_each',              # Dict[var_name, source] - variables to iterate
        '_broadcast_inputs',  # Dict[var_name, value] - same value for all iterations
        '_raw_inputs',        # Original inputs from constructor
        '_raw_outputs',       # Original outputs from constructor
        '_var_indices'        # Pre-computed indices for performance
    ]
```

### Each() Marker

`Each()` danh dau input se duoc iterate:

```python
class Each:
    """Marker de danh dau iteration source."""
    def __init__(self, source: Any):
        self.source = source

# Usage
inputs = {
    "item": Each(items_op["items"]),  # Iterate qua list nay
    "multiplier": PARENT["multiplier"]   # Broadcast - gia tri giong nhau cho moi iteration
}
```

### Input Separation

Constructor tu dong tach Each() tu broadcast:

```python
def __init__(self, inputs=None, **kwargs):
    self._each = {}
    self._broadcast_inputs = {}

    for var_name, value in (inputs or {}).items():
        if isinstance(value, Each):
            self._each[var_name] = value.source
        else:
            self._broadcast_inputs[var_name] = value
```

### Build Iteration Data

```python
def _build_iteration_data(self, each_values, broadcast_values) -> List[Dict]:
    """Tao list data cho moi iteration."""
    # Validate lengths match
    lengths = {var: len(lst) for var, lst in each_values.items()}
    if len(set(lengths.values())) > 1:
        raise ValueError(f"All 'each' variables must have same length: {lengths}")

    # Build result
    n = next(iter(lengths.values()))
    result = [{**broadcast_values} for _ in range(n)]

    for i, vals in enumerate(zip(*each_values.values())):
        for j, key in enumerate(each_values.keys()):
            result[i][key] = vals[j]

    return result
```

### Context ID

```python
# Pre-computed suffixes cho performance
_CTX_SUFFIXES = tuple("[" + str(i) + "]" for i in range(1000))

def get_iter_context(prefix: str, i: int) -> str:
    """Tao context ID cho iteration."""
    if i < 1000:
        return prefix + _CTX_SUFFIXES[i]
    return prefix + "[" + str(i) + "]"

# Vi du: "main.[0]", "main.[1]", ...
```

---

## ForOp

**Sequential iteration** - xu ly tung item mot, theo thu tu.

```python
class ForOp(BaseIterationOp):
    type: OpType = "for"
```

### Khi nao dung

- Iterations co the phu thuoc vao ket qua cua iteration truoc
- Can thu tu thuc thi nhat quan
- Memory constraints - chi xu ly 1 item tai mot thoi diem

### Example

```python
with ForOp(
    name="process_loop",
    inputs={
        "x": Each([1, 2, 3]),       # iterate
        "multiplier": 10             # broadcast
    }
) as loop:
    calc = FuncOp(
        name="calc",
        code_fn=lambda x, multiplier: {"result": x * multiplier},
        inputs={"x": PARENT["x"], "multiplier": PARENT["multiplier"]},
        outputs={"result": PARENT}
    )
    START >> calc >> END

# Results: [10, 20, 30]
```

### Execution Flow

```python
async def run(self, state, context_id=None, parent_context=None):
    # Resolve values
    each_values = self._resolve_values(self._each, state, parent_context)
    broadcast_values = self._resolve_values(self._broadcast_inputs, state, parent_context)
    iteration_data = self._build_iteration_data(each_values, broadcast_values)

    final_results = []
    ctx_prefix = (context_id + ".") if context_id else ""

    # Execute SEQUENTIALLY
    for i, loop_data in enumerate(iteration_data):
        iter_context = get_iter_context(ctx_prefix, i)

        # Set iteration variables
        for var_name, value in loop_data.items():
            state[self.full_name, var_name, iter_context] = value

        # Run child graph
        result = await self._run_graph(state, iter_context, iter_context)
        final_results.append(result)

    # Transpose to column format
    output_keys = [k for k in self.outputs.keys() if k != "iteration_metrics"]
    outputs = {key: [r.get(key) for r in final_results] for key in output_keys}
    outputs["iteration_metrics"] = {
        "total_iterations": len(iteration_data),
        "success_count": success_count,
        "error_count": error_count
    }
    return outputs
```

---

## MapOp

**Parallel iteration** - xu ly tat ca items dong thoi voi concurrency limit.

```python
class MapOp(BaseIterationOp):
    type: OpType = "map"

    __slots__ = ['_max_concurrency']

    def __init__(self, inputs=None, max_concurrency=None, **kwargs):
        super().__init__(inputs=inputs, **kwargs)
        self._max_concurrency = max_concurrency or os.cpu_count()
```

### Khi nao dung

- Items doc lap, co the xu ly song song
- Thu tu thuc thi khong quan trong (ket qua van theo thu tu)
- I/O-bound operations can throughput cao

### Example

```python
with MapOp(
    name="process_map",
    inputs={
        "url": Each(urls),           # iterate
        "timeout": 30                 # broadcast
    },
    max_concurrency=10
) as map_op:
    fetch = FuncOp(
        name="fetch",
        code_fn=fetch_url,
        inputs={"url": PARENT["url"], "timeout": PARENT["timeout"]},
        outputs={"content": PARENT}
    )
    START >> fetch >> END
```

### Execution Flow

```python
async def run(self, state, context_id=None, parent_context=None):
    each_values = self._resolve_values(self._each, state, parent_context)
    broadcast_values = self._resolve_values(self._broadcast_inputs, state, parent_context)
    iteration_data = self._build_iteration_data(each_values, broadcast_values)

    # Semaphore de limit concurrency
    semaphore = asyncio.Semaphore(self._max_concurrency)

    async def execute_iteration(iter_context, loop_data):
        async with semaphore:  # Limit concurrent executions
            for var_name, value in loop_data.items():
                state[self.full_name, var_name, iter_context] = value
            return await self._run_graph(state, iter_context, iter_context)

    ctx_prefix = (context_id + ".") if context_id else ""

    # Execute ALL in parallel
    raw_results = await asyncio.gather(*[
        execute_iteration(get_iter_context(ctx_prefix, i), data)
        for i, data in enumerate(iteration_data)
    ])

    # Collect results (da theo dung thu tu nho asyncio.gather)
    return self._format_outputs(raw_results)
```

---

## WhileOp

**Conditional iteration** - lap cho den khi dieu kien thoa man.

```python
class WhileOp(BaseIterationOp):
    type: OpType = "while"

    __slots__ = ['_stop_condition', '_max_iterations']

    def __init__(
        self,
        stop_condition: str,
        max_iterations: int = 100,
        **kwargs
    ):
        self._stop_condition = stop_condition
        self._max_iterations = max_iterations
```

### Khi nao dung

- So lan lap khong biet truoc
- Can dung khi dat dieu kien
- Agentic workflows voi iteration loops

### Example

```python
with WhileOp(
    name="retry_loop",
    stop_condition="success == True or attempts >= 3",
    max_iterations=10,
    inputs={"attempts": 0}
) as while_op:
    attempt = FuncOp(
        name="attempt",
        code_fn=try_operation,
        inputs={"attempts": PARENT["attempts"]},
        outputs={"success": PARENT, "attempts": PARENT}
    )
    START >> attempt >> END
```

### Execution Flow

```python
async def run(self, state, context_id=None, parent_context=None):
    iteration = 0
    ctx_prefix = (context_id + ".") if context_id else ""

    while iteration < self._max_iterations:
        iter_context = get_iter_context(ctx_prefix, iteration)

        # Run iteration
        result = await self._run_graph(state, iter_context, parent_context)

        # Check stop condition
        if self._evaluate_condition(result):
            break

        iteration += 1

    return result
```

---

## AIterOp

**Async iteration** - xu ly async iterator/generator.

```python
class AIterOp(BaseIterationOp):
    type: OpType = "async_iter"
```

### Khi nao dung

- Streaming data tu external source
- Data khong available toan bo ngay tu dau
- Event-driven processing

---

## Output Format

Tat ca iteration ops output theo dang column-oriented:

```python
# Input data: [{"x": 1}, {"x": 2}, {"x": 3}]
# Results: [{"result": 10}, {"result": 20}, {"result": 30}]

# Output format (column-oriented):
{
    "result": [10, 20, 30],
    "iteration_metrics": {
        "total_iterations": 3,
        "success_count": 3,
        "error_count": 0
    }
}
```

## Error Handling

Errors trong iteration duoc capture, khong crash toan bo loop:

```python
for i, loop_data in enumerate(iteration_data):
    try:
        result = await self._run_graph(state, iter_context, iter_context)
        final_results.append(result)
        success_count += 1
    except Exception as e:
        final_results.append({"error": str(e), "error_type": type(e).__name__})
        # Tiep tuc voi iteration tiep theo

# Log warning neu error rate > 10%
if error_count / len(iteration_data) > 0.1:
    LOGGER.warning("High error rate: %.1f%%", error_count / len(iteration_data) * 100)
```
