# 1. Op types

An **op** is one step. You write ops, wire them inside a `@graph`, and run
the graph with `Operon`. Every example below is a complete script.

## `@op` — a Python function as an op

```python
import asyncio

from operonx import END, START, Operon, graph, op


@op
def add(a: int, b: int = 1) -> dict:
    return {"total": a + b}  # the returned dict's keys are the op's outputs


@op
async def double(total: int) -> dict:  # async def works the same way
    await asyncio.sleep(0)
    return {"doubled": total * 2}


@graph
def calc(a):
    s = add(a=a)  # inputs are keyword arguments: a Ref, a literal, or left to the default
    d = double(total=s["total"])  # op["key"] reads another op's output
    START >> s >> d >> END  # >> sets the order; `>> END` returns d's outputs


async def main():
    out = await Operon(calc, params={"a": None}).run(inputs={"a": 2})
    assert out["doubled"] == 6


asyncio.run(main())
```

- **Outputs:** return a dict literal. operonx reads its keys from the
  source, so the op must live in a `.py` file. Keys built at run time must
  be named where the op is used: `d = dyn(return_keys=["a", "b"])`.
- **`bound=`:** `def` runs inline (`"sync"`), `async def` runs as a task
  (`"io"`), and `bound="cpu"` sends a blocking `def` to a thread. Never put
  `bound="io"` on a `def`.
- **Calls are keyword-only**, and a Ref cannot sit inside a dict or list
  argument; pass each value as its own input.
- **Unit test an op** by calling it: `add(a=2)()` returns `{"total": 3}`.

## Generator ops — streaming

A function that `yield`s is a streaming op: every yielded dict runs the
ops downstream of it once. See [control flow](03-control-flow.md#streaming).

```python
import asyncio

from operonx import END, START, Operon, graph, op


@op
def words(text: str):
    for w in text.split():
        yield {"word": w}


@op
def shout(word: str) -> dict:
    return {"loud": word.upper()}


@graph
def flow(text):
    w = words(text=text)
    s = shout(word=w["word"])
    START >> w >> s >> END


async def main():
    out = await Operon(flow, params={"text": None}).run(inputs={"text": "hi there"})
    assert out["loud"] == ["HI", "THERE"]  # one value per item: a list


asyncio.run(main())
```

**Transient ops:** `@op(transient=True)` on a high-rate generator (audio
frames, a long stream) frees each item once it is consumed, so memory stays
flat. A transient output may have only one consumer, cannot feed a
`PARENT.declare` cell, and cannot be `.collect()`ed.

## `@graph` — a graph, and a graph inside a graph

A `@graph` function wires ops. Its parameters are the graph's inputs, and
calling it inside another graph makes it a sub-graph op.

```python
import asyncio

from operonx import END, START, Operon, graph, op


@op
def double(x: int) -> dict:
    return {"result": x * 2}


@op
def label(value: int) -> dict:
    return {"text": f"= {value}"}


@graph
def inner(x):
    d = double(x=x)  # `x` already is PARENT["x"] here: use the parameter
    START >> d >> END


@graph
def outer(n):
    sub = inner(x=n)  # a graph used as an op; its outputs are sub["..."]
    t = label(value=sub["result"])
    START >> sub >> t >> END


async def main():
    engine = Operon(outer, params={"n": None})  # None: `n` is a runtime input
    assert (await engine.run(inputs={"n": 5}))["text"] == "= 10"
    fixed = Operon(outer, params={"n": 7})  # a value: baked in at build time
    assert (await fixed.run(inputs={}))["text"] == "= 14"


asyncio.run(main())
```

- A graph parameter becomes a runtime input only when you pass `None`
  (or a Ref) for it in `params=`. Omitting it raises `TypeError`.
- **Use a parameter by its name.** Inside the body a runtime-input
  parameter already is `PARENT["x"]`, so `double(x=x)` is the whole of it;
  `double(x=PARENT["x"])` beside a parameter `x` says the same thing twice.
  Keep `PARENT[...]` for what the graph does not declare: a loop cell
  (`PARENT.declare`) or a write-back (`op["n"] >> PARENT["n"]`).
- A `Job` given a `@graph` builds it with every parameter as a runtime
  input (`params={name: None}`), so a default in the signature never
  applies there. Give the value in `Job(inputs=...)`.
- `run()` returns the outputs of the ops wired `>> END`, plus `"$state"`.
  A key that got several values (streaming, loops) holds a list.
- The graph takes the name of the variable its engine is assigned to
  (`engine` above). Pin it with `name="..."` when a name matters.

## `LLMOp` — a model call

Models are resources: declare them in `resources.yaml`, refer to them by
name.

```yaml file=resources.yaml
llm:assistant:
  api_type: openai          # openai | azure | vllm | gemini | anthropic
  api_key: ${LLM_API_KEY}
  base_url: ${LLM_BASE_URL}
  model: gpt-4o-mini
```

```python
import asyncio

import operonx
from operonx import END, START, Operon, graph
from operonx.providers.ops import LLMOp


@graph
def answer(question):
    llm = LLMOp.of(
        resource="assistant",  # the key without "llm:"
        prompt={"system": "Answer in one line.", "user": "{question}"},
        question=question,  # every other keyword fills the template
    )
    START >> llm >> END


@graph
def classify(message):
    llm = LLMOp.of(
        resource="assistant",
        prompt="Give the intent of: {message}. Reply as <intent>...</intent>",
        fields=["intent: str"],  # parsed into its own output
        message=message,
    )
    START >> llm >> END


async def main():
    operonx.bootstrap(resources="resources.yaml")  # before any Operon() that uses a model
    out = await Operon(answer, params={"question": None}).run(inputs={"question": "Hello?"})
    assert out["content"]  # also: usage, cost_usd, finish_reason, model_used
    out = await Operon(classify, params={"message": None}).run(
        inputs={"message": "I want my money back"}
    )
    assert out["intent"] == "refund" and out["error"] is None


asyncio.run(main())
```

- `prompt` is a string (one user message) or `{"system": ..., "user": ...}`
  with `{placeholders}`; `messages=[...]` passes a ready message list.
- `stream=True` makes it a streaming op that yields `content` deltas.
- Name template variables after what they hold (`question`, `message`).
  Never `user=` or `temperature=` and the like: those are model settings.
- `cost_usd` is `None` unless the resource sets `cost_per_input_token`
  and `cost_per_output_token`.

## Agents — a model that calls tools

`build_react_agent` loops model → tools → model until the model is done.
Tools are `@tool` functions.

```yaml file=resources.yaml
llm:assistant:
  api_type: openai
  api_key: ${LLM_API_KEY}
  base_url: ${LLM_BASE_URL}
  model: gpt-4o-mini
```

```python
import asyncio

import operonx
from operonx import Operon
from operonx.agents import agent_result, build_react_agent, get_tool_definitions, tool
from operonx.agents.ops.model_ops import make_llm_caller


@tool(
    name="add",
    description="Add two numbers.",
    schema={
        "type": "object",
        "properties": {"a": {"type": "number"}, "b": {"type": "number"}},
        "required": ["a", "b"],
    },
    readonly=True,
)
def add(a: float, b: float) -> dict:
    return {"sum": a + b}


async def main():
    operonx.bootstrap(resources="resources.yaml")
    agent = build_react_agent(
        call_model=make_llm_caller("assistant", tools=get_tool_definitions()),
        max_turns=8,
    )(messages=None)
    out = await Operon(agent).run(
        inputs={"messages": [{"role": "user", "content": "What is 2 + 3?"}]}
    )
    result = agent_result(out, agent)  # messages, turns, final, stopped_early, truncated, ...
    assert result["final"]


asyncio.run(main())
```

- Read the answer with `agent_result(out, agent)`; never index
  `out["messages"]` (it holds one list per turn). `stopped_early` is True
  when the budget ran out or the last response was cut off (`truncated`,
  with `finish_reason`); `final` is `None` if the model never answered.
- `destructive=True` tools pause for approval through an `InterruptOp`;
  `AgentSession(agent).send(text, on_approval=...)` handles the loop.
- In tests, pass a scripted `call_model` op instead of a real model.

## Flow ops

- **`if_` / `.else_()`** — branching; see [control flow](03-control-flow.md#ifelse).
- **`EmitOp(payload=ref, channel="progress")`** sends a side event to
  `engine.stream(inputs, mode="custom")` without changing the data flow.
- **`InterruptOp(payload=ref, timeout=0)`** pauses for a human answer;
  resume with `handle.state.resume_interrupt(interrupt_id, value)`.

```python
import asyncio

from operonx import END, START, EmitOp, Operon, graph, op


@op
def work(n: int) -> dict:
    return {"value": n * 2, "note": f"doubled {n}"}


@graph
def progress(n):
    w = work(n=n)
    say = EmitOp(payload=w["note"], channel="progress")
    START >> w >> END
    w >> say >> END  # a side branch; it must reach END too


async def main():
    engine = Operon(progress, params={"n": None})
    seen = [e.payload async for e in engine.stream({"n": 4}, mode="custom", channels=["progress"])]
    assert seen == ["doubled 4"]


asyncio.run(main())
```

## Retrieval ops

All take `resource=` (a `resources.yaml` key) and are built with `.of(...)`:

| Op | Inputs | Outputs |
|---|---|---|
| `EmbeddingOp` | `texts` | `embeddings` |
| `RerankOp` | `query`, `documents`, `top_k`, `threshold` | `reranks` |
| `VectorSearchOp` | `query_vector`, `top_k`, `filter`, `collection` | `ids`, `scores`, `metadata` |
| `DocFetchOp` | `ids`, `collection`, `fields` | `rows`, `missing` |

Import them from `operonx.providers.ops`.

## Doors — how a served graph meets its caller

`ingress()` yields each item a client sends; `egress(item=...)` sends one
back. A graph with doors runs unchanged as a web service and as a job; see
[composition](02-composition.md).
