# 3. Control flow

`a >> b` means "b runs after a". A value read through `a["key"]` does not
order anything by itself, so always write the edge.

## Streaming

A generator op runs its downstream ops **once per `yield`**, one item at a
time, in order. `.parallel()` runs items at once; `.collect()` waits for
the whole stream and hands over one list.

```python
import asyncio

from operonx import END, START, Operon, graph, op


@op
def words(text: str):
    for w in text.split():
        yield {"word": w}


@op
async def shout(word: str) -> dict:
    await asyncio.sleep(0.01)
    return {"loud": word.upper()}


@op
def join(words: list) -> dict:
    return {"line": " ".join(words)}


@graph
def per_item(text):
    w = words(text=text)
    s = shout(word=w["word"])  # sequential, in yield order (the default)
    START >> w >> s >> END


@graph
def at_once(text):
    w = words(text=text)
    s = shout(word=w["word"].parallel())  # all items together; order not kept
    START >> w >> s >> END


@graph
def all_together(text):
    w = words(text=text)
    j = join(words=w["word"].collect())  # runs once, after the last yield
    START >> w >> j >> END


async def main():
    text = {"text": "a b c"}
    assert (await Operon(per_item, params={"text": None}).run(inputs=text))["loud"] == [
        "A",
        "B",
        "C",
    ]
    assert sorted((await Operon(at_once, params={"text": None}).run(inputs=text))["loud"]) == [
        "A",
        "B",
        "C",
    ]
    assert (await Operon(all_together, params={"text": None}).run(inputs=text))["line"] == "a b c"


asyncio.run(main())
```

- Sequential is the default. It keeps per-item state safe (a counter, a
  buffer), so reach for `.parallel()` only for independent items.
- `.collect()` only batches when the consumer reads **straight from the
  generator**. Behind another per-item op it hands over one-item lists.
- `.parallel(max=N)` does not limit anything yet; bound concurrency with
  the graph's `concurrency=N` (default 64).
- A stream has no "end" signal besides `.collect()`.

## Loops

### While loop

State that changes between iterations lives in a declared cell. Write it
back with `op["x"] >> PARENT["x"]`, and loop with a back-edge through
`if_(...).else_(op)`.

```python
import asyncio

from operonx import END, PARENT, START, Operon, graph, op
from operonx.core.ops import if_


@op
def step(n: int) -> dict:
    return {"n": n + 1, "done": n + 1 >= 3}


@graph
def count_to_3():
    PARENT.declare(n=0)  # loop state: a cell, starting at 0
    s = step(n=PARENT["n"])
    s["n"] >> PARENT["n"]  # write the new value back every iteration
    START >> s >> if_(s["done"] == True, END).else_(s)  # noqa: E712 — else_ is the back-edge


async def main():
    out = await Operon(count_to_3).run(inputs={})
    assert out["n"] == [1, 2, 3]  # one value per iteration; the last is out["n"][-1]


asyncio.run(main())
```

- **Termination:** after each iteration the loop continues only if the
  back-edge fired. It stops at 1000 iterations whatever happens.
- **Always `PARENT.declare` loop state.** An undeclared value is re-read
  from the first iteration every time, so the loop never ends.
- **Compute the stop condition in an op** (`"done": n >= limit`) and branch
  on `op["done"] == True`.
- **Exit to `END`.** An op wired on the exit arm runs on every iteration;
  do follow-up work in the graph that calls the loop.

### For loop

There is no for-each op. Iterate with a generator op; the ops after it run
once per item.

```python
import asyncio

from operonx import END, START, Operon, graph, op


@op
def each(items: list):
    for i, item in enumerate(items):
        yield {"i": i, "item": item}


@op
def price(i: int, item: dict) -> dict:
    return {"line": f"{i + 1}. {item['name']}: {item['qty'] * item['unit']}"}


@graph
def invoice(items):
    e = each(items=items)
    p = price(i=e["i"], item=e["item"])
    START >> e >> p >> END


async def main():
    items = [{"name": "pen", "qty": 2, "unit": 3}, {"name": "pad", "qty": 1, "unit": 5}]
    out = await Operon(invoice, params={"items": None}).run(inputs={"items": items})
    assert out["line"] == ["1. pen: 6", "2. pad: 5"]


asyncio.run(main())
```

## if/else

`if_(condition, op)` routes to the first matching op; chain more cases with
`.if_(...)` and finish with `.else_(op)`. The arms **merge automatically**:
an op fed by both arms runs once, when the arm that ran finishes.

```python
import asyncio

from operonx import END, START, Operon, graph, op
from operonx.core.ops import if_


@op
def check(n: int) -> dict:
    return {"big": n > 10}


@op
def big(n: int) -> dict:
    return {"label": "big"}


@op
def small(n: int) -> dict:
    return {"label": "small"}


@op
def done(
    big_label: str = None, small_label: str = None
) -> dict:  # the arm that did not run gives None
    return {"answer": big_label or small_label}


@graph
def classify(n):
    c = check(n=n)
    b, s = big(n=n), small(n=n)
    d = done(big_label=b["label"], small_label=s["label"])
    START >> c >> if_(c["big"] == True, b).else_(s)  # noqa: E712
    b >> d
    s >> d  # no extra syntax: d runs once, after whichever arm ran
    d >> END


async def main():
    engine = Operon(classify, params={"n": None})
    assert (await engine.run(inputs={"n": 3}))["answer"] == "small"
    assert (await engine.run(inputs={"n": 30}))["answer"] == "big"


asyncio.run(main())
```

- **Put a real op before the branch.** `START >> if_(...)` is a `TypeError`.
- **Finish with `.else_(op)`, or `.build()` for no default.** With
  `.build()`, when nothing matches no arm runs, and neither does an op fed
  only by the arms.
- **Give merge inputs a default** (`= None`): the arm that did not run
  sends nothing.
- A condition is a Ref compared to a literal or to another Ref
  (`c["big"] == True`, `c["n"] > 10`, `p["a"] >= p["b"]`), combined with
  `&`, `|`, `~` (never `and`, `or`, `not`), or an op that returns a single
  `bool`.
- Write a branch inline (it is named `route_1`, `route_2`, … in its
  graph); assign it (`size = if_(...)`) only when another op refers to it.

## `~` — fire on whichever arrives first

Branch merges need no `~`. It is still needed for a **race**: two
independent ops (no branch between them) feed one op, and that op should
run as soon as the first one lands. Nothing in the graph's shape says so,
which is why operonx cannot infer it: without `~`, the op waits for both.

```python
import asyncio

from operonx import END, START, Operon, graph, op

ORDER = []


@op
async def cache_lookup() -> dict:
    return {"answer": "cached"}


@op
async def slow_compute() -> dict:
    await asyncio.sleep(0.3)
    ORDER.append("slow finished")
    return {"answer": "computed"}


@op
def reply(cached: str = None, computed: str = None) -> dict:
    ORDER.append("reply")
    return {"text": cached or computed}


@graph
def fastest():
    c, s = cache_lookup(), slow_compute()
    r = reply(cached=c["answer"], computed=s["answer"])
    START >> [c, s]
    c >> ~r  # ~ marks the edge into r as soft:
    s >> ~r  # r fires once, on the first soft edge that lands
    r >> END


async def main():
    out = await Operon(fastest).run(inputs={})
    assert out["text"] == "cached"
    assert ORDER == ["reply", "slow finished"]  # replied before the slow op was done


asyncio.run(main())
```

- `a >> ~b` softens only the edge into `b`.
- Later arrivals are ignored once the op has fired.
- **Never mix one hard edge with two or more soft edges into the same op**:
  two soft arrivals can fire it before the hard one lands.
- `~` on a Ref (`~ref`) is logical NOT, not a soft edge.

## Cells and SCRATCH

- **`PARENT.declare(x=0, reducers={"x": fn})`** makes a graph-level cell:
  loop state, or many writers folded by a reducer
  (`operator.add`, `operonx.reducers.add_messages`). An op that omits a key
  leaves the cell as it was.
- **`SCRATCH["k"]`** is a per-run dict for side data. Read and write it
  inside op bodies; seed it with `engine.run(inputs, scratch={...})`. It
  orders nothing.
