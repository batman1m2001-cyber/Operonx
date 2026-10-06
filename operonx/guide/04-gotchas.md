# 4. Gotchas — silent failures

Each of these fails **without an exception**: the run finishes and a value
is missing or wrong. The rule comes first, then a script that shows the
failure and the fix.

## An op that raises is reported in `$errors`, not raised

The run finishes; the failed op's outputs are missing, and so are those
of every op after it. `run()` adds `"$errors"` only when an op failed, so
check for that key. Each entry is `{"<graph>.<op>": {"type", "message",
"count", "first_ctx"}}`: the exception's class name, the first failure's
traceback trimmed to your own frames, how many times the op failed (a
streaming op failing on two items counts 2), and where the first one ran
(the failed trace node is `f"{op}#{first_ctx}"`). The whole traceback stays
in the trace and the op's `error` cell.

```python
import asyncio

from operonx import END, PARENT, START, Operon, graph, op


@op
def parse(x: str) -> dict:
    return {"n": int(x)}


@graph
def flow():
    p = parse(x=PARENT["x"])
    START >> p >> END


async def main():
    engine = Operon(flow)
    out = await engine.run(inputs={"x": "not a number"})
    assert "n" not in out  # no exception: the output is just missing
    error = out["$errors"][f"{engine.name}.p"]
    assert error["type"] == "ValueError" and error["count"] == 1
    assert error["message"].endswith("invalid literal for int() with base 10: 'not a number'\n")
    assert "$errors" not in await engine.run(inputs={"x": "3"})  # absent when clean


asyncio.run(main())
```

An op raising inside a subgraph stops the ops after the subgraph too, and
`$errors` gets a `"<graph>.<sub>"` entry (`type` `"SubgraphError"`) naming
the op that raised. A structured `LLMOp` step that fails returns `error`
rather than raising, and is in `$errors` too (`type` `"ParserError"`), so
the cause shows beside whatever failed downstream of it.

`handle.errors` is the same dict on a started run. Over HTTP a failed run
is a `500 {"error": "the graph produced no output", "trace_id": ...}`; the
traceback stays in the log and the trace, which that id finds.

## Return a dict literal; name dynamic keys where the op is used

Output keys are read from the `return {...}` in the source. A dict built
up at run time has no known keys, and its values are dropped.

```python
import asyncio

from operonx import END, START, Operon, graph, op


@op
def build() -> dict:
    out = {}
    out["total"] = 3
    return out


@graph
def silent():
    b = build()
    START >> b >> END


@graph
def fixed():
    b = build(return_keys=["total"])  # name them at the call site
    START >> b >> END


async def main():
    assert "total" not in await Operon(silent).run(inputs={})
    assert (await Operon(fixed).run(inputs={}))["total"] == 3


asyncio.run(main())
```

Ops must be defined in a `.py` file (not a REPL or `exec`) for the same
reason.

## A misspelled output key fails the build; `op.get` reads an optional one

When an op returns dict literals, its output keys are known, and building
the graph checks every `op["key"]` against them: a typo raises
`GraphValidationError` with "did you mean". An output the op may leave
out — an op someone else wrote, whose contract makes it optional — is
read with `op.get("key")`: not checked, and the reader gets its own
parameter default when the key never arrives.

```python
import asyncio

from operonx import END, START, Operon, graph, op
from operonx.core.ops.graph import GraphValidationError


@op
def make(x: int) -> dict:
    return {"total": x + 1}


@op
def show(total: int = 0, note: str = "none") -> dict:
    return {"text": f"total={total} note={note}"}


@graph
def typo(x):
    m = make(x=x)
    s = show(total=m["totl"])
    START >> m >> s >> END


@graph
def optional(x):
    m = make(x=x)
    s = show(total=m["total"], note=m.get("note"))  # make() never returns "note"
    START >> m >> s >> END


async def main():
    try:
        Operon(typo, params={"x": None})
        raise AssertionError("expected a build error")
    except GraphValidationError as e:
        assert "did you mean 'total'?" in str(e)
    out = await Operon(optional, params={"x": None}).run(inputs={"x": 1})
    assert out["text"] == "total=2 note=none"


asyncio.run(main())
```

## A Ref does not order anything; `>>` does

`b(x=a["y"])` reads a's output but does not wait for it. A read with no
edge `a >> b` (directly or through other ops) fails the build: `b` would get
the value or its default depending on timing. Draw the edge.

```python
import asyncio

from operonx import END, START, Operon, graph, op
from operonx.core.ops.graph.validation import GraphValidationError


@op
async def make() -> dict:
    await asyncio.sleep(0.05)
    return {"y": 2}


@op
def use(x: int = -1) -> dict:
    return {"z": x}


@graph
def no_edge():
    a = make()
    b = use(x=a["y"])
    START >> [a, b]  # b would not wait for a
    [a, b] >> END


@graph
def with_edge():
    a = make()
    b = use(x=a["y"])
    START >> a >> b >> END


async def main():
    try:
        Operon(no_edge)
    except GraphValidationError as e:
        assert "START >> a >> b" in str(e)  # the message names the edge to draw
    else:
        raise AssertionError("expected the build to fail")
    assert (await Operon(with_edge).run(inputs={}))["z"] == 2


asyncio.run(main())
```

- Reads of `PARENT[...]` (graph inputs, declared cells) and `SCRATCH[...]`
  order nothing by design and are not checked. To read *whatever value is
  there* from an op running beside you, share it through a declared cell
  (`PARENT.declare(y=None)`, `a["y"] >> PARENT["y"]`, read `PARENT["y"]`).
- An op with no edges at all, in a graph with no `START >>`, runs as an
  entry, beside every other entry; the build warns. In a graph that has
  `START >> ...`, an op nothing leads to never runs.

## Combine conditions with `&` `|` `~`, never `and` `or` `not`

Python's `and`, `or`, `not`, `if` and `in` cannot see inside a Ref, so
they raise a `TypeError` when the graph is built. Use `&`, `|`, `~`.

```python
import asyncio

from operonx import END, PARENT, START, Operon, graph, op
from operonx.core.ops import if_


@op
def pair(a: int, b: int) -> dict:
    return {"a": a, "b": b}


@op
def yes() -> dict:
    return {"r": "yes"}


@op
def no() -> dict:
    return {"r": "no"}


@graph
def with_amp():
    p = pair(a=PARENT["a"], b=PARENT["b"])
    y = yes()
    n = no()
    START >> p >> if_((p["a"] == 1) & (p["b"] == 2), y).else_(n)
    y >> END
    n >> END


async def main():
    p = pair(a=1, b=2)
    try:
        _ = p["a"] == 1 and p["b"] == 2  # never do this
        raise AssertionError("expected a TypeError")
    except TypeError as e:
        assert "&" in str(e)  # the message names the operators to use
    assert (await Operon(with_amp).run(inputs={"a": 5, "b": 2}))["r"] == "no"
    assert (await Operon(with_amp).run(inputs={"a": 1, "b": 2}))["r"] == "yes"


asyncio.run(main())
```

## `None` does not bind to an input

An op that receives `None` gets its parameter default instead, and with no
default it fails (silently, as above). Give every input that may be `None`
a default.

```python
import asyncio

from operonx import END, PARENT, START, Operon, graph, op


@op
def lookup(key: str) -> dict:
    return {"value": None if key == "missing" else key.upper()}


@op
def show(value: str = "(none)") -> dict:  # the default is what arrives for None
    return {"text": value}


@graph
def flow():
    lk = lookup(key=PARENT["key"])
    s = show(value=lk["value"])
    START >> lk >> s >> END


async def main():
    assert (await Operon(flow).run(inputs={"key": "missing"}))["text"] == "(none)"


asyncio.run(main())
```

## `LLMOp`: never `user=`, never a Ref in `validators=`

- `user`, `temperature`, `seed` and the other model settings are sent to
  the provider, never into the template. A `{user}` placeholder can never
  be filled, so building the op raises `PromptError`. Name template
  variables after what they hold: `{user_prompt}`, `{question}`.
- `validators=` is read when the graph is built. A Ref there (a graph
  parameter, `PARENT[...]`) is never resolved, so building the op raises
  `TypeError`. When the allowed values arrive at run time, check them in
  an op after the LLM (second example).

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
from operonx import END, START, Operon, graph
from operonx.core.exceptions import PromptError
from operonx.providers.ops import LLMOp


@graph
def wrong(q):
    llm = LLMOp.of(resource="assistant", prompt={"system": "Be brief.", "user": "{user}"}, user=q)
    START >> llm >> END


@graph
def right(q):
    llm = LLMOp.of(
        resource="assistant", prompt={"system": "Be brief.", "user": "{user_prompt}"}, user_prompt=q
    )
    START >> llm >> END


async def main():
    operonx.bootstrap(resources="resources.yaml")
    try:
        Operon(wrong, params={"q": None})
        raise AssertionError("expected PromptError")
    except PromptError as e:
        assert "{user}" in str(e)  # names the placeholder and suggests {user_prompt}
    assert (await Operon(right, params={"q": None}).run(inputs={"q": "hi"}))["content"]


asyncio.run(main())
```

```python
import asyncio

import operonx
from operonx import END, START, Operon, graph, op
from operonx.providers.ops import LLMOp

PROMPT = "Give the intent of: {message}. Reply as <intent>...</intent>"


@graph
def wrong(message, allowed):
    llm = LLMOp.of(
        resource="assistant",
        prompt=PROMPT,
        fields=["intent: str"],
        validators={"intent": allowed},  # a Ref: refused when the graph is built
        message=message,
    )
    START >> llm >> END


@op
def check(intent: str, allowed: list) -> dict:
    return {"intent": intent if intent in allowed else "other"}


@graph
def right(message, allowed):
    llm = LLMOp.of(resource="assistant", prompt=PROMPT, fields=["intent: str"], message=message)
    c = check(intent=llm["intent"], allowed=allowed)
    START >> llm >> c >> END


async def main():
    operonx.bootstrap(resources="resources.yaml")
    params = {"message": None, "allowed": None}
    try:
        Operon(wrong, params=params)
        raise AssertionError("expected TypeError")
    except TypeError as e:
        assert "validators" in str(e)
    out = await Operon(right, params=params).run(
        inputs={"message": "I want my money back", "allowed": ["refund", "cancel"]}
    )
    assert out["intent"] == "refund"


asyncio.run(main())
```

## Names come from variables

An op or graph is named after the variable it is assigned to; an engine's
root graph after the variable holding the engine. Not assigned to a plain
variable (`self.engine = Operon(greet)`, `out = await
Operon(greet).run()`), a `@graph` is named after its function. Jobs and
services name their runs after the graph's function. State keys and trace
names follow, so renaming a variable renames them. Pin names that other
code reads.

**Do not write `name=` by habit.** `reply = chat(text=text)` is already
`reply`. Write `name=` only when:

- other code reads the name — a trace filter, a state key, an eval that
  checks an op's output by name (`trajectory.op_output("classify", ...)`);
- the op is not assigned to a plain variable — built in a list, a dict
  or a loop, it is named after the container or not at all;
- two ops in one graph would otherwise get the same name.

**One op per line.** `a, b = f(), g()` names neither `a` nor `b` (they fall
back to their function names), on every Python version. Write
`a = f()` and `b = g()`.

```python
import asyncio

from operonx import END, START, Operon, graph, op


@op
def hello() -> dict:
    return {"text": "hi"}


@graph
def greet():
    h = hello()
    START >> h >> END


async def main():
    engine = Operon(greet)
    assert engine.name == "engine"  # not "greet"
    pinned = Operon(greet(name="greet"))
    assert pinned.name == "greet"
    engines = {"greet": Operon(greet)}  # no plain variable: the function's name
    assert engines["greet"].name == "greet"


asyncio.run(main())
```

## Smaller rules

- **Graph parameters:** pass `params={"x": None}` to `Operon` for each
  runtime input. Keys in `run(inputs=...)` that nothing reads are ignored,
  not reported.
- **A long-running op reads its inputs once.** A generator that runs for a
  whole session never sees later cell changes; keep such state in a
  mutable object.
- **Inputs and outputs are traced as JSON.** A dict with tuple keys breaks
  the trace; use string keys.
- **HTTP doors reply after the run ends**; stream with a websocket door.
- **A keyword your function takes is its input.** `@op def label(id, name)`
  called `label(id=7, name="x")` gets both, though `id` and `name` are also
  op settings. To give such a setting, `label.configure(name="labeller")(id=7,
  name="x")`.
- **A reducer cell's final value is `out["$cells"]`** (the root's declared
  cells by name); `out["<cell>"]` is what the run wrote to it.
- **Logs go to stderr**, at `OPERONX_LOG_LEVEL` (else `LOG_LEVEL`, else
  WARNING). "Slow op" fires past `OPERONX_SLOW_OP_MS` (100), never for
  model, store, graph, agent, door or generator ops.
- **`trace_clickhouse` drops runs rather than wait.** Its queue is
  bounded (`queue_size`); while ClickHouse is down or slow, runs past the
  bound are dropped. A warning is logged once per outage, and the count is
  in `store.writer.stats` (`dropped_full`, `dropped_failed`). A short
  script that must not lose its last runs calls `store.flush()`.
