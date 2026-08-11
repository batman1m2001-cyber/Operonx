"""The context lifecycle must actually run inside the loop.

Compaction, memory, skills, prompt assembly and cache control were all
built, tested and exported — and wired into nothing. `build_react_agent`
never called them, so a deployment grew context until the provider
rejected it with a fully-tested compactor sitting unused, and got no
prompt-cache benefit at all.

Unit tests on each piece could not catch that: they all passed. These
assert on what the **model actually receives**, which is the only place
the wiring is observable.
"""

from __future__ import annotations

import pytest

from operonx.agents.graphs.react import build_react_agent
from operonx.agents.memory import MemoryEntry, MemoryProvider
from operonx.agents.ops.compact_ops import SUMMARY_MARKER
from operonx.agents.skills import Skill
from operonx.agents.tool import clear_registry
from operonx.core import Operon, op

pytestmark = pytest.mark.unit


class Notes(MemoryProvider):
    def __init__(self, text="deploys go through staging"):
        self.text = text
        self.queries = []

    async def _prefetch(self, query, limit):
        self.queries.append(query)
        return [MemoryEntry(self.text, "notes", 1.0)]

    async def _write(self, text, source):
        pass


@pytest.fixture(autouse=True)
def _clean():
    clear_registry()
    yield
    clear_registry()


def capturing_model():
    """Records exactly what the model was handed each turn."""
    seen: list[list] = []

    @op
    def call_model(messages: list = None) -> dict:
        seen.append(list(messages or []))
        return {
            "assistant_message": [{"id": f"a{len(seen)}", "role": "assistant", "content": "ok"}],
            "tool_calls": [],
            "done": True,
        }

    return call_model, seen


async def run(user="how do deploys work", **kwargs):
    model, seen = capturing_model()
    agent = build_react_agent(call_model=model, max_turns=3, **kwargs)(messages=None)
    await Operon(agent).run(inputs={"messages": [{"role": "user", "content": user}]})
    assert seen, "the model was never called"
    return seen[0]


class TestSystemPrompt:
    @pytest.mark.asyncio
    async def test_system_prompt_reaches_the_model(self):
        prompt = await run(system="be terse")
        assert prompt[0]["role"] == "system"
        assert prompt[0]["content"] == "be terse"

    @pytest.mark.asyncio
    async def test_no_system_message_when_none_given(self):
        prompt = await run()
        assert all(m.get("role") != "system" for m in prompt)


class TestCacheControl:
    @pytest.mark.asyncio
    async def test_the_prefix_is_marked(self):
        prompt = await run(system="be terse")
        assert prompt[0].get("cache_control") == {"type": "ephemeral"}

    @pytest.mark.asyncio
    async def test_per_turn_messages_are_not_marked(self):
        """Caching something that changes per turn costs a write and buys
        nothing."""
        prompt = await run(system="be terse")
        assert all("cache_control" not in m for m in prompt[1:])


class TestMemory:
    @pytest.mark.asyncio
    async def test_retrieved_context_reaches_the_model(self):
        notes = Notes()
        prompt = await run(memory_providers=[notes])
        assert any("deploys go through staging" in str(m.get("content")) for m in prompt)

    @pytest.mark.asyncio
    async def test_memory_is_queried_with_the_user_turn(self):
        """Matching on the whole transcript would retrieve the same thing
        every turn regardless of the question."""
        notes = Notes()
        await run(user="how do deploys work", memory_providers=[notes])
        assert notes.queries == ["how do deploys work"]

    @pytest.mark.asyncio
    async def test_memory_goes_after_the_conversation(self):
        """Leading with it would push the history out of the cached
        prefix — the most expensive possible position."""
        prompt = await run(system="s", memory_providers=[Notes()])
        roles = [m["role"] for m in prompt]
        user_first = roles.index("user")
        memory_at = next(
            i for i, m in enumerate(prompt) if "deploys go through staging" in str(m.get("content"))
        )
        assert memory_at > user_first

    @pytest.mark.asyncio
    async def test_a_broken_provider_does_not_stop_the_turn(self):
        class Broken(MemoryProvider):
            async def _prefetch(self, query, limit):
                raise RuntimeError("backend down")

            async def _write(self, text, source):
                pass

        prompt = await run(memory_providers=[Broken()])
        assert prompt, "memory is enrichment; a failure must not end the turn"

    @pytest.mark.asyncio
    async def test_no_providers_adds_nothing(self):
        with_mem = await run(system="s", memory_providers=[Notes()])
        without = await run(system="s")
        assert len(without) < len(with_mem)


class TestSkills:
    @pytest.mark.asyncio
    async def test_a_matching_skill_is_injected(self):
        skill = Skill(
            name="deploy",
            description="How to deploy.",
            body="Run make deploy.",
            triggers=["deploy", "deploys"],
        )
        prompt = await run(user="how do deploys work", skills=[skill])
        assert any("Run make deploy." in str(m.get("content")) for m in prompt)

    @pytest.mark.asyncio
    async def test_an_unmatched_skill_is_not_injected(self):
        skill = Skill(name="db", description="Database.", body="X", triggers=["postgres"])
        prompt = await run(user="how do deploys work", skills=[skill])
        assert all("<skills>" not in str(m.get("content")) for m in prompt)

    @pytest.mark.asyncio
    async def test_skills_are_a_user_message(self):
        """They change per query; the system prompt is the cached prefix."""
        skill = Skill(name="deploy", description="D.", body="B", triggers=["deploy", "deploys"])
        prompt = await run(user="deploys?", system="s", skills=[skill])
        block = next(m for m in prompt if "<skills>" in str(m.get("content")))
        assert block["role"] == "user"


class TestCompaction:
    @pytest.mark.asyncio
    async def test_an_oversized_history_is_compacted_before_sending(self):
        """The whole point: without this the prompt grows until the
        provider rejects it."""
        model, seen = capturing_model()
        agent = build_react_agent(call_model=model, max_turns=2, token_budget=200, keep_recent=1)(
            messages=None
        )
        history = [{"role": "user", "content": "q" * 400}] + [
            {"role": "assistant", "content": "a" * 4000},
            {"role": "user", "content": "and now?"},
        ]
        await Operon(agent).run(inputs={"messages": history})
        sent = seen[0]
        assert any(SUMMARY_MARKER in str(m.get("content")) for m in sent), (
            "the prompt must be compacted, not sent whole"
        )

    @pytest.mark.asyncio
    async def test_a_small_history_is_sent_untouched(self):
        prompt = await run(user="hi", token_budget=100_000)
        assert all(SUMMARY_MARKER not in str(m.get("content")) for m in prompt)

    @pytest.mark.asyncio
    async def test_the_stored_conversation_is_not_destroyed_by_compaction(self):
        """Compaction shapes the prompt, not the history — so nothing is
        lost irrecoverably and `agent_result` still returns everything."""
        from operonx.agents.graphs.react import agent_result

        model, _ = capturing_model()
        agent = build_react_agent(call_model=model, max_turns=2, token_budget=200, keep_recent=1)(
            messages=None
        )
        history = [
            {"role": "user", "content": "the original question"},
            {"role": "assistant", "content": "a" * 4000},
            {"role": "user", "content": "and now?"},
        ]
        result = await Operon(agent).run(inputs={"messages": history})
        answer = agent_result(result, agent)
        assert any("the original question" in str(m.get("content")) for m in answer["messages"]), (
            "the stored history must survive prompt-time compaction"
        )
