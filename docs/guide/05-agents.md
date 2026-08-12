# Agents

An agent is a loop: the model asks for tools, your ops run them, the
results go back, repeat until it answers. `operonx.agents` gives you the
loop, the tool registry, a permission gate and a turn budget, so what you
write is the tools and the model call.

## A working agent

```python
import asyncio
import operonx
from operonx.agents import agent_result, build_react_agent, get_tool_definitions, tool
from operonx.core import Operon
from operonx.providers import LLMOp

@tool(
    name="get_weather",
    description="Current weather for a city.",
    schema={
        "type": "object",
        "properties": {"city": {"type": "string"}},
        "required": ["city"],
    },
    readonly=True,
)
async def get_weather(city: str) -> dict:
    return {"temp_c": 21, "sky": "clear", "city": city}

def call_model(messages):
    return LLMOp.of(
        resource="gpt-4o",
        messages=messages,
        tools=get_tool_definitions(),
    )

async def main():
    operonx.bootstrap()
    agent = build_react_agent(call_model=call_model, max_turns=10)(messages=None)

    result = await Operon(agent).run(
        inputs={"messages": [{"role": "user", "content": "Weather in Hanoi?"}]}
    )

    answer = agent_result(result, agent)
    print(answer["final"]["content"])
    print(f"{answer['turns']} turns, stopped_early={answer['stopped_early']}")

asyncio.run(main())
```

`@tool` registers the function as both an `@op` (so it is a real graph
node with tracing and bound routing) and an LLM-callable tool. The two
schemas are separate on purpose: the signature drives graph wiring, the
JSON Schema drives the model's request payload, and neither can be
derived from the other.

## Read the result through `agent_result`

Do not index the run result directly. A graph reports its outputs as the
*stream of writes*, so with a loop `result["messages"]` is a list of
per-turn lists rather than the conversation. The reducer-merged value
lives in the shared cell, and `agent_result` reads it:

```python
answer = agent_result(result, agent)
answer["messages"]        # the conversation, flat
answer["final"]           # last assistant message, or None
answer["turns"]
answer["stopped_early"]   # True if the turn budget ran out
```

It needs the built graph because that is where the cells live. If you
drive the agent with `engine.start()` instead, pass `handle.state` —
`handle.result()` is built from emitted frames and carries no state.

An empty `messages` means an op raised. Operonx records errors into state
and returns a partial result rather than propagating, so check the logs
rather than concluding the model had nothing to say.

## Turn budget

`max_turns` is a real budget, not a kill switch. When it runs out the
model is told, and gets one final turn to answer with what it has:

```python
agent = build_react_agent(call_model=call_model, max_turns=10)
# ... roles: user, assistant, tool, assistant, tool, user(notice), assistant
```

This is deliberately not the synthesized loop's `max_iterations`, which
is a runaway guard set far above any real workload. That guard cuts
mid-flight and tells the model nothing, so you would get a truncated run
with no answer in it.

## Permission policy

A tool declares what it *is*; a policy decides what may happen to it
*here*. Same tool, different deployments:

```python
from operonx.agents import ToolPolicy

# Unattended batch: read freely, never write.
ToolPolicy(default="deny", readonly="allow")

# Interactive: ask before anything destructive, never shell out.
ToolPolicy(default="allow", destructive="ask", rules={"shell": "deny"})

agent = build_react_agent(call_model=call_model, policy=my_policy)
```

Resolution is most-specific-first: `rules[name]` → `destructive` /
`readonly` → `default`. An unrecognised outcome raises at construction
rather than falling through to the default, since falling through is how
a policy silently widens what an agent may do.

`deny` refuses outright and never reaches a human — asking someone to
approve what policy already forbids trains them to click through, and
the answer would be ignored anyway.

## Human approval

A tool marked `destructive=True` (or any tool a policy sets to `ask`)
suspends until a human answers. The caller drives that:

```python
from operonx.checkpoint import bind_interrupt_bus

handle = Operon(agent).start(inputs={"messages": messages})

def on_approval(event):
    print(f"Allow {event.payload['tool']} with {event.payload['args']}?")
    approved = input("[y/N] ").strip().lower() == "y"
    handle.state.resume_interrupt(event.interrupt_id, {"approved": approved})

bind_interrupt_bus(handle.state, sink=on_approval)
await handle.result()
answer = agent_result(handle.state, agent)
```

The payload carries the real tool name and arguments, so the human sees
what they are approving. Approvals arrive one at a time even when tool
calls fan out.

On denial or timeout the tool does not run and the model gets a message
saying so — those two cases read differently, because "a human declined"
and "nobody answered" warrant different next moves.

See [`ex09_agent_workflow`](https://github.com/batman1m2001-cyber/Operonx/tree/main/examples/python/ex09_agent_workflow)
for a runnable version.

## Every failure reaches the model

Providers reject a conversation in which an assistant `tool_call` has no
matching result, so every dispatch path returns exactly one tool message:
unknown tool, unparseable arguments, an exception inside the tool, a
timeout, a policy refusal, a human denial. The model reads the error and
corrects itself rather than the run ending.

Tool output is truncated to `max_result_chars` (default 100,000) and the
truncation is announced — a model shown half a file with no marker will
reason about it as if it were whole.

## Writing tools

```python
@tool(
    name="delete_file",
    description="Delete a file. Cannot be undone.",
    schema={...},
    destructive=True,       # routes through the approval gate
    timeout=30.0,           # per-call wall clock
    max_result_chars=4_000,
    bound="io",             # forwarded to @op
)
async def delete_file(path: str) -> dict:
    ...
```

The description is the only thing the model reads when deciding to call
the tool, so an empty one is rejected at import. So is a duplicate name —
it would silently shadow another tool — and a schema that is not a JSON
Schema object, which the provider would otherwise reject with an error
naming the request rather than the tool.

## Multi-turn — `AgentSession`

One `Operon.run()` is one *exchange*. A session threads the history across
several:

```python
from operonx.agents import AgentSession

session = AgentSession(agent, system="You are terse.")
await session.send("what files are here?")
await session.send("delete the second one")      # sees turn 1
```

Pass `on_approval=` whenever gated tools are reachable — without it a
gated call waits out its full `approval_timeout` with nobody to answer,
which reads as a hang rather than as a question.

## Context that survives a long conversation

`build_react_agent` runs a context stage before every model call:
compaction, memory retrieval, skill matching, prompt assembly and
cache-control placement.

```python
agent = build_react_agent(
    call_model=call_model,
    token_budget=100_000,        # compact the prompt above this
    keep_recent=6,               # exchanges kept verbatim
    memory_providers=[LocalMarkdownMemory("./memory.md")],
    skills=load_skills("./skills"),
)(messages=None)
```

Compaction shapes the **prompt**, not the stored conversation, so nothing
is lost irrecoverably and `agent_result` still returns everything that
happened.

Retrieved memory and matched skills are placed *after* the conversation,
not in the system prompt: they change per query, and leading with them
would push the whole history out of the provider's cached prefix.

## Tools from another process — MCP

Connect to a Model Context Protocol server and its tools become ordinary
`@tool` ops:

```python
from operonx.agents import MCPServer, connect_mcp

client, names = await connect_mcp(
    MCPServer(name="fs", command="npx", args=["-y", "@modelcontextprotocol/server-filesystem", "."])
)
try:
    agent = build_react_agent(call_model=..., )(messages=None)   # build AFTER registering
finally:
    await client.close()
```

Needs `operonx[mcp]`. Three things it does that are worth knowing:

- Tools are namespaced `server__tool`, so a third-party server cannot
  shadow a local one.
- A tool the server does **not** annotate as read-only is gated by
  default. Absent hints mean unknown, and unknown third-party code asks a
  human. If a live run seems to hang, this is usually why.
- Registration must happen **before** the graph is built —
  `get_tool_definitions()` reads the registry at build time.

## Running on a schedule — `Heartbeat`

For agents nobody is talking to:

```python
from operonx.agents import Heartbeat

hb = Heartbeat(session, "Check the queue and handle anything new.", interval=300)
await hb.start()
...
await hb.stop()
```

An overlapping beat is **skipped and counted** (`hb.skipped`) rather than
queued — a backlog never drains. A failing beat does not stop the clock.

## Where to go next

- Stream the model's tokens: [Streaming](06-streaming.md).
- Trace every tool call: [Tracing](07-tracing.md).
- Teach the agent from its own work: [Learning loop](09-learning-loop.md).
- Keep a credential out of the trace:
  [Observability](../architecture/observability.md).
- What is known-broken today:
  [Open findings](https://github.com/batman1m2001-cyber/Operonx/blob/main/docs/design/OPEN_FINDINGS.md).
