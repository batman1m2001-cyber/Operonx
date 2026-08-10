# Loops and branches

Two control-flow primitives cover most workflow patterns: generator ops
for iteration, and `BranchOp` for conditional routing. For feedback
loops where the next iteration depends on the previous one, write a
back-edge inside a `@graph` — the build-time cycle-rewrite pass
synthesizes a hidden loop under the hood.

## Iterate with a generator op

```python
from operonx.core import GraphOp, op, START, END, PARENT

@op
def each_item(items: list):
    for item in items:
        yield {"value": item}

@op
def double(value: int):
    return {"result": value * 2}

with GraphOp(name="map") as graph:
    gen = each_item(items=PARENT["numbers"])
    step = double(value=gen["value"])
    START >> gen >> step >> END
```

Three items in, three frames downstream — running in parallel by default.

## Feedback loop with a back-edge

Write the graph as if the loop is a regular DAG plus a `>>` back to an
earlier node; the build-time pass rewrites the cyclic body into a
hidden `_GraphLoop` for the scheduler:

```python
from operonx.core import graph, op, START, END, PARENT
from operonx.core.ops.flow.branch_op import if_

@op
def increment(counter: int):
    return {"counter": counter + 1}

@graph
def counter():
    # Loop state as a shared cell — every iteration writes back into it.
    PARENT.declare(count=0)
    inc = increment(counter=PARENT["count"])
    inc["counter"] >> PARENT["count"]
    # if_() decides: END (exit) vs inc (continue — the back-edge).
    START >> inc >> if_(PARENT["count"] >= 5, END).else_(inc)

g = counter()
```

The `if_(...).else_(inc)` is the back-edge — the branch's else-target
returns to `inc`. Each iteration commits its outputs to the shared
`count` cell, the branch reads the updated value, and decides to exit
or loop again.

Two ways loops terminate:

1. **Branch to `END`** (the pattern above) — the natural while-loop
   shape. Recommended.
2. **`max_iterations` cap** on the synthesized loop — defaults to 1000.
   Fires when no branch ever routes away from the back-edge. A
   safety valve; not the primary exit path.

### Nested loops

Nest `@graph`s freely. Each inner loop is rewritten independently and
gets its own per-iteration ctx segment (`{full_name}#{n}`), so
per-iteration cell values from different loops never collide:

```python
@graph
def inner():
    PARENT.declare(i=0)
    step = tick(i=PARENT["i"])
    step["i"] >> PARENT["i"]
    START >> step >> if_(PARENT["i"] >= 3, END).else_(step)

@graph
def outer():
    PARENT.declare(o=0)
    body = do_work(o=PARENT["o"])
    body["o"] >> PARENT["o"]
    nested = inner()
    START >> body >> nested >> if_(PARENT["o"] >= 2, END).else_(body)
```

### Loops that accumulate — use a reducer

`PARENT.declare(**vars, reducers={...})` binds a reducer to a shared
cell. Writes go through `reducer(old, new) → merged` instead of
overwriting, so lists / dicts / counters accumulate across iterations:

```python
import operator

@graph
def collect():
    PARENT.declare(items=[], reducers={"items": operator.add})
    step = produce_item()
    step["item"] >> PARENT["items"]     # append via operator.add
    START >> step >> if_(step["done"] == True, END).else_(step)
```

See `operonx.reducers` for the standard set (`add_messages` for
LangGraph-style id-upsert, `dict_merge` for recursive dict merge, etc.).

## Branch with hard vs soft edges

`>>` is a hard edge — the downstream op runs unconditionally. `>>~` is a
soft edge — the downstream op runs only when the upstream branch op
selects this output.

```python
from operonx.core import BranchOp

@op
def is_long(text: str):
    return {"long": len(text) > 100}

with GraphOp(name="route") as graph:
    check = is_long(text=PARENT["text"])
    branch = BranchOp(
        condition=check["long"],
        outputs=["summary", "passthrough"],
    )
    summary_op = summarize(text=PARENT["text"])
    passthrough_op = identity(text=PARENT["text"])

    START >> check >> branch
    branch >>~ summary_op       # soft — runs only when condition is true
    branch >>~ passthrough_op   # soft — runs only when condition is false
    summary_op >> END
    passthrough_op >> END
```

Soft edges do **not** count toward `ready_count`. The downstream ops
fire only when the branch routes to them.

## Opt out of the rewrite

If you want fail-fast behaviour on accidental cycles (typos in wiring),
mark the graph strict:

```python
@graph(strict_dag=True)
def dag_only():
    ...
    # A stray back-edge here surfaces as a build-time cycle warning
    # instead of being silently synthesized into a loop.
```

## Where to go next

- Build agentic patterns: [Agents](05-agents.md).
- Stream loop frames: [Streaming](06-streaming.md).
