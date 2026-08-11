"""Context compaction.

The dominant risk is not losing information — it is breaking the
assistant ``tool_call`` / tool-message pairing. A provider rejects that
outright, and the rejection arrives on the *next* request reported as a
malformed request rather than as a compaction bug. Most of these tests
exist to pin the pairing.
"""

from __future__ import annotations

import pytest

from operonx.agents.ops.compact_ops import (
    SUMMARY_MARKER,
    apply_compaction,
    compaction_summary_prompt,
    count_tokens,
    estimate_tokens,
    plan_compaction,
    unmatched_tool_calls,
)

pytestmark = pytest.mark.unit

plan = plan_compaction.__wrapped__
apply_ = apply_compaction.__wrapped__
count = count_tokens.__wrapped__


def exchange(i, n_tools=1, size=200):
    """One assistant turn requesting tools, plus its answers."""
    calls = [{"id": f"c{i}_{k}", "name": "tool", "args": {}} for k in range(n_tools)]
    out = [{"role": "assistant", "content": "x" * size, "tool_calls": calls}]
    out += [{"role": "tool", "tool_call_id": c["id"], "content": "y" * size} for c in calls]
    return out


def conversation(n_exchanges, size=200):
    msgs = [{"role": "system", "content": "be helpful"}]
    msgs.append({"role": "user", "content": "start"})
    for i in range(n_exchanges):
        msgs.extend(exchange(i, size=size))
    return msgs


class TestEstimate:
    def test_empty(self):
        assert estimate_tokens([]) == 0
        assert estimate_tokens(None) == 0

    def test_grows_with_content(self):
        small = estimate_tokens([{"role": "user", "content": "hi"}])
        large = estimate_tokens([{"role": "user", "content": "hi" * 500}])
        assert large > small

    def test_counts_tool_calls(self):
        """A turn requesting eight tools is not small, and a naive count
        that only reads `content` would call it near-empty."""
        plain = [{"role": "assistant", "content": ""}]
        with_calls = [
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [{"id": f"c{i}", "name": "tool", "args": {"x": i}} for i in range(8)],
            }
        ]
        assert estimate_tokens(with_calls) > estimate_tokens(plain)

    def test_survives_malformed_entries(self):
        assert estimate_tokens(["not a dict", None, {"role": "user"}]) >= 0

    def test_count_op_reports_over_budget(self):
        out = count(messages=[{"role": "user", "content": "x" * 10_000}], budget=100)
        assert out["over_budget"] is True
        assert out["ratio"] > 1


class TestTrigger:
    def test_not_needed_under_the_ratio(self):
        assert plan(messages=conversation(1), budget=100_000)["needed"] is False

    def test_triggers_before_the_budget_is_exceeded(self):
        """Waiting for the budget to be *exceeded* means the turn that
        discovers it has already failed."""
        msgs = conversation(6, size=400)
        tokens = estimate_tokens(msgs)
        out = plan(messages=msgs, budget=int(tokens / 0.8), keep_recent=1)
        assert out["needed"] is True, "must trigger while still under budget"

    def test_zero_budget_disables_compaction(self):
        assert plan(messages=conversation(20), budget=0)["needed"] is False

    def test_nothing_older_than_the_keep_window(self):
        msgs = conversation(2, size=4000)
        assert plan(messages=msgs, budget=10, keep_recent=50)["needed"] is False


class TestPairingIsPreserved:
    @pytest.mark.parametrize("keep_recent", [1, 2, 3, 5])
    def test_plan_never_splits_an_exchange(self, keep_recent):
        msgs = conversation(8, size=400)
        out = plan(messages=msgs, budget=100, keep_recent=keep_recent)
        assert unmatched_tool_calls(out["keep"]) == {
            "calls_without_results": [],
            "results_without_calls": [],
        }
        assert unmatched_tool_calls(out["summarize"]) == {
            "calls_without_results": [],
            "results_without_calls": [],
        }

    def test_result_is_a_valid_conversation(self):
        msgs = conversation(8, size=400)
        out = plan(messages=msgs, budget=100, keep_recent=2)
        applied = apply_(**{k: out[k] for k in ("pinned", "summarize", "keep")}, summary="notes")
        assert unmatched_tool_calls(applied["messages"]) == {
            "calls_without_results": [],
            "results_without_calls": [],
        }

    def test_multi_tool_exchange_moves_together(self):
        msgs = [{"role": "user", "content": "go"}] + exchange(0, n_tools=4)
        out = plan(messages=msgs, budget=1, keep_recent=1)
        kept_or_dropped = out["keep"] + out["summarize"]
        assert unmatched_tool_calls(kept_or_dropped)["calls_without_results"] == []

    def test_unanswered_tail_is_not_orphaned(self):
        """A conversation cut mid-exchange must not have its dangling
        assistant turn separated from the group."""
        msgs = conversation(4, size=400)
        msgs.append({"role": "assistant", "content": "x", "tool_calls": [{"id": "z", "name": "t"}]})
        out = plan(messages=msgs, budget=1, keep_recent=1)
        assert any(
            any(c.get("id") == "z" for c in m.get("tool_calls") or []) for m in out["keep"]
        ), "the dangling exchange is the most recent one and must be kept"


class TestPinning:
    def test_system_messages_always_survive(self):
        """Losing the system prompt changes the agent's behaviour rather
        than its recall — the hardest failure to attribute later."""
        msgs = conversation(10, size=400)
        out = plan(messages=msgs, budget=1, keep_recent=1)
        assert out["pinned"] == [{"role": "system", "content": "be helpful"}]
        assert all(m.get("role") != "system" for m in out["summarize"])

    def test_pinned_come_first_after_apply(self):
        msgs = conversation(10, size=400)
        out = plan(messages=msgs, budget=1, keep_recent=1)
        applied = apply_(**{k: out[k] for k in ("pinned", "summarize", "keep")}, summary="s")
        assert applied["messages"][0]["role"] == "system"


class TestApply:
    def test_no_summarize_span_is_a_passthrough(self):
        out = apply_(pinned=[{"role": "system"}], summarize=[], keep=[{"role": "user"}])
        assert out["compacted"] is False
        assert out["messages"] == [{"role": "system"}, {"role": "user"}]

    def test_summary_is_marked(self):
        out = apply_(
            summarize=[{"role": "user", "content": "old"}], keep=[], summary="they said hi"
        )
        assert SUMMARY_MARKER in out["messages"][0]["content"]
        assert "they said hi" in out["messages"][0]["content"]

    def test_missing_summary_says_so_rather_than_dropping_silently(self):
        """Silently losing history produces an agent that contradicts
        itself with no way to see why."""
        out = apply_(summarize=[{"role": "user", "content": "old"}] * 3, keep=[], summary="")
        body = out["messages"][0]["content"]
        assert "3 earlier messages were removed" in body
        assert "restate" in body

    def test_reports_how_much_went(self):
        out = apply_(summarize=[{"role": "user"}] * 7, keep=[], summary="s")
        assert out["dropped"] == 7
        assert out["compacted"] is True

    def test_recent_messages_keep_their_order(self):
        keep = [{"role": "user", "content": "a"}, {"role": "assistant", "content": "b"}]
        out = apply_(summarize=[{"role": "user"}], keep=keep, summary="s")
        assert out["messages"][-2:] == keep


class TestSummaryPrompt:
    def test_includes_tool_names(self):
        text = compaction_summary_prompt(exchange(0, n_tools=2))
        assert "called:" in text

    def test_asks_for_notes_not_prose(self):
        text = compaction_summary_prompt([{"role": "user", "content": "hi"}])
        assert "notes, not prose" in text


class TestUnmatchedHelper:
    def test_detects_a_call_without_a_result(self):
        msgs = [{"role": "assistant", "tool_calls": [{"id": "a", "name": "t"}]}]
        assert unmatched_tool_calls(msgs)["calls_without_results"] == ["a"]

    def test_detects_a_result_without_a_call(self):
        msgs = [{"role": "tool", "tool_call_id": "b", "content": "x"}]
        assert unmatched_tool_calls(msgs)["results_without_calls"] == ["b"]

    def test_clean_conversation_reports_nothing(self):
        assert unmatched_tool_calls(conversation(3)) == {
            "calls_without_results": [],
            "results_without_calls": [],
        }
