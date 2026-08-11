"""The ReAct loop, run rather than built.

A graph that compiles is not a graph that runs. Every defect in §15.1
V6–V10 was invisible at build time: the loop capped at one iteration, an
op after it never became ready, `collect()` handed over a dict where the
reducer wanted a list. All of them produced a *plausible* partial answer
rather than an error, so these tests assert on message ordering and turn
counts, not just on completion.
"""

from __future__ import annotations

import asyncio

import pytest

from operonx.agents.graphs.react import (
    EMPTY_RESULT,
    agent_result,
    build_react_agent,
)
from operonx.agents.tool import clear_registry, tool
from operonx.checkpoint import bind_interrupt_bus
from operonx.core import Operon, op

pytestmark = pytest.mark.unit

NUM = {"type": "object", "properties": {"a": {"type": "number"}}, "required": ["a"]}
CALL = [{"id": "t0", "name": "echo", "args": {"a": 1}}]
USER = [{"id": "u0", "role": "user", "content": "hi"}]


@pytest.fixture(autouse=True)
def _tools():
    clear_registry()
    deleted = []

    @tool(name="echo", description="Echo a number.", schema=NUM)
    async def echo(a: float) -> dict:
        return {"a": a}

    @tool(name="wipe", description="Delete everything.", schema=NUM, destructive=True)
    async def wipe(a: float) -> dict:
        deleted.append(a)
        return {"gone": a}

    yield deleted
    clear_registry()


def scripted_model(script):
    """A model op replaying ``script`` — [(tool_calls, done), ...].

    Past the end it answers and finishes, so a runaway loop shows up as a
    turn count rather than a hang.
    """
    state = {"i": 0}

    @op
    def call_model(messages: list = None) -> dict:
        i = state["i"]
        state["i"] += 1
        calls, done = script[i] if i < len(script) else ([], True)
        return {
            "assistant_message": [
                {"id": f"a{i}", "role": "assistant", "content": "final" if done else f"turn {i}"}
            ],
            "tool_calls": calls,
            "done": done,
        }

    return call_model


async def run_agent(script, *, max_turns=25, answer=None, approval_timeout=5.0, messages=USER):
    built = build_react_agent(
        call_model=scripted_model(script),
        max_turns=max_turns,
        approval_timeout=approval_timeout,
    )(messages=None)
    engine = Operon(built)
    handle = engine.start(inputs={"messages": messages})
    prompts = []

    def sink(evt):
        prompts.append(evt.payload)
        if answer is not None:
            handle.state.resume_interrupt(evt.interrupt_id, answer)

    bind_interrupt_bus(handle.state, sink=sink)
    await asyncio.wait_for(handle.result(), timeout=60)
    # handle.result() is built from emitted frames and carries no state,
    # so read the cells through the handle's own MemoryState.
    return agent_result(handle.state, built), prompts


def roles(result):
    return [m.get("role") for m in result["messages"]]


class TestTurns:
    @pytest.mark.asyncio
    async def test_no_tool_call_is_one_turn(self):
        result, _ = await run_agent([([], True)])
        assert result["turns"] == 1
        assert roles(result) == ["user", "assistant"]
        assert result["final"]["content"] == "final"

    @pytest.mark.asyncio
    async def test_one_tool_turn(self):
        result, _ = await run_agent([(CALL, False)])
        assert result["turns"] == 2
        assert roles(result) == ["user", "assistant", "tool", "assistant"]

    @pytest.mark.asyncio
    async def test_loop_actually_iterates(self):
        """The defect this guards capped every such loop at one turn, and
        did it silently — the answer just looked short."""
        result, _ = await run_agent([(CALL, False), (CALL, False)])
        assert result["turns"] == 3
        assert roles(result) == [
            "user",
            "assistant",
            "tool",
            "assistant",
            "tool",
            "assistant",
        ]

    @pytest.mark.asyncio
    async def test_the_opening_messages_survive(self):
        result, _ = await run_agent([([], True)])
        assert result["messages"][0]["content"] == "hi"

    @pytest.mark.asyncio
    async def test_parallel_tool_calls_all_answered(self):
        """Every tool_call needs a matching result or the provider 400s."""
        calls = [{"id": f"t{i}", "name": "echo", "args": {"a": i}} for i in range(4)]
        result, _ = await run_agent([(calls, False)])
        tool_ids = {m["tool_call_id"] for m in result["messages"] if m.get("role") == "tool"}
        assert tool_ids == {"t0", "t1", "t2", "t3"}


class TestBudget:
    @pytest.mark.asyncio
    async def test_stops_at_the_cap(self):
        never_finishes = [(CALL, False)] * 50
        result, _ = await run_agent(never_finishes, max_turns=3)
        assert result["turns"] == 3
        assert result["stopped_early"] is True

    @pytest.mark.asyncio
    async def test_exhaustion_is_graceful_not_a_cut(self):
        """The model is told, and gets a final turn — otherwise the caller
        receives a truncated run with no answer in it."""
        result, _ = await run_agent([(CALL, False)] * 50, max_turns=3)
        assert roles(result) == [
            "user",
            "assistant",
            "tool",
            "assistant",
            "tool",
            "user",
            "assistant",
        ]
        notice = result["messages"][-2]
        assert "budget" in notice["content"].lower()
        assert result["final"]["role"] == "assistant"

    @pytest.mark.asyncio
    async def test_finishing_early_is_not_flagged_as_stopped(self):
        result, _ = await run_agent([([], True)], max_turns=3)
        assert result["stopped_early"] is False

    def test_zero_budget_rejected(self):
        with pytest.raises(ValueError, match=r"max_turns must be >= 1"):
            build_react_agent(call_model=scripted_model([]), max_turns=0)


class TestFailuresReachTheModel:
    @pytest.mark.asyncio
    async def test_unknown_tool_does_not_end_the_run(self):
        bad = [{"id": "t0", "name": "nope", "args": {}}]
        result, _ = await run_agent([(bad, False)])
        tool_msgs = [m for m in result["messages"] if m.get("role") == "tool"]
        assert len(tool_msgs) == 1
        assert tool_msgs[0]["status"] == "error"
        assert result["turns"] == 2, "the model gets another turn to correct itself"


class TestDestructiveTools:
    @pytest.mark.asyncio
    async def test_approval_gates_the_call(self, _tools):
        calls = [{"id": "t0", "name": "wipe", "args": {"a": 5}}]
        result, prompts = await run_agent([(calls, False)], answer={"approved": True})
        assert prompts[0]["tool"] == "wipe"
        assert _tools == [5]
        assert result["turns"] == 2

    @pytest.mark.asyncio
    async def test_denial_still_produces_a_tool_message(self, _tools):
        calls = [{"id": "t0", "name": "wipe", "args": {"a": 5}}]
        result, _ = await run_agent([(calls, False)], answer={"approved": False})
        assert _tools == []
        tool_msgs = [m for m in result["messages"] if m.get("role") == "tool"]
        assert len(tool_msgs) == 1 and tool_msgs[0]["status"] == "error"


class TestAgentResult:
    def test_missing_state_gives_the_empty_shape(self):
        """Callers index ["messages"] unconditionally; an empty answer
        means an op raised, not that the agent was silent."""
        assert agent_result({}, object()) == EMPTY_RESULT

    @pytest.mark.asyncio
    async def test_final_is_the_last_assistant_message(self):
        result, _ = await run_agent([(CALL, False)])
        assert result["final"] is result["messages"][-1]
        assert result["final"]["role"] == "assistant"

    @pytest.mark.asyncio
    async def test_messages_are_flat_not_per_turn_snapshots(self):
        """Indexing the raw run() dict yields a list of per-iteration
        writes; agent_result must return the merged conversation."""
        result, _ = await run_agent([(CALL, False), (CALL, False)])
        assert all(isinstance(m, dict) for m in result["messages"])
