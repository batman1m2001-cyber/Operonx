# Ops

## BaseOp

Base class for all ops in Hush workflows.

::: hush.core.ops.base.BaseOp
    options:
      show_source: true
      members:
        - __init__
        - run
        - __rshift__

## GraphOp

Container op for building DAG workflows.

::: hush.core.ops.graph.graph_op.GraphOp
    options:
      show_source: true
      members:
        - __init__
        - __enter__
        - __exit__
        - build
        - serialize
        - loop

## @op Decorator

Turn a function into a workflow op.

::: hush.core.ops.transform.func_op.op

## @graph Decorator

Turn a builder function into a reusable GraphOp factory.

::: hush.core.ops.graph._decorators.graph

## Loops

Feedback loops use `GraphOp.loop()` or the `@graph.loop()` decorator:

```python
# Class method style
with GraphOp.loop(until="count >= 5", max_iterations=100) as loop:
    inc = increment(counter=PARENT["count"])
    inc["counter"] >> PARENT["count"]
    START >> inc >> END

# Decorator style
@graph.loop(until="count >= 5")
def counter(count):
    inc = increment(counter=count)
    inc["counter"] >> PARENT["count"]
    START >> inc >> END

loop = counter(count=0)
```

## Constants

- `START` — entry point sentinel for graph edges
- `END` — exit point sentinel for graph edges
- `PARENT` — reference to parent graph state (for inputs from `engine.run()`)
