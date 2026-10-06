"""``StateStore`` — where a :class:`RunState` lives between turns.

Three backends: :class:`InMemoryStateStore` (one process, tests),
:class:`RedisStateStore` and :class:`SQLiteStateStore` (survive the
process: what crash recovery and a resume in another process need).

A store is not a ``Checkpointer``: that one observes a graph run; this one
holds the state an agent's explicit loop continues from.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional, Protocol, Union, runtime_checkable

from operonx_agents._storage import SQLite, redis_client
from operonx_agents.run.state import RunState

__all__ = ["InMemoryStateStore", "RedisStateStore", "SQLiteStateStore", "StateStore"]


@runtime_checkable
class StateStore(Protocol):
    """Saves and loads run states by ``run_id``. A save replaces the
    previous one whole: one write, never half a state."""

    async def save(self, state: RunState) -> None: ...

    async def load(self, run_id: str) -> Optional[RunState]: ...

    async def delete(self, run_id: str) -> None: ...


class InMemoryStateStore:
    """A dict of serialised states: the same JSON round trip as the
    durable stores, so a state that would not survive one fails here."""

    def __init__(self) -> None:
        self._states: Dict[str, str] = {}

    async def save(self, state: RunState) -> None:
        self._states[state.run_id] = state.dumps()

    async def load(self, run_id: str) -> Optional[RunState]:
        text = self._states.get(run_id)
        return RunState.loads(text) if text is not None else None

    async def delete(self, run_id: str) -> None:
        self._states.pop(run_id, None)

    def __len__(self) -> int:
        return len(self._states)


class RedisStateStore:
    """One Redis string per run, ``<prefix><run_id>``.

    Args:
        client: A ``redis.asyncio`` ``Redis`` or ``RedisCluster``.
        url: Or a URL to build one from.
        prefix: The key prefix.
        ttl: Seconds a state is kept after its last save; ``None`` keeps it.
    """

    def __init__(
        self,
        client: Any = None,
        *,
        url: Optional[str] = None,
        prefix: str = "operonx_agents:run:",
        ttl: Optional[int] = None,
    ) -> None:
        self.client = redis_client(client, url, "RedisStateStore")
        self.prefix = prefix
        self.ttl = ttl

    async def save(self, state: RunState) -> None:
        await self.client.set(self.prefix + state.run_id, state.dumps(), ex=self.ttl)

    async def load(self, run_id: str) -> Optional[RunState]:
        raw = await self.client.get(self.prefix + run_id)
        if raw is None:
            return None
        return RunState.loads(raw.decode() if isinstance(raw, bytes) else raw)

    async def delete(self, run_id: str) -> None:
        await self.client.delete(self.prefix + run_id)


_SCHEMA = """
CREATE TABLE IF NOT EXISTS operonx_agents_runs (
    run_id TEXT PRIMARY KEY,
    state TEXT NOT NULL
);
"""


class SQLiteStateStore:
    """One row per run in a SQLite file (created if missing)."""

    def __init__(self, path: Union[str, Path]) -> None:
        self._db = SQLite(path, _SCHEMA)

    async def save(self, state: RunState) -> None:
        text = state.dumps()
        await self._db.run(
            lambda db: db.execute(
                "INSERT INTO operonx_agents_runs (run_id, state) VALUES (?, ?) "
                "ON CONFLICT(run_id) DO UPDATE SET state = excluded.state",
                (state.run_id, text),
            )
        )

    async def load(self, run_id: str) -> Optional[RunState]:
        row = await self._db.run(
            lambda db: db.execute(
                "SELECT state FROM operonx_agents_runs WHERE run_id = ?", (run_id,)
            ).fetchone()
        )
        return RunState.loads(row[0]) if row else None

    async def delete(self, run_id: str) -> None:
        await self._db.run(
            lambda db: db.execute("DELETE FROM operonx_agents_runs WHERE run_id = ?", (run_id,))
        )

    def close(self) -> None:
        self._db.close()
