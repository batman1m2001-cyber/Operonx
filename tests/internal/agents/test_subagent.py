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


def recording_hub(seen_tools):
    """A ResourceHub whose model records the ``tools=`` it is sent and
    answers at once, so a real ``make_llm_caller`` runs with no network."""
    from unittest.mock import Mock

    from openai.types.chat.chat_completion import ChatCompletion

    async def generate(messages, tools=None, **kwargs):
        seen_tools.append(sorted(t["function"]["name"] for t in tools or []))
        return ChatCompletion.model_validate(
            {
                "id": "x",
                "created": 0,
                "model": "m",
                "object": "chat.completion",
                "choices": [
                    {
                        "index": 0,
                        "finish_reason": "stop",
                        "message": {"role": "assistant", "content": "child done"},
                    }
                ],
            }
        )

    llm = Mock()
    llm.generate = generate
    hub = Mock()
    hub.get.return_value = llm
    return hub


class TestChildSeesOnlyWhatItMayCall:
    """``_child_policy`` restricted *dispatch*, but the child reused the
    parent's ``call_model``, whose ``tools=`` list ``make_llm_caller``
    baked in. The child was shown tools it could never call, called them,
    and spent its own turn budget on refusals."""

    @pytest.fixture(autouse=True)
    def _shell(self, _registry):
        @tool(name="shell", description="Run a command.", schema=EMPTY)
        async def shell() -> dict:
            return {}

    @pytest.mark.asyncio
    async def test_the_childs_model_is_shown_only_its_allowed_tools(self):
        from unittest.mock import patch

        from operonx.agents.ops.model_ops import make_llm_caller
        from operonx.agents.tool import get_tool_definitions

        seen: list = []
        parent_caller = make_llm_caller("mock", tools=get_tool_definitions())
        delegate = make_delegate_tool(call_model=parent_caller, allow_tools=["read", "wipe"])
        with patch("operonx.providers.ops._utils.ResourceHub") as hub_cls:
            hub_cls.instance.return_value = recording_hub(seen)
            out = await asyncio.wait_for(delegate.__wrapped__(task="go"), timeout=30)

        assert out.get("answer") == "child done", out
        # `shell` is outside allow_tools; `wipe` would ask a human, and a
        # child has no one to ask — see TestApprovalInAChild.
        assert seen == [["read"]]

    def test_the_parent_caller_is_left_as_it_was(self):
        from operonx.agents.ops.model_ops import make_llm_caller
        from operonx.agents.tool import get_tool_definitions

        caller = make_llm_caller("mock", tools=get_tool_definitions())
        narrowed = caller.with_tools(["read"])
        assert [t["function"]["name"] for t in narrowed.tools] == ["read"]
        assert sorted(t["function"]["name"] for t in caller.tools) == ["read", "shell", "wipe"]

    def test_a_tool_registered_after_the_parent_caller_is_still_shown(self):
        """The child's toolset is resolved per call; a definitions list
        frozen when the parent caller was built must not undo that."""
        from operonx.agents.ops.model_ops import make_llm_caller
        from operonx.agents.tool import get_tool_definitions

        caller = make_llm_caller("mock", tools=get_tool_definitions(["read"]))

        @tool(name="grep", description="Search.", schema=EMPTY, readonly=True)
        async def grep() -> dict:
            return {}

        assert [t["function"]["name"] for t in caller.with_tools(["read", "grep"]).tools] == [
            "read",
            "grep",
        ]

    def test_describe_reports_what_the_child_is_shown(self):
        out = describe_delegation(allow_tools=["read", "wipe"])
        assert out["shown"] == ["read"]


class TestApprovalInAChild:
    """An inherited ``ask`` verdict could never be answered in a child:
    nothing binds an interrupt bus to the child's run. The call waited out
    ``approval_timeout`` (300s by default — as long as the delegation's own
    timeout) and the parent was told the sub-agent timed out."""

    @staticmethod
    def _asks_for_wipe_then_answers(seen):
        state = {"i": 0}

        @op
        def call_model(messages: list = None) -> dict:
            i = state["i"]
            state["i"] += 1
            seen.append(list(messages or []))
            calls = [{"id": "w0", "name": "wipe", "args": {}}] if i == 0 else []
            return {
                "assistant_message": [
                    {"id": f"c{i}", "role": "assistant", "content": "tried" if i else ""}
                ],
                "tool_calls": calls,
                "done": not calls,
            }

        return call_model

    @pytest.mark.asyncio
    async def test_a_gated_call_is_refused_at_once(self, _registry):
        import time

        seen: list = []
        delegate = make_delegate_tool(
            call_model=self._asks_for_wipe_then_answers(seen),
            allow_tools=["read", "wipe"],
            timeout=5.0,
        )
        started = time.monotonic()
        out = await asyncio.wait_for(delegate.__wrapped__(task="clean up"), timeout=30)
        elapsed = time.monotonic() - started

        assert "error" not in out, out
        assert elapsed < 2.0, f"waited {elapsed:.1f}s for an approval nobody can give"
        assert _registry == [], "the gated tool must not run"

    @pytest.mark.asyncio
    async def test_the_child_is_told_why(self, _registry):
        seen: list = []
        delegate = make_delegate_tool(
            call_model=self._asks_for_wipe_then_answers(seen),
            allow_tools=["read", "wipe"],
            timeout=5.0,
        )
        await asyncio.wait_for(delegate.__wrapped__(task="clean up"), timeout=30)
        refusal = next(m for m in seen[-1] if m.get("role") == "tool")
        assert "approval" in refusal["content"].lower()
        assert "sub-agent" in refusal["content"].lower()

    @pytest.mark.asyncio
    async def test_a_child_left_with_only_gated_tools_is_not_spawned(self, _registry):
        delegate = make_delegate_tool(call_model=answering_model(), allow_tools=["wipe"])
        out = await asyncio.wait_for(delegate.__wrapped__(task="go"), timeout=30)
        assert out["error"] == NO_TOOLS_MESSAGE


class TestTruncatedChild:
    @pytest.mark.asyncio
    async def test_a_child_cut_at_length_is_flagged(self, _registry):
        """The parent must not treat half an answer as a finished one."""

        @op
        def cut_model(messages: list = None) -> dict:
            return {
                "assistant_message": [{"id": "c", "role": "assistant", "content": "Half of"}],
                "tool_calls": [],
                "done": True,
                "finish_reason": "length",
            }

        delegate = make_delegate_tool(call_model=cut_model, allow_tools=["read"])
        out = await asyncio.wait_for(delegate.__wrapped__(task="go"), timeout=30)
        assert out["answer"] == "Half of"
        assert out["truncated"] is True
