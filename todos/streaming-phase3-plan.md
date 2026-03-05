# Streaming Phase 3 — Remove WhileOp → GraphOp.loop()

## Context

After Phase 2, ForOp/MapOp/AIterOp are gone — replaced by generator ops + streaming scheduler. WhileOp is the last iteration op standing. Phase 3 promotes WhileOp's feedback loop behavior into GraphOp itself, then deletes the entire `iteration/` module.

## Design: GraphOp.loop()

### API

```python
# Class method — creates a looping GraphOp
with GraphOp.loop(name="agent", until="done == True", max_iterations=100) as loop:
    llm = LLMOp.of(resource="gpt-4o", messages=PARENT["messages"])
    parse = extract_action(response=llm["content"])
    execute = run_tool(action=parse["action"])
    update = append_messages(messages=PARENT["messages"], tool_result=execute["result"])

    update["new_messages"] >> PARENT["messages"]  # state carry-forward
    parse["done"] >> PARENT["done"]
    START >> llm >> parse >> execute >> update >> END

# With initial state
with GraphOp.loop(name="counter", count=0, until="count > 3") as loop:
    inc = increment(count=PARENT["count"])
    inc["count"] >> PARENT["count"]
    START >> inc >> END
```

### @graph.loop() decorator

```python
@graph.loop(until="done == True", max_iterations=100)
def agent(messages: list):
    llm = LLMOp.of(resource="gpt-4o", messages=messages)
    parse = extract_action(response=llm["content"])
    execute = run_tool(action=parse["action"])
    update = append_messages(messages=messages, tool_result=execute["result"])

    update["new_messages"] >> PARENT["messages"]
    parse["done"] >> PARENT["done"]
    START >> llm >> parse >> execute >> update >> END

# Usage
with GraphOp(name="main") as g:
    a = agent(messages=PARENT["msgs"])
    START >> a >> END
```

### How It Works

`GraphOp.loop()` returns a GraphOp with `_loop_config` set:

```python
@classmethod
def loop(cls, name=None, until=None, max_iterations=100, **initial_state):
    """Create a looping GraphOp that re-runs until condition is met."""
    graph = cls(name=name)
    graph._loop_config = LoopConfig(
        until=until,              # condition expression (string or callable)
        max_iterations=max_iterations,
        initial_state=initial_state,  # e.g., count=0
    )
    return graph
```

### Scheduler Behavior

In `GraphOp.run()`, after the normal scheduler loop completes, check if this is a looping graph:

```python
# Normal scheduler runs to completion (all ops done)
# ...existing event-queue scheduler...

# Loop logic (only if _loop_config is set)
if self._loop_config:
    iteration = 0
    while iteration < self._loop_config.max_iterations:
        # Collect graph outputs
        _outputs = self.get_outputs(state, context_id, parent_context)

        # Evaluate until condition
        if self._evaluate_until(_outputs):
            break

        # Create new iteration context (tuple-based, migrated in Phase 1)
        iteration += 1
        iter_ctx = context_id + (f"[{iteration}]",)

        # Carry forward: graph outputs become next iteration's inputs
        for var_name, value in _outputs.items():
            state[self.full_name, var_name, iter_ctx] = value

        # Re-run scheduler with new context
        await self._run_scheduler(state, iter_ctx, parent_context)

    _outputs = self.get_outputs(state, context_id_current, parent_context)
```

### State Carry-Forward

The `>>  PARENT["key"]` output mapping already works:
1. Inner op writes output → push ref propagates to graph's output variable
2. Graph outputs collected at end of iteration
3. Loop logic writes outputs as inputs for next iteration context
4. Next iteration's `get_inputs()` reads from new context → gets previous iteration's output

This is exactly what WhileOp does in `_execute()` — just integrated into GraphOp.

### Until Condition

Same evaluation as WhileOp — supports both string expressions and callables:

```python
# String expression — evaluated against graph outputs
GraphOp.loop(until="count > 3")
GraphOp.loop(until="done == True")
GraphOp.loop(until="len(messages) > 10")

# Callable — receives graph outputs dict
GraphOp.loop(until=lambda out: out["count"] > 3)
```

### Safety

- `max_iterations` (default 100) prevents infinite loops
- Graph outputs include `_loop_metrics`:
  ```python
  {
      "total_iterations": 5,
      "stopped_by_condition": True,   # vs max_iterations_reached
  }
  ```

## Comparison: WhileOp vs GraphOp.loop()

| Feature | WhileOp | GraphOp.loop() |
|---------|---------|----------------|
| Container | Separate op class (~150 lines) | GraphOp classmethod (~30 lines) |
| Scheduler | Own mini-scheduler via `_run_graph()` | Reuses GraphOp's event-queue scheduler |
| State carry-forward | `_store_iteration_data()` | Graph output → next iteration input |
| Until condition | String or callable | Same |
| Max iterations | `max_iterations` param | Same |
| Context isolation | `get_iter_context()` | Same tuple-based contexts |
| Nesting | WhileOp inside GraphOp | GraphOp.loop() inside GraphOp |
| Streaming inside loop | Needs special handling | Works naturally (scheduler handles both) |

Key advantage: GraphOp.loop() reuses the **same scheduler** that handles streaming. A loop iteration can contain generator ops, and they work with no special code — the scheduler already handles per-yield contexts.

## What Gets Deleted

The entire `iteration/` module:

```
hush-core/hush/core/ops/iteration/
├── __init__.py      ← delete
├── base.py          ← delete (BaseIterationOp, Each, Broadcast, get_iter_context)
├── for_op.py        ← already deleted in Phase 2
├── map_op.py        ← already deleted in Phase 2
├── aiter_op.py      ← already deleted in Phase 2
└── while_op.py      ← delete
```

`get_iter_context()` moves to `graph_op.py` (already tuple concatenation since Phase 1).

## What Gets Added

| File | Change |
|------|--------|
| `hush-core/hush/core/ops/graph/graph_op.py` | `loop()` classmethod, `_loop_config`, loop logic in `run()` |
| `hush-core/hush/core/decorators.py` | `@graph.loop()` decorator |
| `hush-core/tests/ops/test_graph_loop.py` | Loop tests (migrate from WhileOp tests) |

## Tests

| Test | Description |
|------|-------------|
| `test_simple_loop` | `count=0, until="count > 3"` → 4 iterations |
| `test_loop_max_iterations` | Hits max_iterations safety limit |
| `test_loop_with_streaming` | Generator op inside a loop iteration |
| `test_loop_state_carry_forward` | Output of iteration N becomes input of N+1 |
| `test_loop_callable_until` | Lambda condition works |
| `test_nested_loops` | Loop inside a loop |
| `test_loop_metrics` | `total_iterations`, `stopped_by_condition` in output |
| `test_graph_loop_decorator` | `@graph.loop()` decorator works |

## End State

After Phase 3, the op hierarchy is:

```
BaseOp
├── FuncOp          (@op — sync/async/generator functions)
├── GraphOp         (container + scheduler + loop)
├── BranchOp        (conditional routing)
└── (providers)     (LLMOp, EmbeddingOp, etc.)

No iteration/ module. GraphOp handles:
  - Regular workflows    → GraphOp(name="...")
  - Streaming/fan-out    → generator @op inside GraphOp
  - Feedback loops       → GraphOp.loop(until="...")
```
