"""Long-term memory: a store per owner that keeps few items (duplicates
refresh, expired ones stop counting and are purged), seals its text with a
codec, and three tools whose owner comes from the run, never the model."""

from __future__ import annotations

import asyncio
import base64
import time

import pytest

from operonx_agents import (
    Approve,
    InMemoryMemoryStore,
    InMemoryStateStore,
    Runner,
    SQLiteMemoryStore,
    memory_tools,
)
from operonx_agents.memory import fold, search
from tests.agents import asks as call
from tests.agents import make, says


@pytest.fixture(params=["memory", "sqlite"])
def store(request, tmp_path):
    if request.param == "memory":
        yield InMemoryMemoryStore()
    else:
        s = SQLiteMemoryStore(tmp_path / "m.db")
        yield s
        s.close()


class TestStore:
    async def test_items_are_the_owners_own(self, store):
        a = await store.add(
            "ann", "preference", "Thích báo cáo ngắn", source={"conversation": "c1", "seq": 3}
        )
        await store.add("bob", "goal", "Mua nhà năm 2028")
        assert [i.text for i in await store.items("ann")] == ["Thích báo cáo ngắn"]
        assert (await store.get("ann", a.id)).source == {"conversation": "c1", "seq": 3}
        assert await store.get("bob", a.id) is None  # an id is not a key to another owner's item
        assert (
            await store.update("bob", a.id, text="x") is None
            and await store.delete("bob", a.id) is False
        )
        assert await store.clear("bob") == 1 and [i.id for i in await store.items("ann")] == [a.id]

    async def test_the_same_thing_twice_is_one_item_refreshed(self, store):
        a = await store.add("ann", "goal", "Mua nhà  năm 2028", confidence=0.6)
        time.sleep(0.01)
        b = await store.add("ann", "goal", "mua nha nam 2028", confidence=0.9)
        assert b.id == a.id and b.confidence == 0.9 and b.updated > a.updated
        assert len(await store.items("ann")) == 1
        c = await store.add("ann", "fact", "Mua nhà năm 2028")  # another kind is another item
        assert c.id != a.id

    async def test_update_and_delete(self, store):
        a = await store.add("ann", "fact", "Có hai con")
        u = await store.update("ann", a.id, text="Có ba con", kind="fact")
        assert u.text == "Có ba con" and (await store.get("ann", a.id)).text == "Có ba con"
        with pytest.raises(ValueError):
            await store.update("ann", a.id, text="  ")
        assert await store.delete("ann", a.id) and await store.get("ann", a.id) is None

    async def test_expired_items_stop_counting_and_are_purged(self, store):
        old = await store.add("ann", "fact", "Đang thuê nhà", expires=time.time() - 1)
        await store.add("ann", "preference", "Gọi buổi sáng")
        assert [i.text for i in await store.items("ann")] == ["Gọi buổi sáng"]
        assert len(await store.items("ann", active=False)) == 2
        again = await store.add(
            "ann", "fact", "Đang thuê nhà"
        )  # an expired item is not its duplicate
        assert again.id != old.id
        assert await store.purge() == 1 and await store.get("ann", old.id) is None

    async def test_kinds_and_limits(self, store):
        for k, t in [("goal", "a"), ("fact", "b"), ("goal", "c")]:
            await store.add("ann", k, t)
        assert [i.text for i in await store.items("ann", kinds=["goal"])] == [
            "c",
            "a",
        ]  # newest first
        assert len(await store.items("ann", limit=2)) == 2
        with pytest.raises(ValueError):
            await store.add("ann", "", "x")

    async def test_search_folds_and_ranks(self, store):
        await store.add("ann", "goal", "Mua nhà cho con năm 2028")
        await store.add("ann", "preference", "Không thích cổ phiếu")
        await store.add("bob", "goal", "Mua nhà")
        got = await search(store, "ann", "mua nha")
        assert [i.text for i in got] == ["Mua nhà cho con năm 2028"]
        assert len(await search(store, "ann", "")) == 2 and fold("  Cổ  PHIẾU ") == "co phieu"


class Rot13Codec:
    """A stand-in for real encryption: what matters is that the stored text
    is not the text, and that the place is checked."""

    def encode(self, text, place):
        return "enc:" + base64.b64encode(f"{place}|{text}".encode()).decode()

    def decode(self, text, place):
        got_place, _, plain = base64.b64decode(text[4:]).decode().partition("|")
        assert got_place == place, "moved to another row"
        return plain


async def test_a_codec_seals_the_text_at_rest(tmp_path):
    path = tmp_path / "m.db"
    store = SQLiteMemoryStore(path, codec=Rot13Codec())
    a = await store.add("ann", "goal", "Mua nhà năm 2028")
    dup = await store.add(
        "ann", "goal", "mua nha nam 2028"
    )  # duplicates are found through the codec
    await store.update("ann", a.id, text="Mua nhà năm 2029")
    assert dup.id == a.id and (await store.get("ann", a.id)).text == "Mua nhà năm 2029"
    raw = b"".join(f.read_bytes() for f in tmp_path.glob("m.db*"))
    assert "Mua nhà".encode() not in raw and b"enc:" in raw
    store.close()


async def test_one_file_many_owners_at_once(tmp_path):
    stores = [SQLiteMemoryStore(tmp_path / "m.db") for _ in range(20)]
    await asyncio.gather(*(s.add(f"o{i % 5}", "fact", f"điều {i}") for i, s in enumerate(stores)))
    assert [len(await stores[0].items(f"o{k}")) for k in range(5)] == [4] * 5
    for s in stores:
        s.close()


class TestTools:
    def tools(self, store, **kw):
        return memory_tools(store, owner=lambda ctx: ctx.deps, **kw)

    async def test_remember_then_recall_for_the_run_owner_only(self, hub):
        store = InMemoryMemoryStore()
        await store.add("bob", "goal", "Bí mật của Bob")
        agent, llm = make(
            hub,
            call(("remember", {"kind": "preference", "text": "Thích báo cáo ngắn"})),
            call(("recall", {"query": ""}), turn=1),
            says("Đã nhớ."),
            tools=self.tools(store),
        )
        res = await Runner.run(agent, "Hãy nhớ tôi thích báo cáo ngắn", deps="ann")
        assert res.status == "completed"
        tool_msgs = [m["content"] for m in res.messages if m["role"] == "tool"]
        assert "Verified" in tool_msgs[0] and "Thích báo cáo ngắn" in tool_msgs[1]
        assert "Bob" not in " ".join(tool_msgs)
        (item,) = await store.items("ann")
        assert item.source["run"] == res.run_id
        schema = {
            t["function"]["name"]: t["function"]["parameters"] for t in llm.requests[0]["tools"]
        }
        assert all(
            "owner" not in s.get("properties", {}) for s in schema.values()
        )  # never the model's to say

    async def test_the_rules_refuse_and_nothing_is_kept(self, hub):
        store = InMemoryMemoryStore()
        no_numbers = lambda t: "an amount" if any(c.isdigit() for c in t) else None  # noqa: E731
        agent, _ = make(
            hub,
            call(("remember", {"kind": "fact", "text": "Có 5 tỷ tiền gửi"})),
            call(("remember", {"kind": "mood", "text": "Vui"}), turn=1),
            call(("remember", {"kind": "fact", "text": "x" * 201}), turn=2),
            says("Không lưu được."),
            tools=self.tools(store, check=no_numbers),
        )
        res = await Runner.run(agent, "nhớ giúp", deps="ann")
        errors = [m["content"] for m in res.messages if m["role"] == "tool"]
        assert (
            "not kept: an amount" in errors[0]
            and "kind must be one of" in errors[1]
            and "201" in errors[2]
        )
        assert await store.items("ann") == []

    async def test_forget_is_checked_and_scoped(self, hub):
        store = InMemoryMemoryStore()
        mine = await store.add("ann", "goal", "Mua nhà")
        theirs = await store.add("bob", "goal", "Mua xe")
        agent, _ = make(
            hub,
            call(("forget", {"item_id": theirs.id})),
            call(("forget", {"item_id": mine.id}), turn=1),
            says("Đã quên."),
            tools=self.tools(store),
        )
        res = await Runner.run(agent, "quên đi", deps="ann")
        out = [m["content"] for m in res.messages if m["role"] == "tool"]
        assert "no such item" in out[0] and "Verified" in out[1]
        assert await store.get("bob", theirs.id) is not None and await store.items("ann") == []

    async def test_with_confirm_a_write_waits_for_the_person(self, hub):
        store = InMemoryMemoryStore()
        agent, _ = make(
            hub,
            call(("remember", {"kind": "goal", "text": "Nghỉ hưu sớm"})),
            says("Đã nhớ."),
            tools=self.tools(store, confirm=True),
        )
        runs = InMemoryStateStore()
        res = await Runner.run(agent, "nhớ giúp", deps="ann", store=runs)
        assert res.status == "interrupted" and await store.items("ann") == []
        done = await Runner.resume(
            agent,
            res.run_id,
            store=runs,
            deps="ann",
            approvals={res.interruptions[0].id: Approve()},
        )
        assert done.status == "completed" and [i.text for i in await store.items("ann")] == [
            "Nghỉ hưu sớm"
        ]

    async def test_expiry_by_kind(self, hub):
        store = InMemoryMemoryStore()
        agent, _ = make(
            hub,
            call(("remember", {"kind": "fact", "text": "Đang thuê nhà"})),
            says("ok"),
            tools=self.tools(
                store, expires=lambda k: time.time() + 180 * 86400 if k == "fact" else None
            ),
        )
        await Runner.run(agent, "nhớ", deps="ann")
        (item,) = await store.items("ann")
        assert 179 * 86400 < item.expires - time.time() <= 180 * 86400
