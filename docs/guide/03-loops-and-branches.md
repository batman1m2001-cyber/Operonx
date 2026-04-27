# Loops and branches

Two control-flow primitives cover most workflow patterns: generator ops
for iteration, and `BranchOp` for conditional routing. Use
`GraphOp.loop` for feedback loops where the next iteration depends on
the previous one.

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

## Feedback loop with `GraphOp.loop`

```python
from operonx.core import GraphOp, op, START, END, PARENT

@op
def increment(counter: int):
    return {"counter": counter + 1}

with GraphOp.loop(until="count >= 5", count=0) as loop:
    inc = increment(counter=PARENT["count"])
    inc["counter"] >> PARENT["count"]
    START >> inc >> END
```

`until=` is evaluated against the loop's local state after each iteration.
`inc["counter"] >> PARENT["count"]` writes the new value back so the next
iteration sees it.

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

## Combining

You can nest loops inside iterations and branches inside loops. Frame
state is per-iteration, so each frame in an outer loop carries its own
inner-loop state.

## Where to go next

- Build agentic patterns: [Agents](05-agents.md).
- Stream loop frames: [Streaming](06-streaming.md).
