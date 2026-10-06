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
