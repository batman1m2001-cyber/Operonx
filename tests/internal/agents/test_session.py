"""Multi-turn sessions.

The failure a session most easily introduces is history corruption:
duplicating earlier turns, dropping the system prompt, or losing what the
agent just said. These tests mostly check the conversation is exactly
what it should be after several exchanges.
"""

from __future__ import annotations

import pytest

from operonx.agents.graphs.react import build_react_agent
from operonx.agents.session import AgentSession
from operonx.agents.tool import clear_registry, tool
from operonx.core import op

pytestmark = pytest.mark.unit

NUM = {"type": "object", "properties": {"a": {"type": "number"}}, "required": ["a"]}


@pytest.fixture(autouse=True)
def _tools():
    clear_registry()
    deleted = []

    @tool(name="echo", description="Echo.", schema=NUM)
    async def echo(a: float) -> dict:
        return {"a": a}

    @tool(name="wipe", description="Wipe.", schema=NUM, destructive=True)
    async def wipe(a: float) -> dict:
        deleted.append(a)
        return {"gone": a}

    yield deleted
    clear_registry()


def replying_model(script=None):
    """Answers immediately unless `script` supplies tool calls."""
    script = script or []
    state = {"i": 0}

    @op
    def call_model(messages: list = None) -> dict:
        i = state["i"]
        state["i"] += 1
        calls, done = script[i] if i < len(script) else ([], True)
        return {
            "assistant_message": [{"id": f"a{i}", "role": "assistant", "content": f"reply {i}"}],
            "tool_calls": calls,
            "done": done,
        }

    return call_model


def make_session(script=None, **kw):
    agent = build_react_agent(call_model=replying_model(script), max_turns=6, approval_timeout=2.0)(
        messages=None
    )
    return AgentSession(agent, **kw)


class TestConversationContinuity:
    @pytest.mark.asyncio
    async def test_second_turn_sees_the_first(self):
        session = make_session()
        await session.send("first question")
        await session.send("second question")
        contents = [m.get("content") for m in session.messages]
        assert "first question" in contents
        assert "second question" in contents

    @pytest.mark.asyncio
    async def test_history_is_not_duplicated(self):
        """The agent's cell already holds the full conversation, so
        appending its output would duplicate every earlier turn."""
        session = make_session()
        await session.send("q1")
        await session.send("q2")
        await session.send("q3")
        users = [m for m in session.messages if m.get("role") == "user"]
        assert [m["content"] for m in users] == ["q1", "q2", "q3"]

    @pytest.mark.asyncio
    async def test_assistant_replies_are_kept(self):
        session = make_session()
        await session.send("q1")
        await session.send("q2")
        assert len([m for m in session.messages if m.get("role") == "assistant"]) == 2

    @pytest.mark.asyncio
    async def test_turns_accumulate_across_runs(self):
        session = make_session()
        await session.send("q1")
        first = session.turns
        await session.send("q2")
        assert session.turns > first


class TestSystemPrompt:
    @pytest.mark.asyncio
    async def test_stays_first_across_turns(self):
        """The prefix must not move, or nothing caches."""
        session = make_session(system="be terse")
        await session.send("q1")
        await session.send("q2")
        assert session.messages[0] == {"role": "system", "content": "be terse"}

    @pytest.mark.asyncio
    async def test_appears_exactly_once(self):
        session = make_session(system="be terse")
        await session.send("q1")
        await session.send("q2")
        assert sum(1 for m in session.messages if m.get("role") == "system") == 1

    def test_blank_system_is_not_added(self):
        assert make_session(system="   ").messages == []


class TestMessagesAccessor:
    @pytest.mark.asyncio
    async def test_returns_a_copy(self):
        """A caller mutating this must not silently rewrite what the next
        turn sends."""
        session = make_session()
        await session.send("q1")
        session.messages.append({"role": "user", "content": "injected"})
        assert all(m.get("content") != "injected" for m in session.messages)


class TestReset:
    @pytest.mark.asyncio
    async def test_keeps_the_system_prompt_by_default(self):
        session = make_session(system="be terse")
        await session.send("q1")
        session.reset()
        assert session.messages == [{"role": "system", "content": "be terse"}]
        assert session.turns == 0

    @pytest.mark.asyncio
    async def test_can_drop_everything(self):
        session = make_session(system="be terse")
        await session.send("q1")
        session.reset(keep_system=False)
        assert session.messages == []


class TestApproval:
    @pytest.mark.asyncio
    async def test_gated_tool_runs_once_approved(self, _tools):
        calls = [{"id": "t0", "name": "wipe", "args": {"a": 9}}]
        session = make_session(script=[(calls, False)])
        seen = []

        def on_approval(event):
            seen.append(event.payload["tool"])
            session.approve(event, True)

        await session.send("clean up", on_approval=on_approval)
        assert seen == ["wipe"]
        assert _tools == [9]

    @pytest.mark.asyncio
    async def test_denial_blocks_the_tool(self, _tools):
        calls = [{"id": "t0", "name": "wipe", "args": {"a": 9}}]
        session = make_session(script=[(calls, False)])

        await session.send("clean up", on_approval=lambda e: session.approve(e, False))
        assert _tools == []

    @pytest.mark.asyncio
    async def test_approve_reports_a_stale_event(self, _tools):
        """A human answering an expired prompt would otherwise believe
        they authorised something that never ran."""
        calls = [{"id": "t0", "name": "wipe", "args": {"a": 9}}]
        session = make_session(script=[(calls, False)])
        captured = []

        def on_approval(event):
            captured.append(event)
            session.approve(event, True)

        await session.send("go", on_approval=on_approval)
        # Answering the same event again, after the run finished.
        assert session.approve(captured[0], True) is False

    @pytest.mark.asyncio
    async def test_subscription_does_not_leak_between_turns(self, _tools):
        """A listener left bound would fire again on a later run and
        auto-answer a prompt the caller never saw."""
        calls = [{"id": "t0", "name": "wipe", "args": {"a": 1}}]
        session = make_session(script=[(calls, False), ([], True), (calls, False)])
        first_turn = []

        await session.send(
            "one", on_approval=lambda e: (first_turn.append(e), session.approve(e, True))[-1]
        )
        count_after_first = len(first_turn)

        # No callback this time — the previous one must not still be live.
        await session.send("two")
        assert len(first_turn) == count_after_first
