"""Postgres store of record.

Pairs with :class:`~operonx.providers.vector_stores.pgvector.PgVectorStore`
— point both at the same DSN and the connection pool is shared, giving you
one database holding the vector table and the document table side by side.
That is the two-store model with zero dual-write risk (plan §5.5).
"""

from typing import Any, Dict, List, Optional, Sequence

from operonx.providers.doc_stores.base import BaseDocStore
from operonx.providers.doc_stores.config import DocStoreConfig
from operonx.providers.vector_stores._pg import IDENT_RE, get_pool

__all__ = ["PostgresDocStore"]


class PostgresDocStore(BaseDocStore):
    """Fetch rows by primary key from Postgres.

    Scope is fetch-by-ids plus an optional column projection — see
    :class:`~operonx.providers.doc_stores.base.BaseDocStore` for the
    boundary and why it is drawn there.
    """

    __slots__ = ("config", "_pool")

    bound = "io"

    def __init__(self, config: DocStoreConfig):
        if not config.dsn:
            raise ValueError("PostgresDocStore requires dsn= in the resource config.")
        self.config = config
        self._pool = get_pool(config.dsn)

    @staticmethod
    def _ident(name: str, what: str) -> str:
        """Validate a SQL identifier — these cannot be bound parameters."""
        if not IDENT_RE.match(name):
            raise ValueError(f"Invalid {what} identifier: {name!r}")
        return name

    async def _fetch(
        self,
        ids: Sequence[Any],
        collection: Optional[str] = None,
        fields: Optional[Sequence[str]] = None,
        id_field: str = "id",
    ) -> List[Dict[str, Any]]:
        """``SELECT … WHERE <id_field> = ANY(%(ids)s)``.

        ``ANY`` takes the id list as a single bound array parameter, so
        the statement text is identical regardless of how many ids are
        requested — one plan cache entry instead of one per batch size.
        """
        from psycopg.rows import dict_row

        # Check for "no table configured at all" before validating the
        # identifier, so the error names the actual problem rather than
        # complaining that "" is not a valid identifier.
        table_name = collection or self.config.collection
        if not table_name:
            raise ValueError(
                "PostgresDocStore needs a table: pass collection= on the op or set "
                "collection: in the resource config."
            )
        table = self._ident(table_name, "collection")
        id_col = self._ident(id_field, "id_field")

        if fields:
            cols = [self._ident(f, "fields") for f in fields]
            # The id column must come back even when the caller didn't ask
            # for it — the base class matches rows to ids by that value.
            if id_col not in cols:
                cols.append(id_col)
            projection = ", ".join(cols)
        else:
            projection = "*"

        sql = f"SELECT {projection} FROM {table} WHERE {id_col} = ANY(%(ids)s)"  # noqa: S608 - identifiers validated

        await self._pool.open()
        async with self._pool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cur:
                await cur.execute(sql, {"ids": list(ids)})
                return await cur.fetchall()
