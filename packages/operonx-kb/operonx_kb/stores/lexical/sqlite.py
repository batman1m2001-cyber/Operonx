"""The SQLite FTS5 lexical index (stdlib ``sqlite3``).

One FTS5 table per lexical collection (``kbx_lex_<name>``): the analyzed
tokens in the one indexed column, the filter payload in ``UNINDEXED``
columns (lists and declared fields as JSON text), the entry's key as the
``rowid``. The ``ascii`` tokenizer with ``_`` as a token character keeps the
:class:`~operonx_kb.text.analyze.Analyzer`'s tokens as they are: it splits on
ASCII punctuation and spaces only, and every non-ASCII character is part of a
token. Ranking is FTS5's ``bm25()``.

A connection per call, WAL, ``BEGIN IMMEDIATE`` for writes: the same rules as
the SQLite catalog, so ops in worker threads can share it.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, List, Mapping, Optional, Sequence, Set, Tuple

from operonx_kb.stores.lexical.base import LexicalIndex, split_payload, table_name

__all__ = ["SqliteLexicalIndex"]

_COLUMNS = (
    "tokens, kb_collection UNINDEXED, kb_document UNINDEXED, kb_tags UNINDEXED, "
    "kb_acl UNINDEXED, kb_mime UNINDEXED, kb_created UNINDEXED, kb_fields UNINDEXED"
)


def _fts_query(tokens: Sequence[str]) -> str:
    """Any of the tokens, each a quoted FTS5 string (tokens hold no quotes)."""
    return " OR ".join('"' + t.replace('"', '""') + '"' for t in dict.fromkeys(tokens))


class SqliteLexicalIndex(LexicalIndex):
    """FTS5 tables in one SQLite file.

    Args:
        path: The database file; its folder is created.
    """

    dialect = "sqlite"

    def __init__(self, path: Any):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    @contextmanager
    def _conn(self, write: bool = False) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(str(self.path), timeout=30, isolation_level=None)
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            try:
                yield conn
            except BaseException:
                conn.execute("ROLLBACK")
                raise
            conn.execute("COMMIT")
        finally:
            conn.close()

    @staticmethod
    def _exists(conn: sqlite3.Connection, table: str) -> bool:
        row = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (table,)
        ).fetchone()
        return row is not None

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
                (int(key), " ".join(toks), fixed["kb_collection"], fixed["kb_document"],
                 json.dumps(fixed["kb_tags"] or [], ensure_ascii=False),
                 json.dumps(fixed["kb_acl"] or [], ensure_ascii=False), fixed["kb_mime"],
                 fixed["kb_created"], json.dumps(fields, ensure_ascii=False))
            )  # fmt: skip
        with self._conn(write=True) as conn:
            conn.execute(
                f"CREATE VIRTUAL TABLE IF NOT EXISTS {table} USING fts5({_COLUMNS}, "
                "tokenize = \"ascii tokenchars '_'\")"
            )
            conn.executemany(f"DELETE FROM {table} WHERE rowid = ?", [(r[0],) for r in rows])
            conn.executemany(
                f"INSERT INTO {table} (rowid, tokens, kb_collection, kb_document, kb_tags, kb_acl, "
                "kb_mime, kb_created, kb_fields) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                rows,
            )
        return len(rows)

    def delete(self, ids: Sequence[int], collection: Optional[str] = None) -> int:
        ids = [int(i) for i in ids]
        if not ids:
            return 0
        table = table_name(collection)
        with self._conn(write=True) as conn:
            if not self._exists(conn, table):
                return 0
            removed = 0
            for key in ids:
                removed += conn.execute(f"DELETE FROM {table} WHERE rowid = ?", (key,)).rowcount
        return removed

    def search(
        self,
        tokens: Sequence[str],
        top_k: int = 10,
        filter: Optional[Tuple[str, List[Any]]] = None,
        collection: Optional[str] = None,
    ) -> Tuple[List[int], List[float]]:
        if not tokens or top_k <= 0:
            return [], []
        where, params = "", []
        if filter is not None:
            if not (isinstance(filter, tuple) and len(filter) == 2 and isinstance(filter[0], str)):
                raise ValueError(
                    "a SQLite lexical filter is (where_sql, params), compiled from a KBFilter by "
                    "operonx_kb.retrieval.filters.native_filter"
                )
            where, params = f" AND ({filter[0]})", list(filter[1])
        table = table_name(collection)
        with self._conn() as conn:
            if not self._exists(conn, table):
                return [], []
            rows = conn.execute(
                f"SELECT rowid, bm25({table}) AS rank FROM {table} WHERE {table} MATCH ?{where} "
                "ORDER BY rank, rowid LIMIT ?",
                [_fts_query(tokens), *params, top_k],
            ).fetchall()
        return [r[0] for r in rows], [-float(r[1]) for r in rows]

    def ids(self, collection: Optional[str] = None) -> Set[int]:
        table = table_name(collection)
        with self._conn() as conn:
            if not self._exists(conn, table):
                return set()
            return {r[0] for r in conn.execute(f"SELECT rowid FROM {table}")}

    def drop(self, collection: Optional[str] = None) -> None:
        with self._conn(write=True) as conn:
            conn.execute(f"DROP TABLE IF EXISTS {table_name(collection)}")
