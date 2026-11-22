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

**Stores:** an op that reads or writes the project's own data says which:
`@op(writes="inbox")`, `@op(reads=["inbox", "events"])`. A store is a name (a
table, a queue, a folder); the engine does nothing with it. The studio's
project picture draws `flow → store → flow` from these, which is how two
flows that never call each other are seen to work together.

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
- `stream=True` makes it a streaming op that yields `content` deltas
  (to a client as server-sent events: `egress(item=s["content"])` on an
  `http(..., sse=True)` door, [composition](02-composition.md#doors-for-streams)).
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
  One test scripts its own turns for a `with` block, whatever `conftest.py`
  installed: `with operonx.testing.fake_model({"status": 503}, "ok",
  retry={"max_attempts": 3}) as model:` (`key="llm:assistant"` by default;
  other keywords are the resource's own, `chunk_size=` too); the old model
  is back after the block.

## Prompts — objects in `prompts/`, asked for by name

A prompt that matters is an object in the project's `prompts/`, with an id
(`pr_` and 8 hex digits) that stays when it is renamed. Two kinds:

- **text** — `prompts/<name>.md`, the text as the model gets it. `{{x}}` is
  data, filled in; any other brace is text (the JSON a prompt shows needs no
  escaping). Front matter (`---` … `---`) holds `id` and `description`.
- **composite** — `prompts/<name>.yaml`, links to other objects. `kind: chat`
  has `system:` and `user:`; `kind: text` (the default) has `parts:`. A part
  is one of:
  - `use: <name>` (or the bare name) — that object;
  - `pick: <var>` + `cases: {<value>: <name>}` + `else: <name> | none` — one
    object, chosen by a value (no `else`: a value with no case raises);
  - `when: <var>` + `use: <name>` — that object when the value is true;
  - `var: <x>` — data, as given;
  - `text: "…"` — literal text, kept as written.

  Parts are joined with `join:` (default a blank line); a part that renders
  empty drops out, with its join.

```python
import asyncio
from pathlib import Path

from operonx import END, START, Operon, graph
from operonx.prompts import prompt
from operonx.providers.ops import LLMOp
from operonx.testing import fake_model

Path("prompts/billing").mkdir(parents=True, exist_ok=True)
Path("prompts/guard.md").write_text('Reply with JSON only: {"intent": "..."}\n')
Path("prompts/billing/rules.md").write_text("Refunds within {{days}} days.\n")
Path("prompts/intake.yaml").write_text(
    """\
kind: chat
system:
  - text: You help {{customer}}.
  - pick: team
    cases: {billing: billing/rules}
    else: none
  - guard
user:
  - var: message
"""
)


@graph
def intake(customer, team, days, message):
    llm = LLMOp.of(
        resource="assistant",
        prompt=prompt("intake"),
        customer=customer,
        team=team,
        days=days,
        message=message,
    )
    START >> llm >> END


async def main():
    params = {"customer": None, "team": None, "days": None, "message": None}
    handle = Operon(intake, params=params).start(
        {"customer": "Lan", "team": "billing", "days": 30, "message": "money back please"}
    )
    await handle.result()
    return handle.trace.nodes


with fake_model('{"intent": "refund"}') as model:
    nodes = asyncio.run(main())

sent = model.calls[0]
sent = sent.get("messages", sent) if isinstance(sent, dict) else sent
assert sent[0]["content"].startswith("You help {{customer}}.\n\nRefunds within 30 days.")
row = next(n for n in nodes if n.op_name == "llm")
# the run says which objects, which versions, and what the pick chose
assert [r["name"] for r in row.attrs["prompts"]] == ["intake", "billing/rules", "guard"]
```

- `prompt("name")` is read when the graph is built: a name the project does
  not have, a link to one, or a malformed composite raises there (so
  `operonx check` and `operonx lint` catch it). A composite given to
  `prompt=` is rendered from the op's values; `prompt("intake")["system"]` is
  one role of it, and `.render(**data)` renders it yourself (a chat gives
  `.system`, `.user`, `.messages`).
- A `text:` part is literal: `{{customer}}` in it stays as written. Data goes
  in through `var:` or a text object's `{{x}}`.
- **A variable is data, never a prompt.** Which text goes in is a link —
  `use`, `pick`, `when` — so the run row and `operonx prompt show` can say
  it. A prompt given as a composite's value raises `TypeError`.
- `operonx prompt show intake` prints a composite's tree (every case of every
  pick) and the variables it needs; `list` shows each object's kind and id;
  `mv OLD NEW` renames an object and rewrites every link to it (published
  versions included), keeping its id.
- **Versions and labels.** `operonx prompt publish intake --label
  production` gives the object an id if it has none, freezes the working
  text as the next number (`prompts/.versions/intake/1.yaml`) and points the
  label at it (`prompts/labels.toml`). A composite's links are loaded at the
  same label. A process serves the label in `OPERONX_PROMPT_LABEL` (default
  `production`); an object without that label is served from its working
  text. Roll back by moving the label: `operonx prompt label intake
  production 1`. Also `diff`, `log`. Labels are read when the graph is
  built: a moved label takes a restart. Commit `prompts/` — the history is
  the repository's.
- **A service that runs from an older build.** Set a prompt store — a
  Postgres address, as a setting of the project:
  `[prompts]` `store = "${PROMPT_STORE_DSN}"` in `operonx.toml`, the address in
  the environment or `.env`. `operonx prompt publish` / `label` then also copy
  the versions (with their id and kind) and labels there (`operonx prompt
  push` copies all of them, `operonx prompt store` shows what it serves), and
  `prompt("name")` in a process with the store set gives what the store's
  label points at — a version published after the image was built included.
  One way only: the files are where a prompt is written; a store that cannot
  be reached serves the files. A process re-reads the store every 15 s in the
  background (`OPERONX_PROMPT_STORE_EVERY`); code that builds its prompts once
  compares `operonx.prompt_store.generation()` and builds them again when it
  went up.
- Every run row of that op carries `attrs.prompts` — `[{part, name, id,
  version, label, hash}]`, each object it linked (`version` is `None` for the
  working text; `hash` is of the text) — and, for a composite,
  `attrs.prompt_tree`: the objects as linked, and the value each `pick` and
  `when` was given.
- **The old ways work for one minor, with a `DeprecationWarning`:**
  `{{> other}}` inside a text object, and a prompt given as a value to
  another's `.render(...)` (an op rendering one prompt into another). Both
  become a composite. `operonx lint` (C19) lists them, with `<<x>>` and
  single-brace `{x}` left in a text object, and prose written into
  `LLMOp(prompt="…")` in the code.

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

A served graph's parameters are what the caller sends and its outputs are
the reply — no door needed. A run that handles many items (a call, a
socket) reads them with `ingress()`, which yields each item a client sends,
and answers with `egress(item=...)`. Either shape runs unchanged as a web
service and as a job; see [composition](02-composition.md).
