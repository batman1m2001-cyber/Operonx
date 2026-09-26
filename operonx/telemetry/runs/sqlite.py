"""The SQLite store — everything in one file.

Summaries and rollups in the shared SQL schema; each run's full record
(``meta`` and its rows) compressed in a third table. Large payloads
(audio, arrays) are offloaded to a media directory beside the file, as
the local consumer does, so the database stays small and a record still
points at its media.
"""

from __future__ import annotations

import json
import shutil
import sqlite3
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

__all__ = ["SqliteRunStore"]


class SqliteRunStore(RunStore):
    """See the module docstring. ``path`` defaults to ``runs.sqlite``
    under the runs root (``<project>/.operonx/runs``)."""

    def __init__(self, path: Any = "", media_threshold: int = 1024):
        super().__init__(config={"path": str(path)})
        self.path = Path(path) if path else resolve_root("") / "runs.sqlite"
        if not self.path.is_absolute():
            self.path = resolve_root("") / self.path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.media_dir = self.path.with_suffix(".media")
        self.media_threshold = int(media_threshold)

        def connect() -> sqlite3.Connection:
            conn = sqlite3.connect(str(self.path), timeout=30)
            conn.execute("PRAGMA journal_mode=WAL")
            return conn

        self._connect = connect
        self.index = SqlIndex(connect)
        self.index.create()
        with self.index._tx() as cur:
            cur.execute(
                "CREATE TABLE IF NOT EXISTS records (trace_id TEXT PRIMARY KEY, meta TEXT, nodes BLOB)"
            )

    def put_trace(self, trace: Any) -> RunSummary:
        # refs are written as "media/<sha>.<ext>", relative to the run's own
        # folder — the same shape a run directory has
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
            cur.execute("DELETE FROM records WHERE trace_id = ?", (summary.trace_id,))
            cur.execute(
                "INSERT INTO records (trace_id, meta, nodes) VALUES (?, ?, ?)",
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
            cur.execute("SELECT meta, nodes FROM records WHERE trace_id = ?", (trace_id,))
            row = cur.fetchone()
        if row is None:
            return None
        nodes = json.loads(zlib.decompress(row[1]).decode("utf-8"))
        run_media = self.media_dir / trace_id
        return RunRecord(
            summary=summary,
            nodes=nodes,
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
                cur.execute("DELETE FROM records WHERE trace_id = ?", (row["trace_id"],))
        for row in gone:
            shutil.rmtree(self.media_dir / row["trace_id"], ignore_errors=True)
        return len(gone)
