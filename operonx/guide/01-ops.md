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
- `run()` returns the outputs of the ops wired `>> END`, plus `"$state"`,
  plus `"$errors"` when an op raised. A key that got several values
  (streaming, loops) holds a list.
- The graph takes the name of the variable its engine is assigned to
  (`engine` above), else its function's name (`outer`). An op takes the
  name of its variable (`d = double(...)` is `d`). Do not write `name=` by
  habit: only when other code reads the name (see
  [names](04-gotchas.md#names-come-from-variables)).

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
  The last frame has `final=True`, an empty `content` and the whole
  answer in `full_content`, so joining every frame's `content` gives the
  answer once. Read `full_content` for the whole text, streamed or not.
- Name template variables after what they hold (`question`, `message`).
  Never `{user}`, `{temperature}` and the like: those are model settings,
  and such a placeholder raises `PromptError` when the op is built.
- `fields=` types are checked, not guessed: `int` from `"2.5"` or `bool`
  from `"maybe"` (it takes true/false/yes/no/1/0) sets `error`, and
  `max_retries=N` asks the model again. A `list` field is a list even
  with one item. The JSON parser reads the first fenced block, or the
  first `{...}` in the text, so prose around the answer is fine.
- `validators=` takes literal values. A Ref there raises `TypeError`;
  check values that arrive at run time in an op after the LLM.
- `cost_usd` is `None` unless the resource sets `cost_per_input_token`
  and `cost_per_output_token`.
- `tool_calls` is a list of `{"id", "name", "args"}` whichever provider
  answered; `args` is a dict, or the model's text when it is not a JSON
  object. Put them back in an assistant message as they are.
- An `llm:` resource takes `timeout:` (seconds for each network wait of a
  request; without it a silent gateway holds a call for 120 s) and
  `structured_output: native | tool | prompted` (how the layer above asks
  it for schema-shaped answers; default `prompted`).
- **Tests need no model:** `api_type: fake` answers from a `script:` of
  turns, in order (the last repeats) — a string, `{tool_calls: [{name:
  lookup, args: {id: 7}}]}`, `{status: 429}` (raises the SDK's error),
  `{delay: 0.5, text: ...}`, `{echo: "Echo: "}` (the last user message
  back). No network; `hub.get("llm:<key>").calls` holds what it was sent.

## Agents — a model that calls tools

Agents are the **operonx-agents** package: an `Agent` spec, `Runner`, and
`AgentOp.of(agent=..., input=...)` for a step of a graph — see the
operonx-agents guide (`.operonx/guide/agents/` once it is installed). The older built-in `operonx.agents`
(`build_react_agent`, `@tool(schema=...)`) was removed in 1.16;
`MIGRATION.md` maps it to the new API.

## Flow ops

- **`if_` / `.else_()`** — branching; see [control flow](03-control-flow.md#ifelse).
- **`EmitOp(payload=ref, channel="progress")`** sends a side event to
  `engine.stream(inputs, mode="custom")` without changing the data flow.
- **`InterruptOp(payload=ref, timeout=0)`** pauses for a human answer.
  `engine.stream(inputs, mode="interrupts")` yields an `InterruptEvent`
  when it pauses (`mode="updates"` yields it among the updates); answer
  with `event.resume(value)`, and the op outputs `response=value`.

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

```python
import asyncio

from operonx import END, START, InterruptOp, Operon, graph, op


@op
def plan(x: int) -> dict:
    return {"plan": f"delete {x} rows"}


@op
def execute(plan: str, approved: str = None) -> dict:
    return {"done": plan if approved == "yes" else "skipped"}


@graph
def guarded(x):
    p = plan(x=x)
    ask = InterruptOp(payload=p["plan"])
    ex = execute(plan=p["plan"], approved=ask["response"])
    START >> p >> ask >> ex >> END


async def main():
    engine = Operon(guarded, params={"x": None})
    async for event in engine.stream({"x": 3}, mode="interrupts"):
        assert event.payload == "delete 3 rows"
        assert event.resume("yes")  # False if the op was no longer waiting


asyncio.run(main())
```

## Retrieval ops

All take `resource=` (a `resources.yaml` key) and are built with `.of(...)`:

| Op | Inputs | Outputs |
|---|---|---|
| `EmbeddingOp` | `texts` | `embeddings` |
| `RerankOp` | `query`, `documents`, `top_k`, `threshold` | `reranks` |
| `VectorSearchOp` | `query_vector`, `top_k`, `filter`, `collection` | `ids`, `scores`, `metadata`, `empty_index` |
| `VectorUpsertOp` | `ids`, `vectors`, `metadata`, `collection` | `upserted` |
| `VectorDeleteOp` | `ids` or `filter`, `collection` | `deleted` |
| `DocFetchOp` | `ids`, `collection`, `fields` | `rows`, `missing` |

Import them from `operonx.providers.ops`. A vector store is an index
derived from your store of record: write to it when a document arrives,
delete from it when one goes.

```yaml file=resources.yaml
vector_store:docs:
  api_type: faiss   # in memory, no server; pgvector and qdrant take the same ops
  metric: ip
  dim: 3
```

```python
import asyncio

import operonx
from operonx import END, START, Operon, graph
from operonx.providers.ops import VectorDeleteOp, VectorSearchOp, VectorUpsertOp


@graph
def sync(ids, vectors, removed, query):
    write = VectorUpsertOp.of(resource="docs", ids=ids, vectors=vectors)
    drop = VectorDeleteOp.of(resource="docs", ids=removed)
    hits = VectorSearchOp.of(resource="docs", query_vector=query, top_k=2)
    START >> write >> drop >> hits >> END


async def main():
    operonx.bootstrap(resources="resources.yaml")
    params = {"ids": None, "vectors": None, "removed": None, "query": None}
    out = await Operon(sync, params=params).run(
        inputs={
            "ids": [1, 2, 3],
            "vectors": [[1, 0, 0], [0.9, 0.1, 0], [0, 1, 0]],
            "removed": [2],
            "query": [1, 0, 0],
        }
    )
    assert out["ids"] == [1, 3]  # 2 was deleted
    assert out["empty_index"] is False


asyncio.run(main())
```

- `VectorDeleteOp` takes `ids=` **or** `filter=`. Neither, both, or an
  empty filter raise: none of them means "delete everything". `ids=[]`
  deletes nothing, and a missing id is not an error.
- `deleted` is `None` on Qdrant, which does not report a count.
- A search on an index that holds no vectors logs a WARNING and sets
  `empty_index`; under a `filter`, no hits is just the answer.

## Doors — how a served graph meets its caller

`ingress()` yields each item a client sends; `egress(item=...)` sends one
back. A graph with doors runs unchanged as a web service and as a job; see
[composition](02-composition.md).
