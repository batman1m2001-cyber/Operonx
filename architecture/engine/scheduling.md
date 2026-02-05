# Node Scheduling & Dependency Resolution

## Overview

Hush sử dụng topological-order scheduling với parallel execution cho independent nodes.

## Ready Count

### Concept

Mỗi node có `ready_count` = số predecessors cần chờ hoàn thành.

```python
# Example graph
START >> A >> [B, C]
[B, C] >> D >> END

# Ready counts:
A: 0  (entry node)
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
        hard_pred_count += 1  # Soft group = 1

    ready_count[name] = hard_pred_count
```

## Execution Loop

### GraphNode.run()

```python
async def run(self, state, context_id=None, parent_context=None):
    active_tasks = {}
    ready_count = self.ready_count.copy()
    soft_satisfied = set()

    # 1. Start entry nodes
    for entry in self.entries:
        task = asyncio.create_task(
            name=entry,
            coro=self._nodes[entry].run(state, context_id, parent_context)
        )
        active_tasks[entry] = task

    # 2. Process completed tasks
    while active_tasks:
        done_tasks, _ = await asyncio.wait(
            active_tasks.values(),
            return_when=asyncio.FIRST_COMPLETED
        )

        for task in done_tasks:
            node_name = task.get_name()
            active_tasks.pop(node_name)
            node = self._nodes[node_name]

            # Determine next nodes
            if node.type == "branch":
                branch_target = node.get_target(state, context_id)
                next_nodes = [branch_target] if branch_target != END.name else []
            else:
                next_nodes = self.nexts[node_name]

            # Update ready counts and schedule
            for next_node in next_nodes:
                edge = self._edges_lookup.get((node_name, next_node))
                is_soft = edge and edge.soft

                if is_soft:
                    if next_node in soft_satisfied:
                        continue  # Already satisfied
                    soft_satisfied.add(next_node)

                ready_count[next_node] -= 1

                if ready_count[next_node] == 0:
                    task = asyncio.create_task(
                        name=next_node,
                        coro=self._nodes[next_node].run(state, context_id, parent_context)
                    )
                    active_tasks[next_node] = task

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

Execution order (if branch → case_a):
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

### BranchNode Returns Target

```python
# BranchNode.core returns
{"target": "case_a", "matched": "score >= 90"}
```

### GraphNode Uses Target

```python
if node.type == "branch":
    branch_target = node.get_target(state, context_id)
    if branch_target != END.name:
        next_nodes = [branch_target]  # Chỉ 1 target
    else:
        next_nodes = []
else:
    next_nodes = self.nexts[node_name]  # Tất cả successors
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

for next_node in next_nodes:
    edge = self._edges_lookup.get((node_name, next_node))
    is_soft = edge and edge.soft

    if is_soft:
        if next_node in soft_satisfied:
            continue  # Đã có soft predecessor hoàn thành
        soft_satisfied.add(next_node)  # Mark as satisfied

    ready_count[next_node] -= 1
```

## Error Handling

Errors trong node không stop graph execution:

```python
# In BaseNode.run()
try:
    _outputs = await self.core(**_inputs)
except Exception as e:
    state[self.full_name, "error", context_id] = traceback.format_exc()
    # Node vẫn "hoàn thành", successors có thể chạy
```

## Iteration Node Scheduling

Iteration nodes tự quản lý scheduling cho child graph:

```python
# ForLoopNode - sequential
for i, data in enumerate(iteration_data):
    result = await self._run_graph(state, f"[{i}]", ...)

# MapNode - parallel với semaphore
semaphore = asyncio.Semaphore(max_concurrency)
await asyncio.gather(*[
    execute_iteration(f"[{i}]", data)
    for i, data in enumerate(iteration_data)
])
```
