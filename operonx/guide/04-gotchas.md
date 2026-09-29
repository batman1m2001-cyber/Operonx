# 4. Gotchas — silent failures

Each of these fails **without an exception**: the run finishes and a value
is missing or wrong. The rule comes first, then a script that shows the
failure and the fix.

## An op that raises does not raise

A failing op's outputs are simply missing, and so are those of every op
after it. Check for the key, and read the error from the state.

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
    error = out["$state"][f"{engine.name}.p", "error"]  # "<graph>.<op>", "error"
    assert "ValueError" in error


asyncio.run(main())
```

Over HTTP the same thing is a `500 {"error": "the graph produced no output"}`.

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

## A Ref does not order anything; `>>` does

`b(x=a["y"])` reads a's output but does not wait for it. Without `a >> b`,
`b` runs as soon as it can, with its default. And an op that nothing wires
from `START` never runs at all.

```python
import asyncio

from operonx import END, START, Operon, graph, op


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
    START >> [a, b]  # b does not wait for a
    a >> END
    b >> END


@graph
def unreachable():
    a = make()
    b = use(x=a["y"])
    START >> a >> END
    b >> END  # nothing leads to b


@graph
def with_edge():
    a = make()
    b = use(x=a["y"])
    START >> a >> b >> END


async def main():
    assert (await Operon(no_edge).run(inputs={}))["z"] == -1  # ran before a finished
    assert "z" not in await Operon(unreachable).run(inputs={})  # never ran
    assert (await Operon(with_edge).run(inputs={}))["z"] == 2


asyncio.run(main())
```

## Never compare two Refs inside `if_()`

`if_(p["a"] >= p["b"], ...)` treats the right-hand Ref as a plain value, so
the first branch always wins. Compute the comparison in an op.

```python
import asyncio

from operonx import END, PARENT, START, Operon, graph, op
from operonx.core.ops import if_


@op
def pair(a: int, b: int) -> dict:
    return {"a": a, "b": b, "a_wins": a >= b}


@op
def first() -> dict:
    return {"winner": "a"}


@op
def second() -> dict:
    return {"winner": "b"}


@graph
def wrong():
    p = pair(a=PARENT["a"], b=PARENT["b"])
    f, s = first(), second()
    START >> p >> if_(p["a"] >= p["b"], f).else_(s)  # Ref vs Ref: never do this
    f >> END
    s >> END


@graph
def right():
    p = pair(a=PARENT["a"], b=PARENT["b"])
    f, s = first(), second()
    START >> p >> if_(p["a_wins"] == True, f).else_(s)  # noqa: E712 — Ref vs literal
    f >> END
    s >> END


async def main():
    inputs = {"a": 1, "b": 100}
    assert (await Operon(wrong).run(inputs=inputs))["winner"] == "a"  # wrong
    assert (await Operon(right).run(inputs=inputs))["winner"] == "b"


asyncio.run(main())
```

## Combine conditions with `&` `|` `~`, never `and` `or` `not`

Python's `and` does not see inside a Ref: `x == 1 and y == 2` becomes just
`y == 2`.

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
def with_and():
    p = pair(a=PARENT["a"], b=PARENT["b"])
    y, n = yes(), no()
    START >> p >> if_(p["a"] == 1 and p["b"] == 2, y).else_(n)
    y >> END
    n >> END


@graph
def with_amp():
    p = pair(a=PARENT["a"], b=PARENT["b"])
    y, n = yes(), no()
    START >> p >> if_((p["a"] == 1) & (p["b"] == 2), y).else_(n)
    y >> END
    n >> END


async def main():
    inputs = {"a": 5, "b": 2}
    assert (await Operon(with_and).run(inputs=inputs))["r"] == "yes"  # wrong
    assert (await Operon(with_amp).run(inputs=inputs))["r"] == "no"


asyncio.run(main())
```

## Always end a branch with `.else_()`

A branch finished with `.build()` instead of `.else_()` has no default.
When no case matches, it runs **every** target.

```python
import asyncio

from operonx import END, PARENT, START, Operon, graph, op
from operonx.core.ops import if_

RAN = []


@op
def read(n: int) -> dict:
    return {"n": n}


@op
def high() -> dict:
    RAN.append("high")
    return {"r": "high"}


@op
def mid() -> dict:
    RAN.append("mid")
    return {"r": "mid"}


@graph
def no_else():
    r = read(n=PARENT["n"])
    h, m = high(), mid()
    START >> r >> if_(r["n"] > 100, h).if_(r["n"] > 50, m).build()
    h >> END
    m >> END


async def main():
    await Operon(no_else).run(inputs={"n": 1})
    assert sorted(RAN) == ["high", "mid"]  # nothing matched, yet both ran


asyncio.run(main())
```

Write `if_(a_cond, a).if_(b_cond, b).else_(c)`: exactly one arm then runs.

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

- `user` is a model setting (OpenAI's end-user id), not a template
  variable, so `prompt={"user": "{user}"}` with `user=...` fails.
  Use `user_prompt=` / `question=`.
- `validators=` is read when the graph is built. A Ref there is never
  resolved, every answer fails validation, and the op falls back. Check
  allowed values in an op after the LLM instead.

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
    assert "content" not in await Operon(wrong, params={"q": None}).run(inputs={"q": "hi"})
    assert (await Operon(right, params={"q": None}).run(inputs={"q": "hi"}))["content"]


asyncio.run(main())
```

## Names come from variables

An op or graph is named after the variable it is assigned to; an engine's
root graph after the variable holding the engine. State keys and trace
names follow, so renaming a variable renames them. Pin names that other
code reads.

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
- **`operonx.toml` rejects `on_error = "record"`**; set it on a Python
  `Job(...)` instead.
- **HTTP doors reply after the run ends**; stream with a websocket door.
