# Agents: operonx-agents

Agents live in their own package, **operonx-agents**, built on operonx and
imported as `operonx.agents` (an alias of `operonx_agents`; both names are
the same modules). The old built-in `operonx.agents` was removed in 1.16;
`MIGRATION.md` maps each of its names to the new one.

```bash
pip install operonx-agents    # until it is on PyPI: from its repository
```

An agent is a spec — `Agent(name, model, instructions, tools, limits,
...)`, data, not a class to subclass — and `Runner` runs it: model → tools
→ model, as plain async code inside one op. Every turn, model call and
tool call is recorded under that op, so a trace reads `agent → turn[i] →
model, <tool>`. For a typed, deadline-bounded single model call inside a
graph (a classifier, an extractor), use `llm_step` instead: see the
operonx-agents README.

## An agent, a tool, a run

```yaml file=resources.yaml
llm:assistant:
  api_type: openai
  api_key: ${LLM_API_KEY}
  base_url: ${LLM_BASE_URL}
  model: gpt-4o-mini
```

```python file=orders.py
from operonx.agents import Agent, Model, UsageLimits, tool

ORDERS = {"A1": "shipped", "A2": "processing"}


@tool(readonly=True)
def order_status(order_id: str) -> str:
    """The shipping status of an order.

    Args:
        order_id: The order's id, like A1.
    """
    return f"{order_id} is {ORDERS.get(order_id, 'unknown')}"


support = Agent(
    name="support",
    model=Model("assistant"),  # llm:assistant in resources.yaml
    instructions="You answer questions about orders. Use the tools.",
    tools=[order_status],
    limits=UsageLimits(turns=6, tool_calls=10),
)
```

```python
import asyncio

import operonx
from operonx.agents import Runner

from orders import support


async def main():
    operonx.bootstrap(resources="resources.yaml")
    res = await Runner.run(support, "What is the status of order A1?")
    assert res.status == "completed", res.error
    assert "A1 is shipped" in res.output
    assert res.turns == 2 and res.usage.input_tokens > 0


asyncio.run(main())
```

- `@tool` reads what the model is shown from the signature and the
  docstring (`Args:`), and validates the model's arguments before the
  function runs; a bad argument becomes one tool message naming the field,
  which the model reads and fixes. A tool is still a function: call it to
  test it.
- An agent runs only the tools it owns. A model that names another
  agent's tool gets "no tool named ...".
- `res.status` is `completed`, `limit` (a `UsageLimits` cap ran out:
  `res.limit_hit`), `interrupted` (a call waits for a human), `blocked` (a
  hook's tripwire) or `failed` (`res.error`, never a silent `{}`).
- `Runner.stream(agent, input)` yields the events as they happen
  (`RunStarted`, `TextDelta`, `ToolCallStarted`/`Finished`,
  `ApprovalRequired`, ..., `RunFinished` with the result).
- `session=` (`InMemorySession`, `RedisSession`, `SQLiteSession`) keeps a
  conversation across runs.

## Approvals: a run that stops for a human

A tool with `approval=` does not run until someone says yes. The run does
not wait in the process: it saves its state and ends `interrupted`, and
`Runner.resume` finishes it — in any process that opens the same store.

```python
import asyncio

import operonx
from operonx.agents import Agent, Approve, Model, Runner, SQLiteStateStore, tool

REFUNDED = []


@tool(idempotent=False, approval=lambda ctx, args: args["amount"] > 500)
def refund(order_id: str, amount: int) -> str:
    """Refund an order. Over 500 needs a human."""
    REFUNDED.append((order_id, amount))
    return f"refunded {amount} on {order_id}"


cashier = Agent(name="cashier", model=Model("assistant"), tools=[refund])


async def main():
    operonx.bootstrap(resources="resources.yaml")
    store = SQLiteStateStore("runs.db")
    res = await Runner.run(cashier, "Please refund 900 on A1", store=store)
    assert res.status == "interrupted" and REFUNDED == []
    (asked,) = res.interruptions
    assert (asked.tool, asked.args) == ("refund", {"order_id": "A1", "amount": 900})

    # later, anywhere the same store is open
    done = await Runner.resume(cashier, res.run_id, store=store, approvals={asked.id: Approve()})
    assert done.status == "completed" and REFUNDED == [("A1", 900)]


asyncio.run(main())
```

- `Deny("reason")` refuses the call; the model reads the refusal and the
  reason. An interruption left unanswered keeps waiting with the same id.
- `approval="always"` asks for every call; a `ToolPolicy` can `ask` or
  `deny` per tool. A policy's `deny` never reaches a human.

## Serve it: `agent_service`

`agent_service(agent, listener, store=...)` is an operonx `Service`:
list it in the `Application` like any other, and `operonx serve` runs it.

```python file=shop.py
from operonx.app import Application, http
from operonx.agents import Agent, InMemoryStateStore, Model, agent_service, tool


@tool(idempotent=False, approval=lambda ctx, args: args["amount"] > 500)
def refund(order_id: str, amount: int) -> str:
    """Refund an order. Over 500 needs a human."""
    return f"refunded {amount} on {order_id}"


cashier = Agent(name="cashier", model=Model("assistant"), tools=[refund])

APP = Application(
    "shop",
    services=[agent_service(cashier, http("POST", "/cashier"), store=InMemoryStateStore())],
)
```

```python
import json

import operonx
from operonx.app.serve.app import build_app
from starlette.testclient import TestClient

from shop import APP

operonx.bootstrap(resources="resources.yaml")
with TestClient(build_app(APP.services)) as client:
    parked = client.post("/cashier", json={"input": "Refund 900 on A1"}).json()
    assert parked["status"] == "interrupted"
    (asked,) = parked["interruptions"]

    answer = {"run_id": parked["run_id"], "approvals": {asked["id"]: "approve"}}
    done = client.post("/cashier/resume", json=answer).json()
    assert done["status"] == "completed" and done["output"] == "Done: refunded 900 on A1"

    # the same door, read as it happens: one server-sent event per event
    stream = client.post(
        "/cashier", json={"input": "Refund 20 on A2"}, headers={"accept": "text/event-stream"}
    )
    events = [json.loads(x[6:]) for x in stream.text.splitlines() if x.startswith("data: ")]
    assert events[0]["type"] == "RunStarted" and events[-1]["type"] == "RunFinished"
    assert events[-1]["result"]["output"] == "Done: refunded 20 on A2"
```

- A run is `{"input": "...", "session_id"?: "..."}` (or a bare string); a
  resume is `{"run_id": ..., "approvals": {"<id>": "approve" | "deny" |
  {"deny": "<reason>"}}}`, sent to `POST <path>/resume` — the route
  `Service(resume=)` adds to an http door.
- A JSON caller gets the result: `status`, `output`, `run_id`,
  `interruptions`, `usage`, `turns`, `error`. A caller sending `Accept:
  text/event-stream`, and every `websocket(...)` door, gets each event as
  it happens, the last `RunFinished` with that result. A websocket's
  requests (runs and resumes) run in turn on its connection.
- A body it cannot read is answered `{"status": "invalid", "error": ...}`
  and runs nothing.
- `store=` is required: an interrupted run waits there, so use
  `SQLiteStateStore` or `RedisStateStore` when the process can restart.

## In a graph: `AgentOp`

```python
import asyncio

import operonx
from operonx import END, START, Operon, graph, op
from operonx.agents import AgentOp

from orders import support


@op
def shout(answer: str) -> dict:
    return {"loud": answer.upper()}


@graph
def flow(question):
    answered = AgentOp.of(agent=support, input=question)
    s = shout(answer=answered["output"])
    START >> answered >> s >> END


async def main():
    operonx.bootstrap(resources="resources.yaml")
    out = await Operon(flow, params={"question": None}).run(
        {"question": "What is the status of order A2?"}
    )
    assert out["loud"] == "DONE: A2 IS PROCESSING"


asyncio.run(main())
```

An agent is a spec, not an op: `AgentOp.of(agent=..., input=...)` runs it as
one step, like `LLMOp.of(...)`, with `session_id` and `deps` as optional
inputs. Its outputs are `output`, `status`, `usage`, `interruptions`,
`state_id`, `error`. `AgentOp.of(agent=..., stream=True, input=...)` is a
transient `event` stream for an `EmitOp` instead.

## Check the path: trajectory evals

An agent eval is an operonx `Eval` (page 7) of the service's graph. The
evaluators read how the answer was reached from the case's trace.

```json file=datasets/orders.jsonl
{"id": "a1", "input": "What is the status of order A1?", "trajectory": {"tool_calls": [{"name": "order_status", "args": {"order_id": "A1"}}]}}
{"id": "a2", "input": "Order A2: what is its status?", "trajectory": {"tool_calls": [{"name": "order_status", "args": {"order_id": "A2"}}]}}
{"id": "hello", "input": "Hello", "trajectory": {"tool_calls": []}}
```

```python
import operonx
from operonx.app import http
from operonx.app.evals import Eval, trajectory
from operonx.agents import InMemoryStateStore, agent_service
from operonx.agents.evals import no_tool_errors, output_valid, tool_not_called, turns_at_most

from orders import support

service = agent_service(support, http("POST", "/support"), store=InMemoryStateStore())

operonx.bootstrap(resources="resources.yaml")
ev = Eval(
    "orders",
    graph=service.graph,
    dataset="dataset:orders",
    evaluators=[
        trajectory.tool_calls(mode="strict", args="subset"),  # the case's "trajectory"
        no_tool_errors(),
        turns_at_most(3),
        tool_not_called("refund"),
        output_valid(str),
    ],
)
s = ev.run_sync().meta["eval"]
assert (s["cases"], s["pass_rate"]) == (3, 1.0)
```

- Also: `tool_called(name, args=..., times=...)`, `cost_at_most(usd)`.
  Each takes `agent=` to judge one agent's steps (an `as_tool` sub-agent
  records its own).
- `dataset_from_runs(store, RunFilter(...))` turns recorded runs into
  cases: what the agent was asked, the calls it made as the reference
  trajectory, its answer as `expected`.

## In tests: a scripted model

```python
import asyncio

from operonx.agents import Runner
from operonx.agents.testing import ScriptedLLM, asks, says, scripted

from orders import support


async def main():
    model = ScriptedLLM(asks(("order_status", {"order_id": "A2"})), says("A2 is on its way."))
    with scripted(assistant=model):  # llm:assistant answers from the script
        res = await Runner.run(support, "where is A2?")
    assert res.output == "A2 is on its way." and model.calls == 2


asyncio.run(main())
```

A script item may also be a function `(messages, params) -> reply`, for a
model that answers by rule.
