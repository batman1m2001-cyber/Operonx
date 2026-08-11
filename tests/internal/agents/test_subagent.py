"""Sub-agents.

Two things can go wrong that nothing downstream will catch. A child given
the full registry has every permission the parent had — there are no
capability tokens, so the construction site *is* the boundary. And a
child that can delegate spawns a tree, because a model that decided
delegation was the answer keeps deciding that.
"""

from __future__ import annotations

import asyncio

import pytest

from operonx.agents.graphs.subagent import (
    DELEGATE_BLOCKED_TOOLS,
    NO_TOOLS_MESSAGE,
    describe_delegation,
    make_delegate_tool,
)
from operonx.agents.tool import TOOL_REGISTRY, clear_registry, tool
from operonx.core import op

pytestmark = pytest.mark.unit

EMPTY = {"type": "object", "properties": {}}


@pytest.fixture(autouse=True)
def _registry():
    clear_registry()
    calls = []

    @tool(name="read", description="Read.", schema=EMPTY, readonly=True)
    async def read() -> dict:
        calls.append("read")
        return {"text": "file contents"}

    @tool(name="wipe", description="Wipe.", schema=EMPTY, destructive=True)
    async def wipe() -> dict:
        calls.append("wipe")
        return {"gone": True}

    yield calls
    clear_registry()


def answering_model(text="sub-agent answer", script=None):
    script = script or []
    state = {"i": 0}

    @op
    def call_model(messages: list = None) -> dict:
        i = state["i"]
        state["i"] += 1
        calls, done = script[i] if i < len(script) else ([], True)
        return {
            "assistant_message": [{"id": f"s{i}", "role": "assistant", "content": text}],
            "tool_calls": calls,
            "done": done,
        }

    return call_model


class TestToolsetIsFixedAtConstruction:
    def test_named_subset_is_what_the_child_gets(self):
        assert describe_delegation(allow_tools=["read"])["tools"] == ["read"]

    def test_unnamed_means_everything_minus_the_blocklist(self):
        """The convenient default is also the dangerous one, which is why
        the blocklist applies even when no subset was named."""
        out = describe_delegation(allow_tools=None)
        assert "read" in out["tools"] and "wipe" in out["tools"]

    def test_unregistered_names_are_dropped_not_invented(self):
        assert describe_delegation(allow_tools=["read", "ghost"])["tools"] == ["read"]

    def test_blocked_tools_never_reach_a_child(self):
        clear_registry()

        @tool(name="delegate", description="d", schema=EMPTY)
        async def delegate() -> dict:
            return {}

        @tool(name="send_message", description="s", schema=EMPTY)
        async def send_message() -> dict:
            return {}

        assert describe_delegation(allow_tools=None)["tools"] == []

    def test_describe_reports_what_was_withheld(self):
        """The difference between 'I restricted the sub-agent' and 'I
        passed the whole registry' is invisible at runtime."""
        out = describe_delegation(allow_tools=["read"])
        assert out["blocked"] == ["wipe"]


class TestDepth:
    def test_no_further_delegation_by_default(self):
        assert describe_delegation(allow_tools=None)["can_delegate_further"] is False

    def test_delegate_is_blocked_at_the_last_permitted_depth(self):
        clear_registry()

        @tool(name="delegate", description="d", schema=EMPTY)
        async def delegate() -> dict:
            return {}

        @tool(name="read", description="r", schema=EMPTY)
        async def read() -> dict:
            return {}

        deep = describe_delegation(allow_tools=None, max_depth=3, depth=0)
        last = describe_delegation(allow_tools=None, max_depth=3, depth=2)
        # `delegate` is in the blocklist regardless, so neither level gets
        # it — the depth guard is the second lock, not the only one.
        assert deep["can_delegate_further"] is False
        assert last["can_delegate_further"] is False

    def test_delegate_is_in_the_blocklist(self):
        assert "delegate" in DELEGATE_BLOCKED_TOOLS


class TestDelegation:
    @pytest.mark.asyncio
    async def test_returns_only_the_final_answer(self, _registry):
        """A sub-agent exists to spend context the parent need not hold;
        handing back the transcript would defeat the point."""
        delegate = make_delegate_tool(
            call_model=answering_model("the answer is 42"), allow_tools=["read"]
        )
        out = await asyncio.wait_for(delegate.__wrapped__(task="do a thing"), timeout=30)
        assert out["answer"] == "the answer is 42"
        assert "messages" not in out

    @pytest.mark.asyncio
    async def test_child_can_use_its_tools(self, _registry):
        calls = [{"id": "t0", "name": "read", "args": {}}]
        delegate = make_delegate_tool(
            call_model=answering_model(script=[(calls, False)]), allow_tools=["read"]
        )
        await asyncio.wait_for(delegate.__wrapped__(task="read it"), timeout=30)
        assert "read" in _registry

    @pytest.mark.asyncio
    async def test_reports_its_own_turn_count(self, _registry):
        calls = [{"id": "t0", "name": "read", "args": {}}]
        delegate = make_delegate_tool(
            call_model=answering_model(script=[(calls, False)]), allow_tools=["read"]
        )
        out = await asyncio.wait_for(delegate.__wrapped__(task="go"), timeout=30)
        assert out["turns"] >= 2

    @pytest.mark.asyncio
    async def test_flags_a_truncated_child(self, _registry):
        """The parent needs to know the answer is partial, or it will
        treat a budget-capped guess as a finished result."""
        calls = [{"id": "t0", "name": "read", "args": {}}]
        delegate = make_delegate_tool(
            call_model=answering_model(script=[(calls, False)] * 20),
            allow_tools=["read"],
            max_turns=2,
        )
        out = await asyncio.wait_for(delegate.__wrapped__(task="go"), timeout=30)
        assert out["truncated"] is True

    @pytest.mark.asyncio
    async def test_empty_toolset_is_reported_not_spawned(self, _registry):
        delegate = make_delegate_tool(call_model=answering_model(), allow_tools=[])
        out = await asyncio.wait_for(delegate.__wrapped__(task="go"), timeout=30)
        assert out["error"] == NO_TOOLS_MESSAGE

    @pytest.mark.asyncio
    async def test_child_budget_is_separate_from_the_parent(self, _registry):
        """A child that loops must not consume the parent's budget."""
        calls = [{"id": "t0", "name": "read", "args": {}}]
        delegate = make_delegate_tool(
            call_model=answering_model(script=[(calls, False)] * 20),
            allow_tools=["read"],
            max_turns=3,
        )
        out = await asyncio.wait_for(delegate.__wrapped__(task="go"), timeout=30)
        assert out["turns"] == 3

    @pytest.mark.asyncio
    async def test_silent_child_is_reported_as_an_error(self, _registry):
        """An empty answer means an op raised — operonx records the error
        into state rather than propagating — so a confident blank would
        be the worst possible return."""

        @op
        def mute_model(messages: list = None) -> dict:
            return {"assistant_message": [], "tool_calls": [], "done": True}

        delegate = make_delegate_tool(call_model=mute_model, allow_tools=["read"])
        out = await asyncio.wait_for(delegate.__wrapped__(task="go"), timeout=30)
        assert "no answer" in out["error"]


class TestRegistration:
    def test_registers_under_its_name(self, _registry):
        make_delegate_tool(call_model=answering_model(), name="handoff")
        assert "handoff" in TOOL_REGISTRY

    def test_duplicate_registration_is_rejected(self, _registry):
        make_delegate_tool(call_model=answering_model())
        with pytest.raises(ValueError, match="already registered"):
            make_delegate_tool(call_model=answering_model())

    def test_schema_tells_the_model_the_task_is_standalone(self):
        from operonx.agents.graphs.subagent import DELEGATE_SCHEMA

        assert "no conversation history" in DELEGATE_SCHEMA["properties"]["task"]["description"]
