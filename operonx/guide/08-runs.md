# 8. Inside a run: run context, child steps, stream modes, live traces, durable runs

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
- **`c.redact = fn`** (`dict -> dict`) scrubs the step's inputs and outputs
  where the trace leaves the process: every run store, the Local and Langfuse
  consumers write them through it. `handle.trace` keeps them as recorded, and
  the run's event loop does none of the work.
- Inside the block, `run_context()` describes the child: each step has its
  own `idempotency_key`.
- Held open across an async generator's `yield` (a streamed model call), pass
  `current=False`: the consumer's code runs between the yields in the same
  context, and must not run inside the step. Nothing nests under such a
  step, and `run_context()` keeps describing the op.
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

## Durable runs: resume after a crash

With a `journal=`, each op's result and the cells it wrote are recorded as
it ends. A run that stopped — the process killed, a deploy — continues
with `resume`, on any worker that opens the same journal. What had ended is
not run again; what had not, runs.

```python
import asyncio

from operonx import END, START, Operon, graph, op
from operonx.durable import SqliteJournal

CHARGED = []
WORKER = {"dies": True}


@op
async def charge(order: int) -> dict:
    CHARGED.append(order)  # a side effect that must not happen twice
    return {"receipt": f"R-{order}"}


@op
async def ship(receipt: str) -> dict:
    if WORKER["dies"]:
        raise SystemExit("the worker died")  # the process stops here
    return {"done": f"{receipt}:shipped"}


@graph
def order(order):
    c = charge(order=order)
    s = ship(receipt=c["receipt"])
    START >> c >> s >> END


async def main():
    engine = Operon(order, params={"order": None}, journal=SqliteJournal("runs.db"))
    try:
        await engine.run({"order": 42}, run_id="order-42")
    except SystemExit:
        WORKER["dies"] = False  # in real life: a new process, the same journal
    handle = await engine.resume("order-42")
    out = await handle.result()
    assert out["done"] == "R-42:shipped"
    assert CHARGED == [42]  # charged once: `charge` had ended, so it was replayed
    print([run.run_id for run in engine.runs(status="running")])  # the ones to resume


asyncio.run(main())
```

- **What a resume keeps:** every cell as the journal last had it, and each
  ended op's results, replayed so what came after it runs as before. An op
  that was running when the process died runs again — at least once — with
  the same `run_context().idempotency_key`, which is what an external call
  deduplicates on.
- **`durability=`** `"async"` (the default: a background writer; a crash
  loses at most the last steps, which run again), `"sync"` (each step
  committed before the next op sees it), `"exit"` (written when the run ends).
- **Values are journalled as JSON** and come back exactly: plain data, tuples, sets,
  bytes, dates, Decimal, UUID, and dataclasses, pydantic models and enums of them.
  Any other type fails the run, naming the op and the output.
- **The graph must be the one that ran:** `resume` refuses a graph whose ops,
  code or wiring changed (`allow_graph_change=True` to resume anyway).
- A generator that had yielded and not ended runs again, its first yields
  checked against the journal (`NonDeterministicResume` if they differ;
  `on_resume="fail"` refuses instead).
- Without `journal=` nothing is recorded, and the scheduler pays one
  `is None` test per op.

### Approvals that outlive the process, and drains

With a journal, an `InterruptOp` does not hold the process while it waits
for a person: the run **parks**. Its question is journalled, the ops in
flight finish, nothing new starts, and the run ends with status
`interrupted`; its result lists the questions. Answer them days later, in
any process, with `resume(..., answers=)`.

```python
import asyncio

from operonx import END, START, InterruptOp, Operon, graph, op
from operonx.durable import SqliteJournal


@op
async def propose(amount: int) -> dict:
    return {"refund": amount}


@op
async def pay(response: str, refund: int) -> dict:
    return {"paid": refund if response == "approve" else 0}


@graph
def refunds(amount):
    p = propose(amount=amount)
    ask = InterruptOp(payload=p["refund"])
    r = pay(response=ask["response"], refund=p["refund"])
    START >> p >> ask >> r >> END


async def main():
    engine = Operon(refunds, params={"amount": None}, journal=SqliteJournal("refunds.db"))
    out = await engine.run({"amount": 30}, run_id="refund-7")
    [question] = out["$interrupted"]  # {interrupt_id, op, ctx, payload}
    assert question["payload"] == 30

    # later — another process with the same journal
    handle = await engine.resume("refund-7", answers={question["interrupt_id"]: "approve"})
    assert (await handle.result())["paid"] == 30


asyncio.run(main())
```

- A question left unanswered parks the run again; an answer to a question
  the run never asked is refused.
- With a journal the interrupt's `timeout` does not apply — nothing waits.
- **`await handle.drain()`** stops a run for a deploy: nothing new starts,
  what runs finishes, status `drained`, `"$drained": True` in its result.
  `resume(run_id)` on the next worker continues it.
- A graph that reads through a door (`ingress`) is journalled but not
  resumable: its items came from a live connection the journal does not hold.

### Threads: cells a conversation keeps between runs

`carry=` names declared cells a thread keeps: a run started with
`thread_id=T` begins with them as T's last run left them, and saves them
when it ends. Threads live in the journal, so any process sees them.

```python
import asyncio
import operator

from operonx import END, PARENT, START, Operon, graph, op
from operonx.durable import SqliteJournal


@op
def answer(text: str, history: list) -> dict:
    return {"said": [text], "turn": len(history) + 1}


@graph
def chat(text):
    PARENT.declare(history=[], reducers={"history": operator.add})
    a = answer(text=text, history=PARENT["history"])
    a["said"] >> PARENT["history"]
    START >> a >> END


async def main():
    engine = Operon(
        chat, params={"text": None}, journal=SqliteJournal("chat.db"), carry=["history"]
    )
    await engine.run({"text": "hello"}, thread_id="cust-7")
    out = await engine.run({"text": "and again"}, thread_id="cust-7")
    state = out["$state"]
    assert state[state.schema.name, "history"] == ["hello", "and again"]


asyncio.run(main())
```

- Only declared cells (`PARENT.declare`) can be carried; a run without a
  `thread_id` starts fresh. A resumed run starts from what its thread gave
  it when it began, not from what the thread holds now.
