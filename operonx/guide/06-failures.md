# 6. Failures: retry, timeout, error edges, fail-fast

By default an op that raises is recorded and the run carries on: its
outputs are missing, the ops after it do not run, and `"$errors"` names it
(see [Gotchas](04-gotchas.md)). Everything on this page changes that, and
all of it is declared, never written into an op body.

| You want | Write |
|---|---|
| try a flaky call again | `@op(retry=Retry(max_attempts=4))` |
| stop a call that hangs | `@op(timeout=Timeout(run=30))` |
| do something when an op fails | `op.on_error(handler)` |
| the failure itself, as an exception | `Operon(g, errors="raise")` |
| at most N ops running at once | `Operon(g, max_concurrency=N)` |

## `retry=` — try again, after a pause

```python
import asyncio

from operonx import END, START, Operon, Retry, graph, op

CALLS = []


@op(retry=Retry(max_attempts=3, initial=0.01))
async def fetch(order: int) -> dict:
    CALLS.append(order)
    if len(CALLS) < 3:
        raise ConnectionError("503 from the CRM")  # a transient error
    return {"status": "shipped"}


@graph
def track(order):
    f = fetch(order=order)
    START >> f >> END


async def main():
    handle = Operon(track, params={"order": None}).start({"order": 7})
    out = await handle.result()
    assert out["status"] == "shipped" and "$errors" not in out
    assert len(CALLS) == 3
    attempts = [n for n in handle.trace.nodes if n.op_name == "f"]
    assert [(n.attempt, n.status) for n in attempts] == [(1, "retried"), (2, "retried"), (3, "ok")]


asyncio.run(main())
```

- **What is retried:** `on=TRANSIENT` by default — a `TimeoutError`, a
  `ConnectionError`, an error carrying HTTP status 429 or 5xx, and the
  connection errors of `httpx` and `openai`. A `ValueError` or a 400 fails
  the same way every time and is not retried. Pass `on=` an exception class,
  a tuple, or a function `error -> bool` to choose.
- **The pause:** `initial` seconds, times `backoff` (2) per attempt, at most
  `max_interval` (30 s), with jitter: a random wait between half of that
  and all of it.
- **A generator is retried only before its first yield.** After that its
  items are already downstream; the error is recorded instead.
- **One use of an op:** `fetch(order=o, retry=Retry(max_attempts=2))`
  overrides the decorator. Only a `Retry` goes there: `fetch(url=u,
  timeout=10)` still passes the function its own `timeout` argument.
- **Every attempt is in the trace** with its `attempt` number. An attempt
  that was run again has `status="retried"` (its error kept) and an
  `op_id` ending in `@1`, `@2` …; it does not make the run a failure. The
  last attempt of an op that never succeeds is an ordinary `error`.
- **`LLMOp`** retries its transport per resource (`max_retries:` in
  `resources.yaml`). An error that retry already gave up on is not retried
  again by `retry=`: set one of the two, not both, for the same errors.

## `timeout=` — a deadline per attempt

```python
import asyncio
import time

from operonx import END, START, Operon, Retry, Timeout, graph, op


@op(timeout=Timeout(run=0.1))
async def lookup(q: str) -> dict:
    await asyncio.sleep(5)  # hangs
    return {"answer": q}


@op
def reply(answer: str) -> dict:
    return {"text": answer.upper()}


@graph
def ask(q):
    lk = lookup(q=q)
    r = reply(answer=lk["answer"])
    START >> lk >> r >> END


# A timeout is TRANSIENT: with a retry it is tried again.
@graph
def ask_twice(q):
    lk = lookup(q=q, retry=Retry(max_attempts=2, initial=0.01))
    START >> lk >> END


async def main():
    engine = Operon(ask, params={"q": None})
    t0 = time.perf_counter()
    out = await engine.run({"q": "hi"})
    assert time.perf_counter() - t0 < 1
    assert "text" not in out  # the op failed, so reply did not run
    assert "TimeoutError" in str(out["$errors"][f"{engine.name}.lk"])

    handle = Operon(ask_twice, params={"q": None}).start({"q": "hi"})
    await handle.result()
    assert len([n for n in handle.trace.nodes if n.op_name == "lk"]) == 2


asyncio.run(main())
```

- **`Timeout(run=)`** bounds one attempt, start to end. **`Timeout(idle=)`**
  is for generators: the longest the generator may take to produce its next
  item (`Timeout(run=60, idle=5)` for a stream that must keep moving).
- **A plain `def` op cannot take a timeout**: it runs on the event loop,
  where nothing can interrupt it, so building it raises. Make it
  `async def`, or add `bound="cpu"`: it then runs in a thread, which the
  timeout abandons (the thread finishes in the background).
- **A subgraph takes `timeout=Timeout(run=)`** at its call site
  (`sub = agent(q=q, timeout=Timeout(run=60))`): past it, the ops inside are
  cancelled and the subgraph fails with `TimeoutError`.

## `op.on_error(handler)` — route a failure

```python
import asyncio

from operonx import END, START, Operon, graph, op


@op
async def lookup(order: int) -> dict:
    if order < 0:
        raise ConnectionError("crm down")
    return {"status": f"order {order} shipped"}


@op
def apologise(error: str, op: str, inputs: dict) -> dict:
    return {"status": f"sorry, {op.split('.')[-1]} failed ({error})"}


@op
def reply(status: str) -> dict:
    return {"text": f"> {status}"}


@graph
def answer(order):
    look = lookup(order=order)
    sorry = apologise()
    r = reply(status=look["status"])
    look.on_error(sorry)  # sorry runs once, only if look failed
    sorry["status"] >> r["status"]
    START >> look >> r >> END
    sorry >> r  # look and its handler never both run, so r merges them by itself


async def main():
    engine = Operon(answer, params={"order": None})
    assert (await engine.run({"order": 2}))["text"] == "> order 2 shipped"
    out = await engine.run({"order": -1})
    assert out["text"] == "> sorry, look failed (ConnectionError: crm down)"
    assert list(out["$errors"]) == [f"{engine.name}.look"]  # handled, still recorded


asyncio.run(main())
```

- The handler's parameters named `error` (`"TypeName: message"`), `op` (the
  failed op's full name) and `inputs` (what it was called with) receive the
  failure; take any of them. It runs once per failed run of the op, after
  the last `retry=` attempt.
- The failure stays in `"$errors"`, marked `"handled": true`. A handled
  failure does not end an `errors="raise"` run, fail a `Job`'s item or a
  served request, or turn the trace's status to `error`; neither does an
  `LLMOp(on_failure="error")` hard failure. A parse failure is not handled.
- On a per-item op the handler runs per failed item, in that item's
  context.

## `errors="raise"` — end the run at the first failure

```python
import asyncio

from operonx import END, START, OpFailed, Operon, graph, op


@op
async def parse(x: str) -> dict:
    return {"n": int(x)}


@op
async def slow(x: str) -> dict:
    await asyncio.sleep(2)
    return {"done": True}


@graph
def batch(x):
    p = parse(x=x)
    s = slow(x=x)
    START >> [p, s]
    [p, s] >> END


async def main():
    engine = Operon(batch, params={"x": None}, errors="raise")
    try:
        await engine.run({"x": "not a number"})
    except OpFailed as failed:
        assert failed.op == f"{engine.name}.p"
        assert isinstance(failed.__cause__, ValueError)
    else:
        raise AssertionError("expected OpFailed")


asyncio.run(main())
```

- The ops still running are cancelled (`slow` above never finishes).
  `run()`, `result()`, `collect()`, iterating the handle and every
  `stream()` mode raise the same `OpFailed`.
- For jobs, tests and batch scripts. A live session keeps the default,
  `errors="record"`: one failing op must not end a call.
- It acts on an op that *raises*. A failure an op returns as data — an
  `LLMOp(fields=..., on_failure="error")` answer that would not parse, in
  its `error` output — is recorded in `"$errors"` and the run goes on, so
  the op after it can branch on `error`.

## `max_concurrency=` — one cap for the whole run

A graph's `concurrency=N` (default 64) caps that graph only, so nested
graphs multiply: two subgraphs at `concurrency=2` run four ops at once.
`Operon(g, max_concurrency=N)` is one cap for every op of the run, nested
or not.

```python
import asyncio

from operonx import END, START, Operon, graph, op

LIVE = {"now": 0, "max": 0}


@op
def fan(n: int):
    for i in range(n):
        yield {"i": i}


@op
async def call_api(i: int) -> dict:
    LIVE["now"] += 1
    LIVE["max"] = max(LIVE["max"], LIVE["now"])
    await asyncio.sleep(0.01)
    LIVE["now"] -= 1
    return {"r": i}


@graph
def inner(n):
    f = fan(n=n)
    c = call_api(i=f["i"].parallel())
    START >> f >> c >> END


@op
def batches(m: int):
    for _ in range(m):
        yield {"n": 4}


@graph
def outer(m):
    b = batches(m=m)
    sub = inner(n=b["n"].parallel(), concurrency=2)
    START >> b >> sub >> END


async def main():
    out = await Operon(outer(m=None, concurrency=2), max_concurrency=2).run({"m": 4})
    assert len(out["r"]) == 16
    assert LIVE["max"] <= 2  # without max_concurrency: 4


asyncio.run(main())
```

It counts the ops that run as tasks (`async def`, `bound="cpu"`); a plain
`def` op runs inline and a subgraph holds no slot of its own.

## Two writers of one cell must be ordered

Two ops that may run at the same time, both writing a `PARENT.declare(...)`
cell that has no reducer, fail the build: the cell would keep whichever
write lands last, which changes from run to run.

```python
from operonx import END, PARENT, START, Operon, graph, op
from operonx.core.ops.graph.validation import GraphValidationError


@op
async def fast(x: int) -> dict:
    return {"v": "fast"}


@op
async def slow(x: int) -> dict:
    return {"v": "slow"}


@graph
def race(x):
    PARENT.declare(v=None)
    a = fast(x=x)
    b = slow(x=x)
    a["v"] >> PARENT["v"]
    b["v"] >> PARENT["v"]
    START >> [a, b]
    [a, b] >> END


@graph
def race_on_purpose(x):
    PARENT.declare(v=None, allow_race=True)  # last write wins, on purpose
    a = fast(x=x)
    b = slow(x=x)
    a["v"] >> PARENT["v"]
    b["v"] >> PARENT["v"]
    START >> [a, b]
    [a, b] >> END


try:
    Operon(race, params={"x": None})
except GraphValidationError as e:
    assert "concurrent writers" in str(e)
else:
    raise AssertionError("expected the build to fail")

Operon(race_on_purpose, params={"x": None})
```

The fixes, in order of preference: order the writers (`a >> b`), give the
cell a reducer (`reducers={"v": operator.add}`), or say last-write-wins is
intended with `allow_race=True` (or `allow_race=["v"]`). Writers on two arms
of one branch, or an op and its error handler, never both run and need
nothing.
