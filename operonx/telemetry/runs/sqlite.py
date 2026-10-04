"""The SQLite store — everything in one file.

Summaries and rollups in the shared SQL schema; each run's full record
(``meta`` and its rows) compressed in a third table, and a running run's
executions in ``live`` as they land (see :class:`~.sql.SqlRunStore`).
Large payloads (audio, arrays) are offloaded to a media directory beside
the file, as the local consumer does, so the database stays small and a
record still points at its media.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from operonx.telemetry.consumers.local import resolve_root

from .sql import SqlRunStore

__all__ = ["SqliteRunStore"]


class SqliteRunStore(SqlRunStore):
    """See the module docstring. ``path`` defaults to ``runs.sqlite``
    under the runs root (``<project>/.operonx/runs``)."""

    def __init__(self, path: Any = "", media_threshold: int = 1024, live: bool = True):
        self.path = Path(path) if path else resolve_root("") / "runs.sqlite"
        if not self.path.is_absolute():
            self.path = resolve_root("") / self.path
        self.path.parent.mkdir(parents=True, exist_ok=True)

        def connect() -> sqlite3.Connection:
            conn = sqlite3.connect(str(self.path), timeout=30)
            conn.execute("PRAGMA journal_mode=WAL")
            return conn

        self._connect = connect
        super().__init__(
            connect,
            media_dir=self.path.with_suffix(".media"),
            media_threshold=media_threshold,
            live=live,
            config={"path": str(path)},
        )
