"""Compaction: triggers on the provider's real usage, persists its summary,
keeps the prefix stable between compactions, and never splits an exchange.

The planner tests are ported from
``operonx/tests/internal/agents/test_compaction.py``: the dominant risk is
breaking the ``tool_call`` / tool-message pairing, which a provider rejects
on the next request, reported as a malformed request.
"""

from __future__ import annotations

import pytest

from operonx_agents import (
    Agent,
    Compacted,
    ContextPolicy,
    InMemorySession,
    Model,
    Runner,
)
from operonx_agents.context.compaction import (
    CLEARED,
    SUMMARY_MARKER,
    clear_tool_results,
    plan,
    summary_prompt,
    unmatched_tool_calls,
    view,
)
from operonx_agents.context.prompt import prefix_is_stable
from tests.agents import RAN, TOOLS, asks, roles, says
from tests.fakes import ScriptedLLM, StatusError

CLEAN = {"calls_without_results": [], "results_without_calls": []}


@pytest.fixture(autouse=True)
def _ran():
    RAN.clear()
    yield


def build(hub, script, summarizer_script=("notes so far",), **policy):
    llm = ScriptedLLM(*script)
    summarizer = ScriptedLLM(*[says(s) if isinstance(s, str) else s for s in summarizer_script])
    hub(m=llm, s=summarizer)
    policy.setdefault("window", 1000)
    policy.setdefault("keep_recent", 2)
    agent = Agent(
        name="a",
        model=Model("m"),
        tools=TOOLS,
        instructions="be terse",
        context=ContextPolicy(summarizer=Model("s"), **policy),
    )
    return agent, llm, summarizer


class TestTrigger:
    async def test_real_usage_over_the_threshold_compacts_the_next_turn(self, hub):
        script = [
            asks(("echo", {"a": 0}), turn=0, prompt_tokens=100),
            asks(("echo", {"a": 1}), turn=1, prompt_tokens=200),
            asks(("echo", {"a": 2}), turn=2, prompt_tokens=800),  # ≥ 0.75 × 1000
            says("done", prompt_tokens=300),
        ]
        agent, llm, summarizer = build(hub, script)
        events = [e async for e in Runner.stream(agent, "go")]
        assert summarizer.calls == 1
        (compacted,) = [e for e in events if isinstance(e, Compacted)]
        assert compacted.dropped > 0 and compacted.summary_tokens == 3
        last = llm.requests[-1]["messages"]
        assert last[0]["content"] == "be terse"
        assert last[1]["content"] == f"{SUMMARY_MARKER}\nnotes so far"
        assert unmatched_tool_calls(last) == CLEAN
        assert all(m.get("content") != "go" for m in last), "the oldest exchange was summarised"
        assert [m.get("tool_call_id") for m in last if m["role"] == "tool"] == ["t1_0", "t2_0"]

    async def test_it_is_the_counted_tokens_not_the_text_length(self, hub):
        """100k characters the provider counted as 50 tokens: no compaction.
        The 3.5 chars/token estimate would have said ~28k."""
        huge = "x" * 100_000
        script = [
            asks(("note", {"text": huge}), prompt_tokens=50),
            asks(("note", {"text": huge}), turn=1, prompt_tokens=50),
            says("done", prompt_tokens=50),
        ]
        agent, _, summarizer = build(hub, script)
        res = await Runner.run(agent, "go")
        assert res.status == "completed" and summarizer.calls == 0

    async def test_below_the_threshold_nothing_happens(self, hub):
        script = [asks(("echo", {"a": 0}), prompt_tokens=749), says("done")]
        agent, _, summarizer = build(hub, script)
        await Runner.run(agent, "go")
        assert summarizer.calls == 0

    def test_policy_is_validated(self):
        with pytest.raises(ValueError, match="window"):
            ContextPolicy(window=0)
        with pytest.raises(ValueError, match="compact_at"):
            ContextPolicy(window=10, compact_at=1.5)
        with pytest.raises(ValueError, match="keep_recent"):
            ContextPolicy(window=10, keep_recent=0)


class TestPersistence:
    async def test_the_summary_persists_and_is_not_recomputed(self, hub):
        script = [
            asks(("echo", {"a": 0}), turn=0),
            asks(("echo", {"a": 1}), turn=1),
            asks(("echo", {"a": 2}), turn=2, prompt_tokens=900),
            says("first answer"),
            says("second answer"),
        ]
        agent, llm, summarizer = build(hub, script)
        session = InMemorySession()
        await Runner.run(agent, "first", session=session)
        items = await session.get_items()
        (summary,) = [i for i in items if i["role"] == "summary"]
        assert summary["content"] == "notes so far"

        await Runner.run(agent, "second", session=session)
        assert summarizer.calls == 1, "the next run read the summary back"
        before, after = llm.requests[-2]["messages"], llm.requests[-1]["messages"]
        check = prefix_is_stable(before, after)
        assert check["stable"], check["diverged"]
        assert after[1]["content"].startswith(SUMMARY_MARKER)

    async def test_the_view_of_the_session_is_what_the_run_ended_with(self, hub):
        script = [
            asks(("echo", {"a": 0}), turn=0),
            asks(("echo", {"a": 1}), turn=1, prompt_tokens=900),
            says("done"),
        ]
        agent, _, _ = build(hub, script, keep_recent=1)
        session = InMemorySession()
        res = await Runner.run(agent, "go", session=session)
        assert view(await session.get_items()) == res.messages
        assert unmatched_tool_calls(res.messages) == CLEAN

    async def test_a_second_compaction_resummarises_the_first(self, hub):
        script = [
            asks(("echo", {"a": i}), turn=i, prompt_tokens=900 if i in (1, 3) else 10)
            for i in range(5)
        ] + [says("done")]
        agent, llm, summarizer = build(hub, script, ("first notes", "second notes"), keep_recent=1)
        session = InMemorySession()
        res = await Runner.run(agent, "go", session=session)
        assert summarizer.calls == 2
        assert "first notes" in summarizer.requests[1]["messages"][0]["content"]
        assert sum(m["content"].startswith(SUMMARY_MARKER) for m in res.messages
                   if m["role"] == "user") == 1  # fmt: skip
        assert view(await session.get_items()) == res.messages

    async def test_tool_results_are_cleared_before_summarising(self, hub):
        script = [
            asks(("echo", {"a": 0}), ("echo", {"a": 1}), ("echo", {"a": 2}), prompt_tokens=900),
            says("done"),
        ]
        agent, llm, _ = build(hub, script, keep_recent=2, clear_tool_results_after=1)
        await Runner.run(agent, "go")
        sent = [m for m in llm.requests[-1]["messages"] if m["role"] == "tool"]
        assert [m["content"] for m in sent][:2] == [CLEARED, CLEARED]
        assert sent[-1]["content"] != CLEARED

    async def test_a_failing_summarizer_fails_open(self, hub):
        script = [asks(("echo", {"a": 0}), prompt_tokens=900), says("done")]
        agent, _, summarizer = build(hub, script, (StatusError(400),))
        res = await Runner.run(agent, "go")
        assert res.status == "completed" and summarizer.calls == 1
        assert roles(res.messages) == ["user", "assistant", "tool", "assistant"]

    async def test_without_a_summarizer_the_span_is_dropped_with_a_note(self, hub):
        hub(m=ScriptedLLM(asks(("echo", {"a": 0}), turn=0), asks(("echo", {"a": 1}), turn=1,
            prompt_tokens=900), says("done")))  # fmt: skip
        agent = Agent(
            name="a",
            model=Model("m"),
            tools=TOOLS,
            context=ContextPolicy(window=1000, keep_recent=1),
        )
        res = await Runner.run(agent, "go")
        assert "were removed" in res.messages[0]["content"]


def exchange(i, n_tools=1, size=200):
    calls = [{"id": f"c{i}_{k}", "name": "tool", "args": {}} for k in range(n_tools)]
    out = [{"role": "assistant", "content": "x" * size, "tool_calls": calls}]
    out += [{"role": "tool", "tool_call_id": c["id"], "content": "y" * size} for c in calls]
    return out


def conversation(n):
    msgs = [{"role": "user", "content": "start"}]
    for i in range(n):
        msgs.extend(exchange(i))
    return msgs


def _asst(name, *call_ids):
    message = {"role": "assistant", "content": name}
    if call_ids:
        message["tool_calls"] = [{"id": c, "name": "tool", "args": {}} for c in call_ids]
    return message


def _result(call_id):
    return {"role": "tool", "tool_call_id": call_id, "content": "r"}


def _user(text):
    return {"role": "user", "content": text}


class TestPairingIsPreserved:
    @pytest.mark.parametrize("keep_recent", [1, 2, 3, 5])
    def test_plan_never_splits_an_exchange(self, keep_recent):
        older, kept = plan(conversation(8), keep_recent)
        assert unmatched_tool_calls(kept) == CLEAN
        assert unmatched_tool_calls(older) == CLEAN

    def test_multi_tool_exchange_moves_together(self):
        older, kept = plan([_user("go")] + exchange(0, n_tools=4), 1)
        assert unmatched_tool_calls(kept)["calls_without_results"] == []
        assert unmatched_tool_calls(older + kept) == CLEAN

    def test_the_most_recent_exchange_is_never_given_up(self):
        msgs = conversation(4) + [_asst("x", "z")]
        older, kept = plan(msgs, 1)
        assert kept == [msgs[-1]], "the dangling exchange is the latest and is kept"

    def test_an_oversized_window_still_frees_something(self):
        msgs = conversation(2)
        older, kept = plan(msgs, 10)
        assert older and kept and older + kept == msgs

    def test_a_late_result_stays_with_its_call(self):
        msgs = [
            _user("a"),
            _asst("b", "a1", "a2"),
            _result("a1"),
            _user("c"),
            _result("a2"),  # answered late
            _user("d"),
            _asst("e", "b1"),
            _result("b1"),
            _asst("f"),
        ]
        older, kept = plan(msgs, 4)
        assert older and unmatched_tool_calls(kept) == CLEAN
        assert unmatched_tool_calls(older)["results_without_calls"] == []

    def test_an_earlier_unanswered_call_goes_to_the_summary(self):
        msgs = [
            _user("a"),
            _asst("b", "never"),
            _user("c"),
            _asst("d", "b1"),
            _result("b1"),
            _asst("e"),
            _user("f"),
            _asst("g"),
        ]
        older, kept = plan(msgs, 10)
        assert _asst("b", "never") in older and unmatched_tool_calls(kept) == CLEAN

    def test_a_result_for_a_call_that_never_existed_is_not_kept(self):
        msgs = [_user("a"), _result("ghost"), _user("b"), _asst("c"), _user("d"), _asst("e")]
        _, kept = plan(msgs, 10)
        assert unmatched_tool_calls(kept) == CLEAN

    def test_order_is_kept(self):
        msgs = conversation(3)
        older, kept = plan(msgs, 2)
        assert older + kept == msgs


class TestHelpers:
    def test_summary_prompt_names_the_tools_and_asks_for_notes(self):
        text = summary_prompt(exchange(0))
        assert "[called: tool]" in text and "notes" in text

    def test_clearing_keeps_the_last_n(self):
        msgs = [_result("a"), _asst("x"), _result("b"), _result("c")]
        out = clear_tool_results(msgs, 1)
        assert [m["content"] for m in out] == [CLEARED, "x", CLEARED, "r"]
        assert clear_tool_results(msgs, None) == msgs
        assert msgs[0]["content"] == "r", "not mutated"

    def test_view_without_a_summary_is_the_items(self):
        items = conversation(2)
        assert view(items) == items
