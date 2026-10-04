"""The seven caps each trip, at the right moment, and a child run's spending
counts toward its parent's.

Every scripted reply spends 10 prompt and 3 completion tokens unless it
says otherwise, so each cap's boundary is arithmetic on those numbers.
"""

from __future__ import annotations

import asyncio
import time

import pytest

from operonx_agents import (
    Agent,
    InMemorySession,
    Model,
    RunContext,
    Runner,
    UsageLimits,
    tool,
)
from operonx_agents.context.compaction import unmatched_tool_calls
from tests.agents import RAN, asks, make, roles, says
from tests.fakes import ScriptedLLM


@pytest.fixture(autouse=True)
def _ran():
    RAN.clear()
    yield


def looping(n: int = 10):
    """A model that asks for a tool every turn."""
    return [asks(("echo", {"a": i}), turn=i) for i in range(n)]


async def test_turns(hub):
    agent, llm = make(hub, *looping(), limits=UsageLimits(turns=2))
    res = await Runner.run(agent, "go")
    assert (res.status, res.limit_hit, llm.calls) == ("limit", "turns", 2)
    assert llm.requests[-1]["tool_choice"] == "none"


async def test_tool_calls(hub):
    """Calls past the cap are answered "not run"; the next turn is the last
    and cannot call tools."""
    three = asks(("echo", {"a": 1}), ("echo", {"a": 2}), ("echo", {"a": 3}))
    agent, llm = make(hub, three, says("partial"), limits=UsageLimits(tool_calls=2))
    res = await Runner.run(agent, "go")
    assert (res.status, res.limit_hit, res.output) == ("limit", "tool_calls", "partial")
    assert RAN == ["echo:1", "echo:2"]
    cut = res.messages[4]
    assert cut["tool_call_id"] == "t0_2" and "tool-call budget" in cut["content"]
    assert "tool-call budget" in res.messages[5]["content"] and res.messages[5]["role"] == "user"
    assert llm.requests[-1]["tool_choice"] == "none"


async def test_input_tokens_before_the_call(hub):
    """10 + 10 fits under 25; a third prompt of at least 10 would not."""
    agent, llm = make(hub, *looping(), limits=UsageLimits(input_tokens=25))
    res = await Runner.run(agent, "go")
    assert (res.status, res.limit_hit, llm.calls, res.usage.input_tokens) == (
        "limit",
        "input_tokens",
        2,
        20,
    )


async def test_input_tokens_after_the_call(hub):
    """One prompt over the cap: the reply's calls are answered, not run."""
    agent, llm = make(
        hub, asks(("echo", {"a": 1}), prompt_tokens=40), limits=UsageLimits(input_tokens=25)
    )
    res = await Runner.run(agent, "go")
    assert (res.status, res.limit_hit, llm.calls) == ("limit", "input_tokens", 1)
    assert RAN == [] and unmatched_tool_calls(res.messages)["calls_without_results"] == []


async def test_output_tokens(hub):
    agent, llm = make(hub, *looping(), limits=UsageLimits(output_tokens=5))
    res = await Runner.run(agent, "go")
    assert (res.status, res.limit_hit, llm.calls, res.usage.output_tokens) == (
        "limit",
        "output_tokens",
        2,
        6,
    )


async def test_total_tokens(hub):
    """13 + 13 = 26, and the next prompt of at least 10 would pass 30."""
    agent, llm = make(hub, *looping(), limits=UsageLimits(total_tokens=30))
    res = await Runner.run(agent, "go")
    assert (res.status, res.limit_hit, llm.calls) == ("limit", "total_tokens", 2)


async def test_cost_usd(hub):
    """0.016 per call (10 × 0.001 + 3 × 0.002): the second goes over 0.03."""
    agent, llm = make(
        hub, *looping(), limits=UsageLimits(cost_usd=0.03), llm_kw={"cost": (0.001, 0.002)}
    )
    res = await Runner.run(agent, "go")
    assert (res.status, res.limit_hit, llm.calls) == ("limit", "cost_usd", 2)
    assert res.usage.cost_usd == pytest.approx(0.032)


async def test_cost_usd_on_an_unpriced_resource_fails_loudly(hub):
    agent, _ = make(hub, *looping(), limits=UsageLimits(cost_usd=0.03))
    res = await Runner.run(agent, "go")
    assert res.status == "failed" and "declares no price" in res.error


async def test_wall_s_cuts_the_turn_and_writes_nothing_for_it(hub):
    agent, _ = make(hub, asks(("slow", {"seconds": 5})), limits=UsageLimits(wall_s=0.2))
    session = InMemorySession()
    start = time.perf_counter()
    res = await Runner.run(agent, "go", session=session)
    took = time.perf_counter() - start
    assert (res.status, res.limit_hit) == ("limit", "wall_s")
    assert 0.2 <= took < 0.3, took
    assert RAN == [] and await session.get_items() == [] and res.turns == 0


async def test_a_spent_wall_ends_before_the_next_call(hub):
    """A commit is never cut (it writes a whole turn or nothing), so a slow
    one can spend the budget; the next turn then does not start."""
    agent, llm = make(hub, asks(("echo", {"a": 1})), says(), limits=UsageLimits(wall_s=0.1))

    class SlowSession(InMemorySession):
        async def add_items(self, items):
            await asyncio.sleep(0.15)
            await super().add_items(items)

    session = SlowSession()
    res = await Runner.run(agent, "go", session=session)
    assert (res.status, res.limit_hit, llm.calls, res.turns) == ("limit", "wall_s", 1, 1)
    assert roles(await session.get_items()) == ["user", "assistant", "tool"]


async def test_a_limit_keeps_the_best_answer_so_far(hub):
    agent, _ = make(
        hub,
        says("first"),
        limits=UsageLimits(output_tokens=2),
    )
    res = await Runner.run(agent, "go")
    assert (res.status, res.limit_hit, res.output) == ("limit", "output_tokens", "first")


async def test_each_limit_is_validated():
    for name in ("turns", "tool_calls", "input_tokens", "output_tokens", "total_tokens"):
        with pytest.raises(ValueError, match=name):
            UsageLimits(**{name: 0})
    with pytest.raises(ValueError, match="whole number"):
        UsageLimits(turns=1.5)
    with pytest.raises(ValueError, match="wall_s"):
        UsageLimits(wall_s=-1)


async def test_child_usage_counts_toward_the_parent(hub):
    """The parent spends 13, its tool's child run 26: the parent's next
    prompt (at least 10) would pass its 40-token cap. Without the child's
    spending it would have fit (13 + 10)."""
    child_llm = ScriptedLLM(asks(("echo", {"a": 9})), says("child answer"))
    parent_llm = ScriptedLLM(asks(("delegate", {"task": "look"})), says("parent answer"))
    hub(parent=parent_llm, child=child_llm)
    child_agent = Agent(name="helper", model=Model("child"), tools=[make_echo()])
    seen = {}

    @tool
    async def delegate(ctx: RunContext, task: str) -> str:
        """Hand a task to the helper agent."""
        res = await Runner.run(child_agent, task, parent=ctx)
        seen["child"] = res.usage
        seen["parent_during"] = ctx.usage
        return res.output

    parent = Agent(
        name="boss",
        model=Model("parent"),
        tools=[delegate],
        limits=UsageLimits(total_tokens=40),
    )
    res = await Runner.run(parent, "go")
    assert seen["child"].total_tokens == 26
    assert seen["parent_during"].total_tokens == 39, "the parent's meter saw the child"
    assert res.usage.total_tokens == 39 and res.usage.requests == 3
    assert (res.status, res.limit_hit, parent_llm.calls) == ("limit", "total_tokens", 1)
    assert roles(res.messages) == ["user", "assistant", "tool"]


def make_echo():
    from tests.agents import echo

    return echo
