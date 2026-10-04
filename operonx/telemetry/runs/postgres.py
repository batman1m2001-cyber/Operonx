"""The Postgres store — one database a team's services and studio share.

The same summaries and rollups schema the SQLite backends use
(:class:`~operonx.telemetry.runs.sql.SqlIndex`), with psycopg's ``%s``
placeholder, ``->>`` for metadata and ``DOUBLE PRECISION`` for times; each
run's full record compressed in a third table, and a running run's
executions in ``live`` as they land (see :class:`~.sql.SqlRunStore`).
Tables carry a prefix
(``operonx_`` by default) so they sit beside a project's own.

Large payloads (audio, arrays) are offloaded to ``media_dir`` as the local
consumer does. On a team server point it at a shared mount, or raise
``media_threshold`` to keep payloads in the record.

Uses psycopg 3 (the ``postgres`` extra) — the driver the pgvector and doc
stores already use; connections are opened per call and closed, the way
every :class:`SqlIndex` works.
"""

from __future__ import annotations

import re
from typing import Any

from operonx.telemetry.consumers.local import resolve_root

from .sql import SqlRunStore

__all__ = ["PostgresRunStore"]

_PREFIX_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,40}$")


class PostgresRunStore(SqlRunStore):
    """See the module docstring."""

    def __init__(
        self,
        dsn: str,
        prefix: str = "operonx_",
        media_dir: Any = "",
        media_threshold: int = 1024,
        live: bool = True,
    ):
        if not dsn:
            raise ValueError("the postgres run store needs a dsn (postgresql://user:pass@host/db)")
        if prefix and not _PREFIX_RE.match(prefix):
            raise ValueError(f"table prefix {prefix!r} is not a plain identifier")
        try:
            import psycopg
        except ImportError as exc:  # pragma: no cover — exercised by the extra's absence
            raise ImportError(
                'the postgres run store needs: pip install "operonx[postgres]"'
            ) from exc
        self.dsn = dsn
        self.prefix = prefix

        def connect() -> Any:
            return psycopg.connect(dsn)

        super().__init__(
            connect,
            media_dir=media_dir or resolve_root("") / "pg-media",
            media_threshold=media_threshold,
            ph="%s",
            json_get=lambda col, key: f"({col}::jsonb ->> '{key}')",
            prefix=prefix,
            real_type="DOUBLE PRECISION",
            blob_type="BYTEA",
            live=live,
            config={"prefix": prefix},
        )
