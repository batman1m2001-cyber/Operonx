# Streaming

Operonx is streaming-first. Generator ops yield frames; downstream ops
consume them as they appear; the engine emits frames from `engine.stream(...)`
in real time.

## Generator ops

```python
from operonx.core import op

@op
def each_chunk(text: str, size: int = 100):
    for i in range(0, len(text), size):
        yield {"chunk": text[i:i+size]}
```

Use `yield` instead of `return`. Each yield produces one frame downstream.

## Streaming an LLM response

LLM ops support streaming via `stream=True`:

```python
from operonx.providers import LLMOp

llm = LLMOp.of(resource="gpt-4o", messages=PARENT["messages"], stream=True)
```

With streaming on, `llm["content"]` is a stream of token chunks rather
than a single string. Downstream ops see one frame per chunk.

The **last** frame repeats the whole accumulated `content` rather than a
tail, so joining every frame emits the answer twice. `final` separates
them — join the `final=False` deltas, or read the one `final=True` frame,
never both.

## Consuming frames

`engine.run(...)` returns the final state. To watch frames as they
arrive, use `engine.stream(...)`:

```python
async for frame in engine.stream(inputs={"messages": [...]}):
    if frame.op_name == "llm":
        print(frame.outputs["content"], end="", flush=True)
```

`frame.op_name`, `frame.outputs`, and `frame.span` (for tracing) are the
common fields.

## Frame fan-out

When a generator op yields N times, downstream ops run N times — in
parallel by default. To collect ordered output, wrap them in a graph that
produces a list:

```python
@op
def collect(values: list):
    return {"all": values}
```

Or use `outputs={"*": PARENT}` to wildcard-forward every output, which
appends to a list at the parent level.

## Loops vs streaming

Generator ops parallelize fan-out. A back-edge inside `@graph` (see
[Loops](03-loops-and-branches.md)) serialises feedback. Use generators
for "do the same thing to N items"; use a back-edge for "iterate until
a condition is met."

## Where to go next

- Wire a tracer to inspect frames: [Tracing](07-tracing.md).
- Deploy the streaming engine over HTTP: [Deployment](08-deployment.md).
