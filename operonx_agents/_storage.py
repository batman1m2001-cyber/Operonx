"""The connections sessions and state stores share: Redis and SQLite.

Redis is the ``redis`` package's asyncio client, a ``Redis`` or a
``RedisCluster``: every key a session or a store touches is one key, so
both work. SQLite is the standard library's, each call run in a thread so
the event loop never waits on the disk.
"""

from __future__ import annotations

import asyncio
import sqlite3
import threading
from pathlib import Path
from typing import Any, Callable, Optional, TypeVar, Union

__all__ = ["SQLite", "redis_client"]

T = TypeVar("T")


def redis_client(client: Any, url: Optional[str], owner: str) -> Any:
    """``client`` as given, or one built from ``url``."""
    if client is not None:
        if url is not None:
            raise ValueError(f"{owner}: give client= or url=, not both.")
        return client
    if url is None:
        raise ValueError(
            f"{owner} needs a Redis connection: client=redis.asyncio.Redis(...) (or a "
            "RedisCluster), or url='redis://host:6379/0'."
        )
    try:
        from redis import asyncio as aioredis
    except ImportError as exc:  # pragma: no cover - depends on the install
        raise ImportError(
            f"{owner} needs the redis package: pip install 'operonx-agents[redis]'."
        ) from exc
    return aioredis.from_url(url)


class SQLite:
    """One SQLite database file, shared by the sessions and stores on it.

    WAL mode, so readers do not block the writer; a commit is durable when
    the call returns, which is what crash recovery relies on.
    """

    def __init__(self, path: Union[str, Path], schema: str) -> None:
        self.path = str(path)
        self._lock = threading.Lock()
        self._db = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA synchronous=FULL")
        self._db.executescript(schema)

    async def run(self, fn: Callable[[sqlite3.Connection], T]) -> T:
        """``fn(connection)`` in a thread, inside one transaction."""

        def call() -> T:
            with self._lock:
                self._db.execute("BEGIN IMMEDIATE")
                try:
                    out = fn(self._db)
                except BaseException:
                    self._db.execute("ROLLBACK")
                    raise
                self._db.execute("COMMIT")
                return out

        return await asyncio.to_thread(call)

    def close(self) -> None:
        self._db.close()
