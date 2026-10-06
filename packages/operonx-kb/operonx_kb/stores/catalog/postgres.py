"""The Postgres catalog (the ``postgres`` extra): psycopg 3 with a connection pool.

The same statements as the SQLite catalog (:mod:`operonx_kb.stores.catalog.sql`).
Writers of one document serialise on a transaction-scoped advisory lock keyed
by the document id, so two ingests of one document cannot interleave while
ingests of different documents run in parallel. ``schema`` keeps the catalog's
tables in their own Postgres schema (created on first use).
"""

from __future__ import annotations

import re
from contextlib import contextmanager
from typing import Any, Dict, Iterator, List, Sequence

from operonx_kb.errors import MissingExtraError
from operonx_kb.stores.catalog.sql import SqlCatalog, Tx

__all__ = ["PostgresCatalog"]

_SCHEMA = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")


class _PgTx(Tx):
    def __init__(self, conn: Any):
        self.conn = conn

    @staticmethod
    def _sql(sql: str) -> str:
        return sql.replace("?", "%s")

    def rows(self, sql: str, args: Sequence[Any] = ()) -> List[Dict[str, Any]]:
        with self.conn.cursor() as cur:
            cur.execute(self._sql(sql), tuple(args))
            return list(cur.fetchall()) if cur.description else []

    def run(self, sql: str, args: Sequence[Any] = ()) -> int:
        with self.conn.cursor() as cur:
            cur.execute(self._sql(sql), tuple(args))
            return max(cur.rowcount, 0)

    def many(self, sql: str, rows: Sequence[Sequence[Any]]) -> None:
        if rows:
            with self.conn.cursor() as cur:
                cur.executemany(self._sql(sql), [tuple(r) for r in rows])


class PostgresCatalog(SqlCatalog):
    """A catalog in a Postgres database.

    Args:
        dsn: libpq connection string.
        schema: Postgres schema for the tables (default ``kb``).
        min_size, max_size: Connection pool bounds.
    """

    dialect = "postgres"

    def __init__(self, dsn: str, schema: str = "kb", min_size: int = 1, max_size: int = 8):
        if not _SCHEMA.match(schema):
            raise ValueError(f"schema {schema!r} must be a lowercase SQL identifier")
        try:
            from psycopg.rows import dict_row
            from psycopg_pool import ConnectionPool
        except ImportError as exc:
            raise MissingExtraError("The Postgres catalog", "postgres", exc) from exc
        self.dsn = dsn
        self.schema = schema

        def configure(conn: Any) -> None:
            conn.row_factory = dict_row
            conn.execute(f"CREATE SCHEMA IF NOT EXISTS {schema}")
            conn.execute(f"SET search_path TO {schema}")
            conn.commit()

        self.pool = ConnectionPool(
            dsn, min_size=min_size, max_size=max_size, configure=configure, open=True
        )
        self.migrate()

    @contextmanager
    def _transaction(self, write: bool) -> Iterator[Tx]:
        with self.pool.connection() as conn:
            with conn.transaction():
                yield _PgTx(conn)

    def _lock_document(self, tx: Tx, document_id: str) -> None:
        tx.rows(
            "SELECT pg_advisory_xact_lock(hashtext(?)) AS locked", (f"{self.schema}:{document_id}",)
        )

    def _lock_migrations(self, tx: Tx) -> None:
        tx.rows("SELECT pg_advisory_xact_lock(hashtext(?)) AS locked", (f"{self.schema}:migrate",))

    def close(self) -> None:
        """Close the pool's connections."""
        self.pool.close()

    def drop_schema(self) -> None:
        """Drop the catalog's schema and everything in it (tests, teardown)."""
        with self.pool.connection() as conn:
            conn.execute(f"DROP SCHEMA IF EXISTS {self.schema} CASCADE")
            conn.commit()
