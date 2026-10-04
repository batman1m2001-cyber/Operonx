# 8. Inside a run: run context, child steps, stream modes, live traces

What an op body can know about the run it is in, how a loop that lives inside
one op stays visible in the trace, and how to watch a run while it goes. None
of it changes what runs: control flow stays in the graph.

| You want | Write |
|---|---|
| the run's id, the attempt, a key for an external API | `run_context()` inside the op |
| something the caller knows (a tenant, a user) | `engine.start(inputs, context=obj)`, read `run_context().context` |
| a model call or tool call inside an op in the trace | `async with child("model", inputs=...) as c:` |
| several stream modes at once | `engine.stream(inputs, mode=["updates", "tasks"])` |
| each op's start and end as it happens | `mode="tasks"` |
| a run in the store before it ends | a ClickHouse or SQL store: it lists the run as `running` |

## `run_context()` — ids, attempt, deadline, idempotency key

```python
import asyncio
from dataclasses import dataclass

from operonx import END, START, Operon, Retry, graph, op, run_context

CHARGED = {}


@dataclass(frozen=True)
class Tenant:
    name: str


@op(retry=Retry(max_attempts=3, initial=0.01))
async def charge(order: int) -> dict:
    rc = run_context()
    # the same key on every attempt of this op in this run: the payment API
    # deduplicates by it, so a retried attempt never charges twice
    CHARGED.setdefault(rc.idempotency_key, []).append(rc.attempt)
    if rc.attempt < 2:
        raise ConnectionError("503 from the payment API")
    return {"tenant": rc.context.name, "run": rc.run_id, "thread": rc.thread_id}


@graph
def checkout(order):
    c = charge(order=order)
    START >> c >> END


async def main():
    engine = Operon(checkout, params={"order": None})
    out = await engine.run(
        {"order": 7}, trace_id="order-7", session_id="cust-1", context=Tenant("acme")
    )
    assert (out["tenant"], out["run"], out["thread"]) == ("acme", "order-7", "cust-1")
    (attempts,) = CHARGED.values()
    assert attempts == [1, 2]
    assert run_context() is None  # outside an op


asyncio.run(main())
```

- **Fields:** `run_id` (the trace id: `trace_id=`, else the request id),
  `thread_id` (the `session_id` you passed, else `None`), `op_path` (the op's
  full name), `ctx`, `attempt`, `deadline` and `remaining` (from
  `Timeout(run=)`, on the `time.monotonic()` clock), `context` (what you
  passed to `start`/`run`/`stream`), and `idempotency_key`.
- **`idempotency_key`** is a hash of the run id, the op and its ctx: the same
  on every attempt, different in every other run and in every item of a
  fan-out. Pass it to an API that deduplicates.
- It is read-only and information only; nothing in operonx reads it back.
- An `InterruptOp`'s `interrupt_id` is the same hash, so a question has the
  same id every time the same run reaches it.

## `child()` — the steps an op runs itself

An agent loop or a retry-by-hand inside one op makes calls the graph never
sees. `child()` records each as its own execution under the op's record, with
its inputs, outputs, status and timing.

```python
import asyncio

from operonx import END, START, Operon, child, graph, op


async def call_model(question: str) -> dict:
    return {"content": f"answer to {question}", "usage": {"prompt_tokens": 12}}


@op
async def agent(question: str) -> dict:
    async with child("turn", inputs={"question": question}, op_type="turn") as turn:
        async with child("model", inputs={"messages": [question]}, op_type="llm") as call:
            reply = await call_model(question)
            call.outputs = reply
            call.attrs["gen_ai.operation.name"] = "chat"
        turn.outputs = {"content": reply["content"]}
    return {"answer": reply["content"]}


@graph
def ask(question):
    a = agent(question=question)
    START >> a >> END


async def main():
    handle = Operon(ask, params={"question": None}).start({"question": "why?"})
    await handle.collect()
    records = {n.op_name: n for n in handle.trace.nodes}
    turn, model = records["turn"], records["model"]
    assert turn.ctx == ("main", "turn[0]") and turn.op_full_name == "ask.a.turn"
    assert model.ctx == ("main", "turn[0]", "model[0]")
    assert model.outputs["content"] == "answer to why?"
    assert model.attrs == {"gen_ai.operation.name": "chat"}


asyncio.run(main())
```

- **Where it hangs:** under the op's record; for a generator, under the yield
  being produced when the block opens; inside another `child`, under it. Its
  ctx is the parent's plus `"<name>[n]"`, so every consumer (local, Langfuse,
  ClickHouse, the studio) nests it with no setup.
- **`c.outputs`** is recorded when the block ends; **`c.attrs`** holds semantic
  attributes (`gen_ai.*`, a tool's name, usage) that consumers keep.
- An exception in the block is recorded as `error` and raised on; a
  cancellation is recorded as `cancelled`.
- The op's `@op(exclude=/include=)` applies to its children's inputs and
  outputs too.
- Inside the block, `run_context()` describes the child: each step has its
  own `idempotency_key`.
- `async with` only: a plain `def` op cannot use it. Outside a run it records
  nothing.
- Names are plain: no `.`, `[`, `]` or `#`.

## Stream several modes at once; `tasks`

`mode` takes a list. The stream then yields `(mode, chunk)` pairs from one
run, in arrival order. The modes are `updates`, `values`, `frames`, `custom`,
`interrupts` and `tasks`.

```python
import asyncio

from operonx import END, START, Operon, TaskFailed, TaskFinished, TaskStarted, graph, op


@op
async def fetch(n: int):
    for i in range(n):
        yield {"row": i}


@op
async def broken() -> dict:
    raise ValueError("bad input")


@graph
def watched(n):
    f = fetch(n=n)
    b = broken()
    START >> f >> END
    START >> b >> END


async def main():
    engine = Operon(watched, params={"n": None})
    seen = []
    async for mode, chunk in engine.stream({"n": 2}, mode=["updates", "tasks"]):
        seen.append((mode, type(chunk).__name__))
        if isinstance(chunk, TaskFailed):
            assert chunk.error == "ValueError: bad input" and chunk.attempt == 1
    tasks = [name for mode, name in seen if mode == "tasks"]
    assert tasks.count("TaskStarted") == 2 and tasks.count("TaskFinished") == 1
    assert ("updates", "dict") in seen


asyncio.run(main())
```

- `TaskStarted(op, ctx, attempt)`, `TaskFinished(op, ctx, attempt, duration_ms)`,
  `TaskFailed(op, ctx, attempt, duration_ms, error, cancelled, retrying)`: one
  start and one end per op invocation (a generator ends after its last item)
  and per `child()`. A retried attempt ends `TaskFailed(retrying=True)` and the
  next one starts with its own `TaskStarted`.
- A string `mode` still yields bare chunks. With `"updates"` and
  `"interrupts"` both, an `InterruptEvent` arrives once, under `"interrupts"`.

## Live traces

A run store that writes as the run goes lists it as `running` from its start,
with each execution as it lands; the final write replaces both. The
ClickHouse and SQL (SQLite, Postgres) stores do; a killed process leaves its
run listed as `running`, with what it finished.

```python
import asyncio

from operonx import END, START, Operon, graph, op
from operonx.telemetry.runs import RunFilter
from operonx.telemetry.runs.sqlite import SqliteRunStore

GO = asyncio.Event()


@op
async def first(x: int) -> dict:
    return {"y": x + 1}


@op
async def slow(y: int) -> dict:
    await GO.wait()
    return {"z": y * 2}


@graph
def job(x):
    f = first(x=x)
    s = slow(y=f["y"])
    START >> f >> s >> END


async def main():
    store = SqliteRunStore("runs.sqlite")
    handle = Operon(job, params={"x": None}, trace=store).start({"x": 1}, trace_id="job-1")
    while not store.list_runs(RunFilter(status="running")).items:
        await asyncio.sleep(0.05)
    record = store.get_run("job-1")
    while not record.nodes:  # each execution lands a moment after it ends
        await asyncio.sleep(0.05)
        record = store.get_run("job-1")
    assert record.summary.status == "running"
    assert [n["op_name"] for n in record.nodes] == ["f"]  # while `slow` still waits
    GO.set()
    await handle.collect()
    assert store.get_run("job-1").summary.status == "ok"


asyncio.run(main())
```

- A consumer of your own goes live by overriding `on_start(trace)` and
  `on_execution(trace, execution)` beside `consume(trace)`. Both run on the
  event loop: queue the work and return.
- The files store (the default `.operonx/runs`), Mongo and Langfuse still write
  when the run ends.
- `running` is all a store can say about a killed run: telling a dead writer
  from a slow op needs a lease, which durable runs bring.
