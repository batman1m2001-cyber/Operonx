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
never both. The two always agree.

### Fallback while streaming

`fallback=[...]` covers a stream only **until its first delta**. A
failure before any text arrives (connection refused, 429, 5xx) switches
to the next resource and the consumer never notices. A failure after it
propagates as the op's error: the fallback would start its answer from
the beginning, and the deltas already out — already on screen, already
spoken by a voice app — would be followed by the whole answer again.

```python
llm = LLMOp.of(resource="gpt-4o", fallback=["claude-haiku"], stream=True,
               messages=PARENT["messages"])
# primary drops after "Sure, your appointment is"
#   before: deltas "Sure, your appointment isSure, your appointment is Monday."
#           final  "Sure, your appointment is Monday."
#   now:    deltas "Sure, your appointment is", then the op fails
```

A consumer that must always finish its turn handles that error itself —
for a voice app, typically a short "sorry, one moment" and a retry.
Without `stream=True` the fallback covers the whole call.

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
