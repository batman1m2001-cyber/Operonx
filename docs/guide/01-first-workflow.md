# Your first workflow

This guide walks through a minimal pure-compute graph — no LLM, no API
keys, no `bootstrap()` call. Once this runs end-to-end you understand
the basic shape of every Operonx program.

## The pieces

- `@op` — turn a Python function into a node.
- `GraphOp` — a context manager that collects ops into a DAG.
- `>>` — wire ops in sequence.
- `PARENT["k"]` — read an input from `engine.run(inputs={...})`.
- `op["k"]` — read an output from a sibling op.
- `Operon(graph)` — the engine.
- `await engine.run(...)` — execute.

## Hello world

```python
import asyncio
from operonx.core import Operon, GraphOp, op, START, END, PARENT

@op
def greet(name: str):
    return {"message": f"Hello, {name}!"}

@op
def shout(text: str):
    return {"result": text.upper()}

async def main():
    with GraphOp(name="hello") as graph:
        g = greet(name=PARENT["name"])
        s = shout(text=g["message"])
        START >> g >> s >> END

    engine = Operon(graph)
    result = await engine.run(inputs={"name": "World"})
    print(result["result"])  # HELLO, WORLD!

asyncio.run(main())
```

Run it and you'll see `HELLO, WORLD!`. Two things to note:

1. `greet` reads `PARENT["name"]` because `name` came in via
   `engine.run(inputs={...})`.
2. `shout` reads `g["message"]` because `message` was produced by `greet`,
   not by the engine. Mixing these up is the most common beginner mistake
   — see [State model](../architecture/state-model.md) for details.

## What `>> END` does

`>> END` auto-forwards the **last op's outputs** to the graph result. In
the example above, `shout` produces `{"result": ...}`, and that's what
`engine.run(...)` returns.

If you want a different shape, map explicitly:

```python
s["result"] >> PARENT["greeting"]
# Now engine.run(...) returns {"greeting": "HELLO, WORLD!"}
```

## Async ops

If your op does I/O, declare it `async`. The engine awaits it the same way:

```python
@op
async def fetch(url: str):
    response = await httpx.AsyncClient().get(url)
    return {"text": response.text}
```

## Run the example

The repository ships a runnable version of this:

```bash
python -m examples.python.ex01_hello_world.demo
```

## Where to go next

- Add an LLM call: [LLM chat](02-llm-chat.md).
- Iterate over a list: [Loops and branches](03-loops-and-branches.md).
- Understand state references: [State model](../architecture/state-model.md).
