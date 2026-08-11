"""pgvector store — Postgres with the ``vector`` extension.

The cleanest expression of the two-store model (plan §5.5): the vector
table and the document table live in the *same database*, so hydration is
a query against the same connection pool and there is no dual-write risk
at all.

Expected schema::

    CREATE TABLE docs_vec (
        id        bigint PRIMARY KEY,
        embedding vector(1024),
        tenant    text,          -- any filterable metadata columns
        doc_type  text
    );
    CREATE INDEX ON docs_vec USING hnsw (embedding vector_ip_ops);

Note the index opclass must match the configured metric, or Postgres will
silently fall back to a sequential scan.
"""

from typing import Any, Dict, List, Optional, Sequence, Tuple, Union

from operonx.providers.vector_stores._pg import IDENT_RE, get_pool, split_filter
from operonx.providers.vector_stores.base import BaseVectorStore
from operonx.providers.vector_stores.config import VectorStoreConfig, VectorStoreMetric

__all__ = ["PgVectorStore"]


# metric → (operator, score expression template)
#
# All three pgvector operators return values where SMALLER IS CLOSER, so
# ORDER BY <op> ASC is always the index-friendly ordering. The score we
# hand back is converted per metric to match what FAISS returns for the
# same metric, so the two backends stay swappable:
#   IP      pgvector's <#> is the NEGATIVE inner product → negate it back
#   COSINE  <=> is cosine DISTANCE → 1 - distance = similarity
#   L2      <-> is euclidean distance → returned as-is
_METRIC_SQL = {
    VectorStoreMetric.IP: ("<#>", "-({expr})"),
    VectorStoreMetric.COSINE: ("<=>", "1 - ({expr})"),
    VectorStoreMetric.L2: ("<->", "({expr})"),
}


class PgVectorStore(BaseVectorStore):
    """Vector similarity search backed by Postgres + pgvector."""

    __slots__ = ("config", "_pool", "_table", "_id_col", "_vec_col", "_meta_cols")

    bound = "io"

    def __init__(self, config: VectorStoreConfig):
        if not config.dsn:
            raise ValueError("PgVectorStore requires dsn= in the resource config.")
        if not config.table:
            raise ValueError("PgVectorStore requires table= in the resource config.")

        self.config = config
        self._pool = get_pool(config.dsn)
        self._table = self._ident(config.table, "table")
        self._id_col = self._ident(config.id_column or "id", "id_column")
        self._vec_col = self._ident(config.vector_column or "embedding", "vector_column")
        self._meta_cols = [
            self._ident(c, "metadata_columns") for c in (config.metadata_columns or [])
        ]

        if config.metric not in _METRIC_SQL:
            raise ValueError(f"PgVectorStore does not support metric {config.metric}.")

    @staticmethod
    def _ident(name: str, what: str) -> str:
        """Validate a SQL identifier.

        Table and column names cannot be bound parameters, so they are
        checked against a strict pattern instead of being interpolated
        blind.
        """
        if not IDENT_RE.match(name):
            raise ValueError(f"Invalid {what} identifier: {name!r}")
        return name

    def _relation(self, collection: Optional[str]) -> str:
        """Resolve the table to query. ``collection`` overrides the default."""
        return self._ident(collection, "collection") if collection else self._table

    # ── BaseVectorStore ───────────────────────────────────────────────

    async def search(
        self,
        query_vector: Sequence[float],
        top_k: int = 10,
        filter: Optional[Union[Dict[str, Any], str]] = None,
        collection: Optional[str] = None,
    ) -> Tuple[List[Any], List[float], List[Dict[str, Any]]]:
        """Nearest-neighbour search, optionally filtered.

        The ORDER BY uses the bare pgvector operator so the HNSW/IVFFlat
        index is used. Ordering by the *converted* score instead (e.g.
        ``ORDER BY -(embedding <#> q) DESC``) is equivalent arithmetic but
        opaque to the planner, and silently degrades to a sequential scan.
        """
        op, score_tmpl = _METRIC_SQL[self.config.metric]
        relation = self._relation(collection)
        where_sql, params = split_filter(filter)

        distance = f"{self._vec_col} {op} %(q)s::vector"
        score_expr = score_tmpl.format(expr=distance)

        select_cols = [f"{self._id_col} AS _id", f"{score_expr} AS _score"]
        select_cols += [f"{c} AS {c}" for c in self._meta_cols]

        sql = f"SELECT {', '.join(select_cols)} FROM {relation}"  # noqa: S608 - identifiers validated
        if where_sql:
            sql += f" WHERE {where_sql}"
        sql += f" ORDER BY {distance} LIMIT %(k)s"

        params["q"] = self._to_pg_vector(query_vector)
        params["k"] = top_k

        rows = await self._fetch_all(sql, params)

        ids: List[Any] = []
        scores: List[float] = []
        metadata: List[Dict[str, Any]] = []
        for row in rows:
            ids.append(row["_id"])
            scores.append(float(row["_score"]))
            metadata.append({c: row[c] for c in self._meta_cols})
        return ids, scores, metadata

    async def upsert(
        self,
        ids: Sequence[Any],
        vectors: Sequence[Sequence[float]],
        metadata: Optional[Sequence[Dict[str, Any]]] = None,
        collection: Optional[str] = None,
    ) -> None:
        """Insert or replace rows by primary key.

        Only columns declared in ``metadata_columns`` are written — an
        unlisted key in ``metadata`` is a config error, not something to
        silently drop.
        """
        ids = list(ids)
        vectors = list(vectors)
        if len(ids) != len(vectors):
            raise ValueError(f"ids/vectors length mismatch: {len(ids)} vs {len(vectors)}")
        if metadata is not None and len(metadata) != len(ids):
            raise ValueError(f"ids/metadata length mismatch: {len(ids)} vs {len(metadata)}")

        relation = self._relation(collection)
        cols = [self._id_col, self._vec_col] + self._meta_cols
        placeholders = ", ".join(f"%({c})s" for c in cols)
        updates = ", ".join(f"{c} = EXCLUDED.{c}" for c in cols[1:])

        sql = (  # noqa: S608 - identifiers validated
            f"INSERT INTO {relation} ({', '.join(cols)}) VALUES ({placeholders}) "
            f"ON CONFLICT ({self._id_col}) DO UPDATE SET {updates}"
        )

        batch = []
        for i, (row_id, vec) in enumerate(zip(ids, vectors)):
            meta = (metadata[i] if metadata else None) or {}
            unknown = set(meta) - set(self._meta_cols)
            if unknown:
                raise ValueError(
                    f"metadata keys {sorted(unknown)} are not in metadata_columns="
                    f"{self._meta_cols}. Add the columns to the resource config "
                    "(and the table) or drop them from the payload."
                )
            params = {self._id_col: row_id, self._vec_col: self._to_pg_vector(vec)}
            for c in self._meta_cols:
                params[c] = meta.get(c)
            batch.append(params)

        await self._execute_many(sql, batch)

    # ── plumbing ──────────────────────────────────────────────────────

    @staticmethod
    def _to_pg_vector(vector: Sequence[float]) -> str:
        """Render a vector in pgvector's text input format."""
        return "[" + ",".join(str(float(v)) for v in vector) + "]"

    async def _fetch_all(self, sql: str, params: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Run a query and return dict rows."""
        from psycopg.rows import dict_row

        await self._pool.open()
        async with self._pool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cur:
                await cur.execute(sql, params)
                return await cur.fetchall()

    async def _execute_many(self, sql: str, batch: List[Dict[str, Any]]) -> None:
        """Run a statement once per parameter set, in one transaction."""
        await self._pool.open()
        async with self._pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.executemany(sql, batch)
