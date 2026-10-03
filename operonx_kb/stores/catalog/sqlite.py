"""The SQLite catalog: stdlib ``sqlite3``, WAL, versioned migrations.

A connection per transaction (operonx's own SQLite stores do the same), so the
catalog is safe to call from the worker threads ``bound="cpu"`` ops run in.
Writes take ``BEGIN IMMEDIATE``, so writers serialise instead of interleaving.
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Iterator, List, Sequence

from operonx_kb.stores.catalog.sql import SqlCatalog, Tx

__all__ = ["SqliteCatalog"]


class _SqliteTx(Tx):
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def rows(self, sql: str, args: Sequence[Any] = ()) -> List[Dict[str, Any]]:
        cur = self.conn.execute(sql, tuple(args))
        names = [d[0] for d in cur.description or ()]
        return [dict(zip(names, row)) for row in cur.fetchall()]

    def run(self, sql: str, args: Sequence[Any] = ()) -> int:
        return self.conn.execute(sql, tuple(args)).rowcount

    def many(self, sql: str, rows: Sequence[Sequence[Any]]) -> None:
        if rows:
            self.conn.executemany(sql, rows)


class SqliteCatalog(SqlCatalog):
    """A catalog in one SQLite file.

    Args:
        path: The database file; its folder is created.
    """

    dialect = "sqlite"

    def __init__(self, path: Any):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.migrate()

    @contextmanager
    def _transaction(self, write: bool) -> Iterator[Tx]:
        conn = sqlite3.connect(str(self.path), timeout=30, isolation_level=None)
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            try:
                yield _SqliteTx(conn)
            except BaseException:
                conn.execute("ROLLBACK")
                raise
            conn.execute("COMMIT")
        finally:
            conn.close()
