"""Sessions and state stores: one contract, three backends.

Memory and SQLite always; Redis when ``REDIS_URL`` points at a throwaway
server (every key here lives under a per-test prefix and is deleted).
"""

from __future__ import annotations

import os
import uuid

import pytest

from operonx_agents import (
    InMemorySession,
    InMemoryStateStore,
    RedisSession,
    RedisStateStore,
    RunState,
    Session,
    SQLiteSession,
    SQLiteStateStore,
    StateStore,
)
from operonx_agents.run.state import PendingTurn

REDIS = os.environ.get("REDIS_URL")
KINDS = [
    "memory",
    "sqlite",
    pytest.param("redis", marks=pytest.mark.skipif(not REDIS, reason="REDIS_URL not set")),
]


@pytest.fixture(params=KINDS)
async def backend(request, tmp_path):
    kind = request.param
    if kind == "memory":
        yield InMemorySession("s1"), InMemoryStateStore()
        return
    if kind == "sqlite":
        db = tmp_path / "runs.db"
        yield SQLiteSession("s1", db), SQLiteStateStore(db)
        return
    from redis import asyncio as aioredis

    client = aioredis.from_url(REDIS)
    prefix = f"test-{uuid.uuid4().hex}:"
    yield (
        RedisSession("s1", client, prefix=prefix + "session:"),
        RedisStateStore(client, prefix=prefix + "run:"),
    )
    for key in await client.keys(prefix + "*"):
        await client.delete(key)
    await client.aclose()


ITEMS = [
    {"role": "user", "content": "xin chào"},
    {"role": "assistant", "content": "", "tool_calls": [{"id": "c", "name": "t", "args": {}}]},
    {"role": "tool", "tool_call_id": "c", "content": "ok"},
]


async def test_session_contract(backend):
    session, _ = backend
    assert isinstance(session, Session)
    assert await session.get_items() == []
    await session.add_items(ITEMS)
    await session.add_items([])
    assert await session.get_items() == ITEMS, "unicode and nested values round-trip"
    assert await session.get_items(limit=2) == ITEMS[1:]
    assert await session.get_items(limit=0) == []
    assert await session.pop_item() == ITEMS[-1]
    assert await session.get_items() == ITEMS[:2]
    await session.clear()
    assert await session.get_items() == [] and await session.pop_item() is None


async def test_sessions_are_separate(tmp_path):
    db = tmp_path / "s.db"
    a, b = SQLiteSession("a", db), SQLiteSession("b", db)
    await a.add_items(ITEMS[:1])
    assert await b.get_items() == []


async def test_store_contract(backend):
    _, store = backend
    assert isinstance(store, StateStore)
    assert await store.load("r") is None
    state = RunState(
        run_id="r",
        agent="a",
        messages=ITEMS,
        pending=PendingTurn(items=ITEMS[1:2], messages=ITEMS[:2], calls=[], inflight=["c"]),
    )
    await store.save(state)
    assert await store.load("r") == state
    state.turn = 3
    await store.save(state)
    assert (await store.load("r")).turn == 3, "a save replaces the state whole"
    await store.delete("r")
    assert await store.load("r") is None


def test_a_state_from_another_format_is_refused():
    text = RunState(run_id="r", agent="a").dumps().replace('"version":1', '"version":9')
    with pytest.raises(ValueError, match="format 9"):
        RunState.loads(text)


def test_redis_needs_a_connection():
    with pytest.raises(ValueError, match="needs a Redis connection"):
        RedisSession("s")
    with pytest.raises(ValueError, match="not both"):
        RedisStateStore(object(), url="redis://x")


async def test_sessions_and_stores_on_one_file_share_one_connection(tmp_path):
    db = tmp_path / "chat.db"
    a, b, store = SQLiteSession("a", db), SQLiteSession("b", str(db)), SQLiteStateStore(db)
    other = SQLiteSession("c", tmp_path / "other.db")
    assert a._db._file is b._db._file is store._db._file and other._db._file is not a._db._file
    a.close()
    await b.add_items(ITEMS)  # one closing leaves the others open
    assert await b.get_items() == ITEMS
    b.close(), store.close(), other.close()
    assert a._db._file.users == 0


async def test_many_sessions_writing_at_once_never_hit_a_locked_database(tmp_path):
    import asyncio

    db = tmp_path / "chat.db"
    sessions = [SQLiteSession(f"user{k}", db) for k in range(50)]

    async def talk(s):
        for t in range(10):
            await s.get_items(limit=40)
            await s.add_items([{"role": "user", "content": f"{s.session_id} {t}"}])

    await asyncio.gather(*(talk(s) for s in sessions))
    assert [len(await s.get_items()) for s in sessions] == [10] * 50
    for s in sessions:
        s.close()


async def test_a_read_takes_no_write_lock(tmp_path):
    import sqlite3

    db = tmp_path / "chat.db"
    s = SQLiteSession("s1", db)
    await s.add_items(ITEMS)
    writer = sqlite3.connect(db, isolation_level=None)  # another process writing
    writer.execute("BEGIN IMMEDIATE")
    try:
        assert await s.get_items() == ITEMS  # a write lock would wait 10 s, then fail
    finally:
        writer.execute("ROLLBACK")
        writer.close()
        s.close()
