"""The Postgres store — one database a team's services and studio share.

The same summaries and rollups schema the SQLite backends use
(:class:`~operonx.telemetry.runs.sql.SqlIndex`), with psycopg's ``%s``
placeholder, ``->>`` for metadata and ``DOUBLE PRECISION`` for times; each
run's full record compressed in a third table. Tables carry a prefix
(``operonx_`` by default) so they sit beside a project's own.

Large payloads (audio, arrays) are offloaded to ``media_dir`` as the local
consumer does. On a team server point it at a shared mount, or raise
``media_threshold`` to keep payloads in the record.

Uses psycopg 3 (the ``postgres`` extra) — the driver the pgvector and doc
stores already use; connections are opened per call and closed, the way
every :class:`SqlIndex` works.
"""

from __future__ import annotations

import json
import re
import shutil
import zlib
from pathlib import Path
from typing import Any, Dict, List, Optional

from operonx.telemetry.consumers.local import resolve_root

from .base import RunStore
from .model import (
    OpRollup,
    Page,
    RunFilter,
    RunRecord,
    RunSummary,
    meta_of_trace,
    rows_of_trace,
    summarize,
)
from .sql import SqlIndex

__all__ = ["PostgresRunStore"]

_PREFIX_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,40}$")


class PostgresRunStore(RunStore):
    """See the module docstring."""

    def __init__(
        self, dsn: str, prefix: str = "operonx_", media_dir: Any = "", media_threshold: int = 1024
    ):
        if not dsn:
            raise ValueError("the postgres run store needs a dsn (postgresql://user:pass@host/db)")
        if prefix and not _PREFIX_RE.match(prefix):
            raise ValueError(f"table prefix {prefix!r} is not a plain identifier")
        super().__init__(config={"prefix": prefix})
        try:
            import psycopg
        except ImportError as exc:  # pragma: no cover — exercised by the extra's absence
            raise ImportError(
                'the postgres run store needs: pip install "operonx[postgres]"'
            ) from exc
        self.dsn = dsn
        self.prefix = prefix
        self.media_dir = Path(media_dir) if media_dir else resolve_root("") / "pg-media"
        self.media_threshold = int(media_threshold)
        self.records = f"{prefix}records"

        def connect() -> Any:
            return psycopg.connect(dsn)

        self.index = SqlIndex(
            connect,
            ph="%s",
            json_get=lambda col, key: f"({col}::jsonb ->> '{key}')",
            prefix=prefix,
            real_type="DOUBLE PRECISION",
        )
        self.index.create()
        with self.index._tx() as cur:
            cur.execute(
                f"CREATE TABLE IF NOT EXISTS {self.records} "
                "(trace_id TEXT PRIMARY KEY, meta TEXT, nodes BYTEA)"
            )

    def put_trace(self, trace: Any) -> RunSummary:
        run_media = self.media_dir / str(trace.trace_id)
        media = run_media / "media"
        rows = rows_of_trace(trace, self, media_dir=media, threshold=self.media_threshold)
        if media.is_dir() and not any(media.iterdir()):
            shutil.rmtree(run_media, ignore_errors=True)
        meta = meta_of_trace(trace)
        summary, rollups = summarize(str(trace.trace_id), rows, meta, location=None)
        blob = zlib.compress(json.dumps(rows, default=str).encode("utf-8"))
        self.index.put(summary, rollups)
        with self.index._tx() as cur:
            cur.execute(f"DELETE FROM {self.records} WHERE trace_id = %s", (summary.trace_id,))
            cur.execute(
                f"INSERT INTO {self.records} (trace_id, meta, nodes) VALUES (%s, %s, %s)",
                (summary.trace_id, json.dumps(meta, default=str), blob),
            )
        return summary

    def list_runs(
        self,
        where: Optional[RunFilter] = None,
        order: str = "started_desc",
        limit: int = 50,
        cursor: Optional[str] = None,
    ) -> Page:
        return self.index.list(where, order, limit, cursor)

    def get_run(self, trace_id: str) -> Optional[RunRecord]:
        summary = self.index.get(trace_id)
        if summary is None:
            return None
        with self.index._tx() as cur:
            cur.execute(f"SELECT meta, nodes FROM {self.records} WHERE trace_id = %s", (trace_id,))
            row = cur.fetchone()
        if row is None:
            return None
        run_media = self.media_dir / trace_id
        return RunRecord(
            summary=summary,
            nodes=json.loads(zlib.decompress(bytes(row[1])).decode("utf-8")),
            meta=json.loads(row[0] or "{}"),
            media_root=str(run_media) if run_media.is_dir() else None,
        )

    def groups(
        self, where: Optional[RunFilter] = None, by=("origin", "name")
    ) -> List[Dict[str, Any]]:
        from .base import _check_by

        return self.index.groups(where, _check_by(by))

    def rollups(self, where: Optional[RunFilter] = None) -> List[OpRollup]:
        return self.index.rollups(where)

    def delete_runs(self, where: RunFilter) -> int:
        gone = self.index.delete(where)
        with self.index._tx() as cur:
            for row in gone:
                cur.execute(f"DELETE FROM {self.records} WHERE trace_id = %s", (row["trace_id"],))
        for row in gone:
            shutil.rmtree(self.media_dir / row["trace_id"], ignore_errors=True)
        return len(gone)
