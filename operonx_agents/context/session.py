"""``Session`` — a conversation that outlives one run.

The OpenAI Agents SDK's shape: ``get_items / add_items / pop_item /
clear``. A session holds **committed** items only: the runner writes a
turn — the model's reply and every tool message answering it — after the
whole turn is done, in one ``add_items``. So a stored history never ends
on an unanswered tool call, and a run cancelled or failed mid-turn leaves
the session as it was.

Items are chat messages (``role`` ``user`` / ``assistant`` / ``tool``),
plus the ``summary`` items compaction appends
(:mod:`operonx_agents.context.compaction`): the session is append-only,
and a summary says which earlier items it stands for.

One run at a time per session: two runs appending to one session
interleave their turns.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Protocol, Union, runtime_checkable

from operonx_agents._storage import SQLite, redis_client

__all__ = ["InMemorySession", "RedisSession", "SQLiteSession", "Session"]


@runtime_checkable
class Session(Protocol):
    """A conversation's committed items, oldest first."""

    session_id: str

    async def get_items(self, limit: Optional[int] = None) -> List[Dict[str, Any]]:
        """All items, or the last ``limit``."""
        ...

    async def add_items(self, items: List[Dict[str, Any]]) -> None:
        """Append ``items`` in one write: all of them or none."""
        ...

    async def pop_item(self) -> Optional[Dict[str, Any]]:
        """Remove and return the last item (``None`` when empty)."""
        ...

    async def clear(self) -> None: ...


class InMemorySession:
    """A list. Items are copied in and out, so a caller mutating one
    cannot rewrite what the next run sends."""

    def __init__(self, session_id: str = "default") -> None:
        self.session_id = session_id
        self._items: List[str] = []

    async def get_items(self, limit: Optional[int] = None) -> List[Dict[str, Any]]:
        items = self._items if limit is None else self._items[-limit:] if limit else []
        return [json.loads(i) for i in items]

    async def add_items(self, items: List[Dict[str, Any]]) -> None:
        self._items.extend(_dumps(i) for i in items)

    async def pop_item(self) -> Optional[Dict[str, Any]]:
        return json.loads(self._items.pop()) if self._items else None

    async def clear(self) -> None:
        self._items.clear()


class RedisSession:
    """One Redis list per session, ``<prefix><session_id>``.

    ``add_items`` is one ``RPUSH`` of every item, so a turn lands whole.

    Args:
        session_id: The conversation's key (a user, a call).
        client: A ``redis.asyncio`` ``Redis`` or ``RedisCluster``.
        url: Or a URL to build one from.
        prefix: The key prefix.
        ttl: Seconds the session is kept after its last write.
    """

    def __init__(
        self,
        session_id: str,
        client: Any = None,
        *,
        url: Optional[str] = None,
        prefix: str = "operonx_agents:session:",
        ttl: Optional[int] = None,
    ) -> None:
        self.session_id = session_id
        self.client = redis_client(client, url, "RedisSession")
        self.key = prefix + session_id
        self.ttl = ttl

    async def get_items(self, limit: Optional[int] = None) -> List[Dict[str, Any]]:
        if limit is not None and limit <= 0:
            return []
        start = -limit if limit else 0
        return [json.loads(raw) for raw in await self.client.lrange(self.key, start, -1)]

    async def add_items(self, items: List[Dict[str, Any]]) -> None:
        if not items:
            return
        await self.client.rpush(self.key, *(_dumps(i) for i in items))
        if self.ttl:
            await self.client.expire(self.key, self.ttl)

    async def pop_item(self) -> Optional[Dict[str, Any]]:
        raw = await self.client.rpop(self.key)
        return json.loads(raw) if raw is not None else None

    async def clear(self) -> None:
        await self.client.delete(self.key)


_SCHEMA = """
CREATE TABLE IF NOT EXISTS operonx_agents_items (
    session_id TEXT NOT NULL,
    seq INTEGER NOT NULL,
    item TEXT NOT NULL,
    PRIMARY KEY (session_id, seq)
);
"""


class SQLiteSession:
    """One row per item in a SQLite file (created if missing)."""

    def __init__(self, session_id: str, path: Union[str, Path]) -> None:
        self.session_id = session_id
        self._db = SQLite(path, _SCHEMA)

    async def get_items(self, limit: Optional[int] = None) -> List[Dict[str, Any]]:
        if limit is not None and limit <= 0:
            return []
        sql = "SELECT item FROM operonx_agents_items WHERE session_id = ? ORDER BY seq"
        args: tuple = (self.session_id,)
        if limit:
            sql = (
                "SELECT item FROM (SELECT seq, item FROM operonx_agents_items "
                "WHERE session_id = ? ORDER BY seq DESC LIMIT ?) ORDER BY seq"
            )
            args = (self.session_id, limit)
        rows = await self._db.run(lambda db: db.execute(sql, args).fetchall())
        return [json.loads(r[0]) for r in rows]

    async def add_items(self, items: List[Dict[str, Any]]) -> None:
        if not items:
            return
        texts = [_dumps(i) for i in items]

        def write(db) -> None:
            (top,) = db.execute(
                "SELECT COALESCE(MAX(seq), -1) FROM operonx_agents_items WHERE session_id = ?",
                (self.session_id,),
            ).fetchone()
            db.executemany(
                "INSERT INTO operonx_agents_items (session_id, seq, item) VALUES (?, ?, ?)",
                [(self.session_id, top + 1 + i, t) for i, t in enumerate(texts)],
            )

        await self._db.run(write)

    async def pop_item(self) -> Optional[Dict[str, Any]]:
        def pop(db) -> Optional[str]:
            row = db.execute(
                "SELECT seq, item FROM operonx_agents_items WHERE session_id = ? "
                "ORDER BY seq DESC LIMIT 1",
                (self.session_id,),
            ).fetchone()
            if row is None:
                return None
            db.execute(
                "DELETE FROM operonx_agents_items WHERE session_id = ? AND seq = ?",
                (self.session_id, row[0]),
            )
            return row[1]

        text = await self._db.run(pop)
        return json.loads(text) if text is not None else None

    async def clear(self) -> None:
        await self._db.run(
            lambda db: db.execute(
                "DELETE FROM operonx_agents_items WHERE session_id = ?", (self.session_id,)
            )
        )

    def close(self) -> None:
        self._db.close()


def _dumps(item: Dict[str, Any]) -> str:
    return json.dumps(item, ensure_ascii=False, default=str)
