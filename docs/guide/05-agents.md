# Agents

An "agent" in Operonx is a workflow that loops on an LLM call until a
stopping condition is met. The LLM decides the next action; your ops
execute it.

## Pattern: tool-calling loop

```python
import asyncio
import operonx
from operonx.core import Operon, GraphOp, op, START, END, PARENT
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

async def main():
    operonx.bootstrap()

    with GraphOp.loop(until="done == True", messages=[], done=False) as loop:
        llm = LLMOp.of(resource="gpt-4o", prompt=PARENT["messages"])
        parsed = parse_action(content=llm["content"])
        executed = execute_action(action=parsed["action"])
        appended = append_messages(
            messages=PARENT["messages"], observation=executed["observation"]
        )

        appended["messages"] >> PARENT["messages"]
        parsed["done"] >> PARENT["done"]

        START >> llm >> parsed >> executed >> appended >> END

    initial = [
        {"role": "system", "content": "You can call tools. End with FINAL: <answer>."},
        {"role": "user", "content": "What is the weather in Hanoi?"},
    ]
    result = await Operon(loop).run(inputs={"messages": initial})
    print(result["messages"][-1])

asyncio.run(main())
```

## Tips

- Use `GraphOp.loop` (not a generator op) for agent loops — each
  iteration depends on the previous, so streaming fan-out doesn't apply.
- Keep tool dispatch in a single `@op` and route by the parsed action
  type. Don't wire one op per tool — the LLM drives selection at runtime.
- Cap the loop with `until=` plus a max-iterations counter so you don't
  spin forever on a model that never says `FINAL:`.

## Where to go next

- Stream the LLM tokens: [Streaming](06-streaming.md).
- Trace every step in the loop: [Tracing](07-tracing.md).
