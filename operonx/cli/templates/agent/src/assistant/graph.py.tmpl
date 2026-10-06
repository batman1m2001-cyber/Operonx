"""The assistant: who it is, what it thinks with, and which tools it owns.

`ASSISTANT` is a spec — data, not a class to subclass. `app/main.py` serves
it. Its loop runs inside one op, and every turn, model call and tool call
is recorded under that op: a run's trace reads agent → turn → model,
word_count.
"""

from operonx.agents import Agent, Model, UsageLimits

from assistant.ops import word_count

ASSISTANT = Agent(
    name="assistant",
    model=Model("assistant", deadline=60),  # llm:assistant in resources.yaml
    instructions="You are a helpful assistant. Use the tools when they help.",
    tools=[word_count],
    limits=UsageLimits(turns=6, tool_calls=12),
)
