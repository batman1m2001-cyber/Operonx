"""Prompt assembly and cache invariants.

A prompt-cache miss is invisible at runtime: the response is identical,
just slower and more expensive. So these tests assert on *byte
stability* and on message order, which are the things that decide whether
the cache hits at all.
"""

from __future__ import annotations

import pytest

from operonx.agents.ops.prompt_ops import (
    apply_cache_control,
    assemble_api_messages,
    build_system_prompt,
    prefix_is_stable,
)

pytestmark = pytest.mark.unit

build = build_system_prompt.__wrapped__
assemble = assemble_api_messages.__wrapped__
cache = apply_cache_control.__wrapped__


class TestSystemPrompt:
    def test_is_byte_stable_across_calls(self):
        """Two identical calls must produce identical bytes, or the
        prefix changes every turn and nothing ever caches."""
        a = build(base="be helpful", sections={"x": "1"}, today="2026-08-11")
        b = build(base="be helpful", sections={"x": "1"}, today="2026-08-11")
        assert a["prompt"] == b["prompt"]

    def test_section_order_does_not_depend_on_dict_order(self):
        """Otherwise the cache depends on how the caller happened to
        build the dict."""
        a = build(base="b", sections={"alpha": "1", "beta": "2"}, today="2026-08-11")
        b = build(base="b", sections={"beta": "2", "alpha": "1"}, today="2026-08-11")
        assert a["prompt"] == b["prompt"]

    def test_carries_a_date_not_a_timestamp(self):
        prompt = build(base="b", today="2026-08-11")["prompt"]
        assert "2026-08-11" in prompt
        assert ":" not in prompt.split("Today's date is")[-1]

    def test_empty_sections_are_omitted(self):
        prompt = build(base="b", sections={"empty": "", "blank": "   "})["prompt"]
        assert "<empty>" not in prompt and "<blank>" not in prompt

    def test_empty_base_is_allowed(self):
        assert build(base="")["prompt"].startswith("Today's date")

    def test_defaults_to_today(self):
        assert "Today's date is" in build(base="b")["prompt"]


class TestAssembly:
    def test_order_is_system_conversation_memory(self):
        """Memory trails the conversation. Leading with it would push the
        whole history out of the cached prefix — the most expensive
        possible position."""
        out = assemble(
            system="sys",
            messages=[{"role": "user", "content": "q"}],
            memory_context="<memory>\n- fact\n</memory>",
        )
        assert [m["role"] for m in out["messages"]] == ["system", "user", "user"]
        assert "memory" in out["messages"][-1]["content"]

    def test_absent_memory_adds_nothing(self):
        out = assemble(system="sys", messages=[{"role": "user", "content": "q"}])
        assert out["count"] == 2

    @pytest.mark.parametrize("empty", ["", "   ", None])
    def test_empty_memory_is_omitted_not_rendered(self, empty):
        """An always-present but sometimes-empty wrapper still changes the
        prefix on the turns it is empty."""
        out = assemble(system="s", messages=[], memory_context=empty)
        assert out["count"] == 1

    def test_notices_come_last(self):
        out = assemble(
            system="s",
            messages=[{"role": "user", "content": "q"}],
            notices=["budget exhausted"],
        )
        assert out["messages"][-1]["content"] == "budget exhausted"

    def test_notices_accept_dicts(self):
        out = assemble(system="s", messages=[], notices=[{"role": "user", "content": "n"}])
        assert out["messages"][-1] == {"role": "user", "content": "n"}

    def test_non_dict_messages_are_dropped(self):
        out = assemble(system="s", messages=["bogus", None, {"role": "user", "content": "q"}])
        assert out["count"] == 2

    def test_growth_preserves_the_prefix(self):
        """The property caching depends on: adding a turn must not change
        any earlier message."""
        first = assemble(system="s", messages=[{"role": "user", "content": "a"}])["messages"]
        second = assemble(
            system="s",
            messages=[{"role": "user", "content": "a"}, {"role": "assistant", "content": "b"}],
        )["messages"]
        assert prefix_is_stable(first, second)["stable"] is True


class TestCacheControl:
    def test_marks_the_last_system_message(self):
        out = cache(messages=[{"role": "system", "content": "s"}, {"role": "user", "content": "q"}])
        assert out["messages"][0]["cache_control"] == {"type": "ephemeral"}
        assert "cache_control" not in out["messages"][1]

    def test_does_not_mark_per_turn_messages(self):
        """Caching something that changes every turn costs a cache write
        and buys nothing."""
        out = cache(
            messages=[
                {"role": "system", "content": "s"},
                {"role": "user", "content": "changes"},
            ],
            breakpoints=4,
        )
        assert out["marked"] == 1

    def test_multiple_breakpoints_within_the_prefix(self):
        out = cache(
            messages=[
                {"role": "system", "content": "a"},
                {"role": "system", "content": "b"},
                {"role": "user", "content": "q"},
            ],
            breakpoints=2,
        )
        assert out["marked"] == 2
        assert all("cache_control" in m for m in out["messages"][:2])

    def test_no_system_message_marks_nothing(self):
        out = cache(messages=[{"role": "user", "content": "q"}])
        assert out["marked"] == 0

    def test_zero_breakpoints_marks_nothing(self):
        out = cache(messages=[{"role": "system", "content": "s"}], breakpoints=0)
        assert out["marked"] == 0

    def test_does_not_mutate_the_input(self):
        """The caller's conversation cell is shared; a marker written into
        it would persist into next turn's history."""
        original = [{"role": "system", "content": "s"}]
        cache(messages=original)
        assert "cache_control" not in original[0]

    def test_custom_marker(self):
        out = cache(messages=[{"role": "system", "content": "s"}], marker={"type": "persistent"})
        assert out["messages"][0]["cache_control"] == {"type": "persistent"}


class TestPrefixStability:
    def test_identical_is_stable(self):
        msgs = [{"role": "system", "content": "s"}]
        assert prefix_is_stable(msgs, msgs)["stable"] is True

    def test_appending_is_stable(self):
        old = [{"role": "system", "content": "s"}]
        new = old + [{"role": "user", "content": "q"}]
        out = prefix_is_stable(old, new)
        assert out["stable"] is True and out["shared"] == 1

    def test_changed_system_prompt_is_reported(self):
        out = prefix_is_stable(
            [{"role": "system", "content": "a"}], [{"role": "system", "content": "b"}]
        )
        assert out["stable"] is False
        assert "content changed at index 0" in out["diverged"]

    def test_role_change_is_named(self):
        out = prefix_is_stable([{"role": "system"}], [{"role": "user"}])
        assert "role changed" in out["diverged"]

    def test_a_timestamp_in_the_prompt_breaks_stability(self):
        """The failure this whole module is shaped around."""
        turn1 = assemble(system=build(base="b", today="2026-08-11")["prompt"], messages=[])
        turn2 = assemble(system=build(base="b", today="2026-08-12")["prompt"], messages=[])
        assert prefix_is_stable(turn1["messages"], turn2["messages"])["stable"] is False

    def test_empty_previous_is_trivially_stable(self):
        assert prefix_is_stable(None, [{"role": "system"}])["stable"] is True
