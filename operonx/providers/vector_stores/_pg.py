"""Shared Postgres plumbing for the pgvector store and the Postgres doc store.

Both talk to the same database — often literally the same one, which is
the whole point of the two-store model (plan §5.5): the vector table and
the document table live side by side, so there is no dual-write risk.
Sharing the pool means they also share connections.
"""

import re
from typing import Any, Dict, Optional, Tuple

__all__ = ["get_pool", "close_pools", "split_filter", "IDENT_RE"]


# Postgres identifiers we are willing to interpolate. Everything that
# reaches SQL as an *identifier* (column, table) is checked against this;
# everything else goes through bound parameters.
IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_$]*$")

# Pools are keyed by DSN so the pgvector store and the doc store pointing
# at the same database share one pool.
_pools: Dict[str, Any] = {}


def get_pool(dsn: str, *, min_size: int = 1, max_size: int = 10):
    """Return the process-wide async connection pool for ``dsn``.

    Pools are cached per DSN. Opening a fresh pool per op would pay
    connection setup on every call — the same reason
    :class:`~operonx.providers.triton.TritonClient` caches its channel.

    Raises:
        ImportError: With an install hint when psycopg is absent.
    """
    if dsn in _pools:
        return _pools[dsn]

    try:
        from psycopg_pool import AsyncConnectionPool
    except ImportError as e:  # pragma: no cover - exercised via factory
        raise ImportError(
            "Postgres support requires additional packages.\n"
            "  Install with: pip install operonx[postgres]\n"
            f"  Original error: {e}"
        ) from e

    # open=False keeps construction synchronous; psycopg opens lazily on
    # first use from within the running loop.
    pool = AsyncConnectionPool(dsn, min_size=min_size, max_size=max_size, open=False)
    _pools[dsn] = pool
    return pool


async def close_pools() -> None:
    """Close every cached pool. Intended for tests and clean shutdown."""
    for pool in list(_pools.values()):
        try:
            await pool.close()
        except Exception:  # noqa: BLE001 - shutdown is best-effort
            pass
    _pools.clear()


def split_filter(
    filter: Optional[Any],
) -> Tuple[Optional[str], Dict[str, Any]]:
    """Normalise a pgvector/Postgres filter into ``(where_sql, params)``.

    Two accepted shapes (plan §5.4):

    * **Explicit SQL** — ``{"where": "tenant = %(tenant)s", "params": {...}}``.
      Full SQL power; params are always bound, never interpolated.
    * **Equality sugar** — ``{"tenant": "acme"}`` becomes
      ``tenant = %(tenant)s``. Disambiguated by the absence of a
      ``"where"`` key.

    Anything else raises. A filter must never silently degrade to "no
    filter" — in a multi-tenant system that is a data leak rather than a
    warning.

    Returns:
        ``(where_sql_or_None, params)``. ``where_sql`` excludes the
        ``WHERE`` keyword so callers can compose it.
    """
    if filter is None:
        return None, {}

    if isinstance(filter, str):
        raise ValueError(
            "Postgres filters must be a dict, not a string. Use "
            '{"where": "tenant = %(tenant)s", "params": {"tenant": "acme"}} '
            'for raw SQL, or {"tenant": "acme"} for equality. '
            "(Expression strings are Milvus's dialect, not Postgres's.)"
        )

    if not isinstance(filter, dict):
        raise ValueError(f"Postgres filters must be a dict, got {type(filter).__name__}.")

    if "where" in filter:
        where = filter["where"]
        if not isinstance(where, str):
            raise ValueError('filter["where"] must be a SQL string.')
        params = filter.get("params") or {}
        if not isinstance(params, dict):
            raise ValueError('filter["params"] must be a dict of bound parameters.')
        unexpected = set(filter) - {"where", "params"}
        if unexpected:
            raise ValueError(
                f"Unexpected keys alongside 'where': {sorted(unexpected)}. "
                "Use either {'where': ..., 'params': ...} or a flat equality dict."
            )
        return where, dict(params)

    # Equality sugar. Keys are column identifiers, so they are validated
    # rather than bound — values are always bound.
    clauses = []
    params = {}
    for col, value in filter.items():
        if not IDENT_RE.match(col):
            raise ValueError(
                f"Invalid column name in filter: {col!r}. Use the explicit "
                "{'where': ..., 'params': ...} form for anything beyond a "
                "plain column equality."
            )
        placeholder = f"f_{col}"
        clauses.append(f"{col} = %({placeholder})s")
        params[placeholder] = value
    return " AND ".join(clauses), params
