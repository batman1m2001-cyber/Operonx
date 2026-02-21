# Op Scheduling & Dependency Resolution

## Overview

Hush sử dụng topological-order scheduling với parallel execution cho independent ops.

## Ready Count

### Concept

Mỗi op có `ready_count` = số predecessors cần chờ hoàn thành.

```python
# Example graph
START >> A >> [B, C]
[B, C] >> D >> END

# Ready counts:
A: 0  (entry op)
B: 1  (waits for A)
C: 1  (waits for A)
D: 2  (waits for B AND C)
```

### Hard vs Soft Edges

```python
# Hard edge (>>): đếm từng predecessor
A >> B  # B.ready_count += 1

# Soft edge (>>~): tất cả soft predecessors đếm chung là 1
A >> ~D
B >> ~D
C >> ~D
# D.ready_count = 1 (chỉ cần 1 trong A,B,C hoàn thành)
```

### Calculation

```python
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

    if has_soft:
        hard_pred_count += 1  # Soft group = 1

    ready_count[name] = hard_pred_count
```

## Execution Loop

### GraphOp.run()

```python
async def run(self, state, context_id=None, parent_context=None):
    active_tasks = {}
    ready_count = self.ready_count.copy()
    soft_satisfied = set()

    # 1. Start entry ops
    for entry in self.entries:
        task = asyncio.create_task(
            name=entry,
            coro=self._ops[entry].run(state, context_id, parent_context)
        )
        active_tasks[entry] = task

    # 2. Process completed tasks
    while active_tasks:
        done_tasks, _ = await asyncio.wait(
            active_tasks.values(),
            return_when=asyncio.FIRST_COMPLETED
        )

        for task in done_tasks:
            op_name = task.get_name()
            active_tasks.pop(op_name)
            op = self._ops[op_name]

            # Determine next ops
            if op.type == "branch":
                branch_target = op.get_target(state, context_id)
                next_ops = [branch_target] if branch_target != END.name else []
            else:
                next_ops = self.nexts[op_name]

            # Update ready counts and schedule
            for next_op in next_ops:
                edge = self._edges_lookup.get((op_name, next_op))
                is_soft = edge and edge.soft

                if is_soft:
                    if next_op in soft_satisfied:
                        continue  # Already satisfied
                    soft_satisfied.add(next_op)

                ready_count[next_op] -= 1

                if ready_count[next_op] == 0:
                    task = asyncio.create_task(
                        name=next_op,
                        coro=self._ops[next_op].run(state, context_id, parent_context)
                    )
                    active_tasks[next_op] = task

    return self.get_outputs(state, context_id, parent_context)
```

## Execution Patterns

### Sequential

```
START >> A >> B >> C >> END

Execution order:
1. A (ready_count=0)
2. B (ready_count=0 after A)
3. C (ready_count=0 after B)
```

### Parallel Fork

```
START >> A >> [B, C, D] >> E >> END

Execution order:
1. A
2. B, C, D (parallel, ready_count=0 after A)
3. E (ready_count=0 after all of B,C,D)
```

### Branch

```
START >> branch >> ~case_a >> merge >> END
         branch >> ~case_b >> merge

Execution order (if branch -> case_a):
1. branch
2. case_a (soft edge, ready_count=1)
3. merge (ready_count=0 after case_a satisfies soft group)
   - case_b không chạy
```

### Diamond

```
START >> A >> [B, C] >> D >> END

Execution order:
1. A
2. B, C (parallel)
3. D (after BOTH B and C)
```

## Branch Handling

### BranchOp Returns Target

```python
# BranchOp.core returns
{"target": "case_a", "matched": "score >= 90"}
```

### GraphOp Uses Target

```python
if op.type == "branch":
    branch_target = op.get_target(state, context_id)
    if branch_target != END.name:
        next_ops = [branch_target]  # Chỉ 1 target
    else:
        next_ops = []
else:
    next_ops = self.nexts[op_name]  # Tất cả successors
```

## Soft Edge Handling

### Purpose

Soft edges dùng cho merge sau branch - chỉ cần 1 predecessor hoàn thành:

```python
# Branch outputs use soft edges
branch >> ~case_a >> merge
branch >> ~case_b >> merge
# merge chờ BẤT KỲ MỘT trong case_a, case_b
```

### Tracking

```python
soft_satisfied = set()

for next_op in next_ops:
    edge = self._edges_lookup.get((op_name, next_op))
    is_soft = edge and edge.soft

    if is_soft:
        if next_op in soft_satisfied:
            continue  # Đã có soft predecessor hoàn thành
        soft_satisfied.add(next_op)  # Mark as satisfied

    ready_count[next_op] -= 1
```

## Error Handling

Errors trong op không stop graph execution:

```python
# In BaseOp.run()
try:
    _outputs = await self.core(**_inputs)
except Exception as e:
    state[self.full_name, "error", context_id] = traceback.format_exc()
    # Op vẫn "hoàn thành", successors có thể chạy
```

## Rust Mode Scheduling (rush-core)

Rush-core (`rush-core/src/ops/graph/graph_op.rs`) implements the same scheduling algorithm in Rust with two key differences:

### 1. Synchronous Execution

Rust mode runs everything synchronously (no asyncio). The queue-based scheduler pops ops one at a time:

```rust
while !queue.is_empty() {
    let batch: Vec<String> = queue.drain(..).collect();
    // Execute batch, then activate successors
}
```

### 2. Batch-Aware Parallel Execution

When multiple ops are ready simultaneously, the scheduler checks if parallel execution would benefit:

```
batch = [A, B, C]  (all ready_count == 0)
│
├── All pure Python ops?  → Sequential (one at a time, GIL blocks parallelism)
│
└── Any rust_op present?  → Parallel via rayon thread pool
                            py.allow_threads(|| {
                              batch.par_iter().for_each(|op| {
                                Python::with_gil(|py| execute(py, op))
                              })
                            })
```

**Heuristic**: `batch.len() > 1 AND any op has rust_op`. Pure Python batches execute sequentially to avoid `allow_threads`/`with_gil` overhead.

### 3. Concurrent State (DashMap)

Rust mode uses `DashMap<(String, String, String), PyObject>` for the state store, enabling lock-free concurrent reads/writes from rayon worker threads. Tags and execution order use `Mutex<Vec>`.

All state mutation methods take `&self` (not `&mut self`), allowing shared access across threads.

### Performance

Rust mode achieves 2-6x speedup over Python mode for typical workflows (sync ops). The speedup comes from:
- No asyncio overhead (no task creation, no event loop yields)
- Rust scheduling loop (no Python interpreter overhead)
- Pre-compiled config (no Python dict lookups per op)

See `rush-core/CLAUDE.md` for detailed benchmark results.

## Iteration Op Scheduling

Iteration ops tự quản lý scheduling cho child graph:

```python
# ForOp - sequential
for i, data in enumerate(iteration_data):
    result = await self._run_graph(state, f"[{i}]", ...)

# MapOp - parallel với semaphore
semaphore = asyncio.Semaphore(max_concurrency)
await asyncio.gather(*[
    execute_iteration(f"[{i}]", data)
    for i, data in enumerate(iteration_data)
])
```
