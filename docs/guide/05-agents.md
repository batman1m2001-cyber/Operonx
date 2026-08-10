# Agents

An "agent" in Operonx is a workflow that loops on an LLM call until a
stopping condition is met. The LLM decides the next action; your ops
execute it; the loop feeds the observation back into the next turn.

## Pattern: tool-calling loop

```python
import asyncio
import operonx
from operonx.core import Operon, graph, op, START, END, PARENT
from operonx.core.ops.flow.branch_op import if_
from operonx.providers import LLMOp

@op
def parse_action(content: str):
    # Parse the LLM's response into a structured action.
    if "FINAL:" in content:
        return {"final": content.split("FINAL:")[-1].strip(), "done": True}
    return {"action": content, "done": False}

@op
async def execute_action(action: str):
    # Replace with your tool dispatch.
    result = await dispatch_tool(action)
    return {"observation": result}

@op
def append_messages(messages: list, observation: str):
    return {
        "messages": messages + [
            {"role": "user", "content": f"Observation: {observation}"}
        ],
    }

@graph
def react_agent():
    # Loop state as shared cells. ``add_messages`` accumulates each turn's
    # LLM output + observations without clobbering prior messages.
    PARENT.declare(messages=[], done=False)

    llm = LLMOp.of(resource="gpt-4o", prompt=PARENT["messages"])
    parsed = parse_action(content=llm["content"])
    executed = execute_action(action=parsed["action"])
    appended = append_messages(
        messages=PARENT["messages"], observation=executed["observation"]
    )

    appended["messages"] >> PARENT["messages"]
    parsed["done"] >> PARENT["done"]

    # Back-edge: if not done, loop back to llm; else exit.
    START >> llm >> parsed >> executed >> appended
    appended >> if_(parsed["done"] == True, END).else_(llm)  # noqa: E712

async def main():
    operonx.bootstrap()
    initial = [
        {"role": "system", "content": "You can call tools. End with FINAL: <answer>."},
        {"role": "user", "content": "What is the weather in Hanoi?"},
    ]
    result = await Operon(react_agent()).run(inputs={"messages": initial})
    print(result["messages"][-1])

asyncio.run(main())
```

## Tips

- The back-edge `appended >> if_(...).else_(llm)` is what makes this a
  loop — the Phase 3 rewrite pass synthesizes a hidden `_GraphLoop` for
  the scheduler. Users write plain DAG shapes.
- Use a shared cell (`PARENT.declare(messages=[])`) for state carried
  across turns; add a reducer (`reducers={"messages": add_messages}`)
  if turns should APPEND rather than overwrite.
- Keep tool dispatch in a single `@op` and route by the parsed action
  type. Don't wire one op per tool — the LLM drives selection at runtime.
- Cap runaway loops via the synthesized loop's `max_iterations` default
  (1000) — the branch is your primary exit; the cap is the safety valve.

## Where to go next

- Stream the LLM tokens: [Streaming](06-streaming.md).
- Trace every step in the loop: [Tracing](07-tracing.md).
