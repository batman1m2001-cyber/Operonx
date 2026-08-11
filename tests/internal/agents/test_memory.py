"""Agent memory — the two failure directions, and the fan-out.

Memory has an asymmetric failure policy on purpose: prefetch fails soft
(an unreadable notes file must not stop the agent answering), writes fail
loud (an agent that appears to learn and does not is discovered weeks
later). Most of these tests pin one direction or the other.
"""

from __future__ import annotations

import asyncio

import pytest

from operonx.agents.memory import (
    LocalMarkdownMemory,
    MemoryEntry,
    MemoryProvider,
    render_memory_block,
)
from operonx.agents.ops.memory_ops import (
    each_provider,
    memory_write,
    merge_memory,
    provider_prefetch,
)

pytestmark = pytest.mark.unit


class Boom(MemoryProvider):
    """A provider that fails every way it can."""

    def __init__(self, mode="raise"):
        self.mode = mode
        self.writes = []

    async def _prefetch(self, query, limit):
        if self.mode == "raise":
            raise RuntimeError("backend down")
        if self.mode == "hang":
            await asyncio.sleep(10)
        return [MemoryEntry("fine", "boom")]

    async def _write(self, text, source):
        if self.mode == "raise":
            raise RuntimeError("disk full")
        self.writes.append((text, source))


@pytest.fixture
def notes(tmp_path):
    path = tmp_path / "MEMORY.md"
    path.write_text(
        "# project memory\n\n"
        "- deploys go through the staging branch first\n"
        "- the callbot uses websockets on port 9924\n"
        "- prose that is not a bullet is ignored\n"
        "Some heading text\n",
        encoding="utf-8",
    )
    return LocalMarkdownMemory(path)


class TestLocalMarkdownMemory:
    @pytest.mark.asyncio
    async def test_retrieves_by_keyword_overlap(self, notes):
        entries = await notes.prefetch("how do deploys work")
        assert entries
        assert "staging branch" in entries[0].text

    @pytest.mark.asyncio
    async def test_unrelated_query_returns_nothing(self, notes):
        assert await notes.prefetch("quantum chromodynamics") == []

    @pytest.mark.asyncio
    async def test_only_bullets_are_retrievable(self, notes):
        entries = await notes.prefetch("heading text prose")
        assert all("Some heading" not in e.text for e in entries)

    @pytest.mark.asyncio
    async def test_respects_the_limit(self, notes):
        assert len(await notes.prefetch("the", limit=1)) <= 1

    @pytest.mark.asyncio
    async def test_missing_file_reads_empty(self, tmp_path):
        """A fresh project must work with no setup."""
        provider = LocalMarkdownMemory(tmp_path / "nope.md")
        assert await provider.prefetch("anything") == []

    @pytest.mark.asyncio
    async def test_single_characters_do_not_match_everything(self, notes):
        assert await notes.prefetch("a I") == []

    @pytest.mark.asyncio
    async def test_entries_carry_their_source(self, notes):
        (entry, *_) = await notes.prefetch("deploys")
        assert entry.source == str(notes.path)


class TestWrites:
    @pytest.mark.asyncio
    async def test_write_then_read_back(self, tmp_path):
        provider = LocalMarkdownMemory(tmp_path / "m.md")
        await provider.write("redis runs on port 6379")
        entries = await provider.prefetch("what port does redis use")
        assert entries and "6379" in entries[0].text

    @pytest.mark.asyncio
    async def test_creates_the_file_with_a_heading(self, tmp_path):
        provider = LocalMarkdownMemory(tmp_path / "sub" / "m.md", label="notes")
        await provider.write("a fact")
        assert provider.path.read_text(encoding="utf-8").startswith("# notes\n")

    @pytest.mark.asyncio
    async def test_duplicate_facts_are_not_appended(self, tmp_path):
        """An agent writing memory every turn would otherwise degrade its
        own retrieval one duplicate at a time."""
        provider = LocalMarkdownMemory(tmp_path / "m.md")
        await provider.write("the same fact")
        await provider.write("the same fact")
        assert provider.path.read_text(encoding="utf-8").count("the same fact") == 1

    @pytest.mark.asyncio
    async def test_hand_edited_file_without_trailing_newline(self, tmp_path):
        """Appending blind would splice the bullet onto the last line."""
        path = tmp_path / "m.md"
        path.write_text("# notes\n\n- first", encoding="utf-8")
        await LocalMarkdownMemory(path).write("second")
        assert "- first\n- second\n" in path.read_text(encoding="utf-8")

    @pytest.mark.asyncio
    async def test_empty_write_is_rejected(self, tmp_path):
        with pytest.raises(ValueError, match=r"empty memory"):
            await LocalMarkdownMemory(tmp_path / "m.md").write("   ")

    @pytest.mark.asyncio
    async def test_write_failure_propagates(self):
        """The loud direction — a silent no-op write is the failure this
        policy exists to prevent."""
        with pytest.raises(RuntimeError, match="disk full"):
            await Boom().write("something")


class TestPrefetchFailsSoft:
    @pytest.mark.asyncio
    async def test_provider_exception_is_swallowed(self):
        assert await Boom().prefetch("q") == []

    @pytest.mark.asyncio
    async def test_empty_query_short_circuits(self, notes):
        assert await notes.prefetch("") == []

    @pytest.mark.asyncio
    async def test_zero_limit_short_circuits(self, notes):
        assert await notes.prefetch("deploys", limit=0) == []

    @pytest.mark.asyncio
    async def test_malformed_entries_are_dropped(self):
        class Sloppy(MemoryProvider):
            async def _prefetch(self, query, limit):
                return [MemoryEntry(""), "not an entry", None, MemoryEntry("good")]

            async def _write(self, text, source):
                pass

        assert [e.text for e in await Sloppy().prefetch("q")] == ["good"]


class TestOps:
    @pytest.mark.asyncio
    async def test_prefetch_op_enforces_a_deadline(self):
        """MemoryProvider.prefetch cannot time out on itself, so the op
        does it — and one slow provider must cost only its own deadline."""
        out = await provider_prefetch.__wrapped__(provider=Boom("hang"), query="q", deadline=0.05)
        assert out["entries"] == []

    @pytest.mark.asyncio
    async def test_prefetch_op_with_no_provider(self):
        out = await provider_prefetch.__wrapped__(provider=None, query="q")
        assert out["entries"] == []

    def test_each_provider_yields_one_frame_each(self):
        frames = list(each_provider.__wrapped__(providers=["a", "b"]))
        assert [f["provider"] for f in frames] == ["a", "b"]

    def test_each_provider_with_none_yields_nothing(self):
        assert list(each_provider.__wrapped__(providers=None)) == []

    def test_merge_renders_a_block(self):
        out = merge_memory.__wrapped__(entries=[[MemoryEntry("x"), MemoryEntry("y")]])
        assert out["context"] == "<memory>\n- x\n- y\n</memory>"
        assert out["count"] == 2

    def test_merge_deduplicates_across_providers(self):
        out = merge_memory.__wrapped__(
            entries=[[MemoryEntry("same", score=0.2)], [MemoryEntry("SAME ", score=0.9)]]
        )
        assert out["count"] == 1
        assert out["entries"][0].score == 0.9, "the better-scoring copy should win"

    def test_merge_orders_by_score(self):
        out = merge_memory.__wrapped__(
            entries=[[MemoryEntry("low", score=0.1), MemoryEntry("high", score=0.9)]]
        )
        assert [e.text for e in out["entries"]] == ["high", "low"]

    @pytest.mark.parametrize(
        "shape",
        [
            pytest.param(None, id="none"),
            pytest.param([], id="empty"),
            pytest.param([[]], id="empty-group"),
        ],
    )
    def test_merge_yields_none_when_empty(self, shape):
        """An always-present but sometimes-empty wrapper changes the
        prompt prefix on the turns it is empty — a cache miss on each."""
        assert merge_memory.__wrapped__(entries=shape)["context"] is None

    def test_merge_accepts_collects_several_shapes(self):
        """collect() hands over a bare value for one item, a list for
        many, and can arrive per item inside a loop."""
        single = merge_memory.__wrapped__(entries=MemoryEntry("solo"))
        flat = merge_memory.__wrapped__(entries=[MemoryEntry("solo")])
        nested = merge_memory.__wrapped__(entries=[[MemoryEntry("solo")]])
        assert single["count"] == flat["count"] == nested["count"] == 1

    @pytest.mark.asyncio
    async def test_write_op_reports_success(self, tmp_path):
        provider = LocalMarkdownMemory(tmp_path / "m.md")
        out = await memory_write.__wrapped__(provider=provider, text="a fact")
        assert out == {"written": True, "error": ""}

    @pytest.mark.asyncio
    async def test_write_op_reports_failure_rather_than_raising(self):
        out = await memory_write.__wrapped__(provider=Boom(), text="a fact")
        assert out["written"] is False
        assert "disk full" in out["error"]

    @pytest.mark.asyncio
    async def test_write_op_rejects_a_non_provider(self):
        out = await memory_write.__wrapped__(provider=object(), text="a fact")
        assert out["written"] is False
        assert "not a MemoryProvider" in out["error"]


class TestRenderBlock:
    def test_none_for_empty(self):
        assert render_memory_block([]) is None

    def test_uses_the_label(self):
        block = render_memory_block([MemoryEntry("x")], label="project memory")
        assert block.startswith("<project memory>")
