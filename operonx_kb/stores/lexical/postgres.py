"""The Postgres FTS lexical index (the ``postgres`` extra): psycopg 3 with a pool.

One table per lexical collection (``<schema>.kbx_lex_<name>``): a ``tsvector``
built directly from the analyzed tokens (a ``tsvector`` literal with
positions, so no text-search configuration re-tokenises or stems them) under a
GIN index, and the filter payload as typed columns (``text[]`` for tags and
ACL, ``jsonb`` for declared fields). Ranking is ``ts_rank_cd`` over an OR of
the query tokens: cover density, not BM25 (track5 §9.3), which is what plain
Postgres offers; the difference is measured in ``docs/bench/k2.md``.

Native filters are the pgvector dialect, ``{"where": sql, "params": {...}}``
with ``%(name)s`` placeholders, compiled by
:func:`operonx_kb.retrieval.filters.native_filter`.
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Mapping, Optional, Sequence, Set, Tuple

from operonx_kb.errors import MissingExtraError
from operonx_kb.stores.lexical.base import LexicalIndex, split_payload, table_name

__all__ = ["PostgresLexicalIndex"]

_SCHEMA = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")
_MAX_POSITION = 16383  # Postgres caps tsvector positions here


def _lexeme(token: str) -> str:
    return "'" + token.replace("\\", "\\\\").replace("'", "''") + "'"


def tsvector_literal(tokens: Sequence[str]) -> str:
    """``'tok':1 'tok2':2 …`` — the tokens as lexemes, with their positions."""
    return " ".join(f"{_lexeme(t)}:{min(i + 1, _MAX_POSITION)}" for i, t in enumerate(tokens) if t)


def tsquery_literal(tokens: Sequence[str]) -> str:
    """``'a' | 'b' …`` — any of the tokens."""
    return " | ".join(_lexeme(t) for t in dict.fromkeys(tokens) if t)


class PostgresLexicalIndex(LexicalIndex):
    """FTS tables in a Postgres schema.

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
            raise MissingExtraError("The Postgres lexical index", "postgres", exc) from exc
        self.schema = schema

        def configure(conn: Any) -> None:
            conn.row_factory = dict_row
            conn.execute(f"CREATE SCHEMA IF NOT EXISTS {schema}")
            conn.execute(f"SET search_path TO {schema}")
            conn.commit()

        self.pool = ConnectionPool(
            dsn, min_size=min_size, max_size=max_size, configure=configure, open=True
        )
        self._created: Set[str] = set()

    def _ensure(self, conn: Any, table: str) -> None:
        if table in self._created:
            return
        conn.execute(
            f"CREATE TABLE IF NOT EXISTS {table} (id bigint PRIMARY KEY, tsv tsvector NOT NULL, "
            "kb_collection text, kb_document text, kb_tags text[], kb_acl text[], kb_mime text, "
            "kb_created double precision, kb_fields jsonb)"
        )
        conn.execute(f"CREATE INDEX IF NOT EXISTS {table}_tsv ON {table} USING GIN (tsv)")
        self._created.add(table)

    def _exists(self, conn: Any, table: str) -> bool:
        row = conn.execute("SELECT to_regclass(%s) AS t", (f"{self.schema}.{table}",)).fetchone()
        return row["t"] is not None

    def upsert(
        self,
        ids: Sequence[int],
        tokens: Sequence[Sequence[str]],
        payloads: Sequence[Mapping[str, Any]],
        collection: Optional[str] = None,
    ) -> int:
        if not (len(ids) == len(tokens) == len(payloads)):
            raise ValueError(
                f"ids/tokens/payloads length mismatch: {len(ids)}, {len(tokens)}, {len(payloads)}"
            )
        if not ids:
            return 0
        table = table_name(collection)
        rows = []
        for key, toks, payload in zip(ids, tokens, payloads):
            fixed, fields = split_payload(payload)
            rows.append(
                (int(key), tsvector_literal(toks), fixed["kb_collection"], fixed["kb_document"],
                 list(fixed["kb_tags"] or []), list(fixed["kb_acl"] or []), fixed["kb_mime"],
                 fixed["kb_created"], json.dumps(fields, ensure_ascii=False))
            )  # fmt: skip
        with self.pool.connection() as conn:
            with conn.transaction():
                self._ensure(conn, table)
                with conn.cursor() as cur:
                    cur.executemany(
                        f"INSERT INTO {table} (id, tsv, kb_collection, kb_document, kb_tags, kb_acl, "
                        "kb_mime, kb_created, kb_fields) VALUES (%s, %s::tsvector, %s, %s, %s, %s, %s, "
                        "%s, %s::jsonb) ON CONFLICT (id) DO UPDATE SET tsv = excluded.tsv, "
                        "kb_collection = excluded.kb_collection, kb_document = excluded.kb_document, "
                        "kb_tags = excluded.kb_tags, kb_acl = excluded.kb_acl, kb_mime = excluded.kb_mime, "
                        "kb_created = excluded.kb_created, kb_fields = excluded.kb_fields",
                        rows,
                    )
        return len(rows)

    def delete(self, ids: Sequence[int], collection: Optional[str] = None) -> int:
        ids = [int(i) for i in ids]
        if not ids:
            return 0
        table = table_name(collection)
        with self.pool.connection() as conn:
            with conn.transaction():
                if not self._exists(conn, table):
                    return 0
                cur = conn.execute(f"DELETE FROM {table} WHERE id = ANY(%s)", (ids,))
                return max(cur.rowcount, 0)

    def search(
        self,
        tokens: Sequence[str],
        top_k: int = 10,
        filter: Optional[Dict[str, Any]] = None,
        collection: Optional[str] = None,
    ) -> Tuple[List[int], List[float]]:
        query = tsquery_literal(tokens)
        if not query or top_k <= 0:
            return [], []
        where, params = "", {}
        if filter is not None:
            if not (isinstance(filter, dict) and set(filter) == {"where", "params"}):
                raise ValueError(
                    'a Postgres lexical filter is {"where": sql, "params": {...}}, compiled from a '
                    "KBFilter by operonx_kb.retrieval.filters.native_filter"
                )
            where, params = f" AND ({filter['where']})", dict(filter["params"])
        table = table_name(collection)
        with self.pool.connection() as conn:
            if not self._exists(conn, table):
                return [], []
            rows = conn.execute(
                f"SELECT id, ts_rank_cd(tsv, q) AS score FROM {table}, CAST(%(_q)s AS tsquery) AS q "
                f"WHERE tsv @@ q{where} ORDER BY score DESC, id LIMIT %(_k)s",
                {**params, "_q": query, "_k": top_k},
            ).fetchall()
        return [int(r["id"]) for r in rows], [float(r["score"]) for r in rows]

    def ids(self, collection: Optional[str] = None) -> Set[int]:
        table = table_name(collection)
        with self.pool.connection() as conn:
            if not self._exists(conn, table):
                return set()
            return {int(r["id"]) for r in conn.execute(f"SELECT id FROM {table}").fetchall()}

    def drop(self, collection: Optional[str] = None) -> None:
        table = table_name(collection)
        with self.pool.connection() as conn:
            conn.execute(f"DROP TABLE IF EXISTS {table}")
            conn.commit()
        self._created.discard(table)

    def close(self) -> None:
        """Close the pool's connections."""
        self.pool.close()

    def drop_schema(self) -> None:
        """Drop the schema and everything in it (tests, teardown)."""
        with self.pool.connection() as conn:
            conn.execute(f"DROP SCHEMA IF EXISTS {self.schema} CASCADE")
            conn.commit()
        self._created.clear()
