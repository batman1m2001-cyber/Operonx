"""The connections sessions and state stores share: Redis and SQLite.

Redis is the ``redis`` package's asyncio client, a ``Redis`` or a
``RedisCluster``: every key a session or a store touches is one key, so
both work. SQLite is the standard library's, each call run in a thread so
the event loop never waits on the disk.
"""

from __future__ import annotations

import asyncio
import os
import sqlite3
import threading
from pathlib import Path
from typing import Any, Callable, Dict, Optional, TypeVar, Union

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


class _File:
    """The one connection a process holds on a database file."""

    def __init__(self, path: str) -> None:
        self.lock = threading.Lock()
        self.db = sqlite3.connect(path, check_same_thread=False, isolation_level=None, timeout=10)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.schemas: set = set()
        self.users = 0


_FILES: Dict[str, _File] = {}
_FILES_LOCK = threading.Lock()


class SQLite:
    """One SQLite database file, shared by the sessions and stores on it.

    Every ``SQLite`` on the same file in a process shares one connection:
    writes are serialised here instead of racing for SQLite's lock (fifty
    sessions, each with its own connection, failed with "database is
    locked" under load). WAL mode, so readers do not block the writer, and
    a read takes no write lock (another process may write meanwhile); a
    commit is durable when the call returns, which is what crash recovery
    relies on. The connection closes with the last ``close()``.
    """

    def __init__(self, path: Union[str, Path], schema: str) -> None:
        self.path = str(path)
        self._key = self.path if self.path == ":memory:" else os.path.realpath(self.path)
        with _FILES_LOCK:
            f = _FILES.get(self._key) if self._key != ":memory:" else None
            if f is None:
                f = _File(self.path)
                if self._key != ":memory:":
                    _FILES[self._key] = f
            f.users += 1
        if schema not in f.schemas:
            with f.lock:
                f.db.executescript(schema)
                f.schemas.add(schema)
        self._file = f
        self._closed = False

    async def run(self, fn: Callable[[sqlite3.Connection], T], *, write: bool = True) -> T:
        """``fn(connection)`` in a thread, inside one transaction; ``write=False`` for a read,
        which then takes no write lock."""
        f = self._file

        def call() -> T:
            with f.lock:
                f.db.execute("BEGIN IMMEDIATE" if write else "BEGIN")
                try:
                    out = fn(f.db)
                except BaseException:
                    f.db.execute("ROLLBACK")
                    raise
                f.db.execute("COMMIT")
                return out

        return await asyncio.to_thread(call)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        with _FILES_LOCK:
            f = self._file
            f.users -= 1
            if f.users == 0:
                if _FILES.get(self._key) is f:
                    del _FILES[self._key]
                f.db.close()
