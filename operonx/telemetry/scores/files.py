"""The files score store — JSONL files, an SQLite index beside them.

The default, and zero setup. The files are the truth, append-only, one
JSON object per line, each with its ``written_at``::

    <root>/                    (default: <runs root>/scores)
      experiments.jsonl        one line per write: running, then finished
      items/<experiment>.jsonl one line per case × repeat
      scores/YYYY-MM.jsonl     by the score's created_at
      .index.sqlite            what every read uses

A write appends its lines, then indexes them. :meth:`FilesScoreStore.refresh`
reads what was appended since the offset the index recorded per file —
lines another process wrote, or every line when the index was deleted —
and the index keeps, per id, the row written last, so reading a line twice
changes nothing. A half-written last line is left until it ends; a line
that is not a row is skipped. The judge cache lives in the index only:
losing it costs money, not truth.
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Sequence, Tuple

from operonx.telemetry.consumers.local import resolve_root

from .base import ONLINE_TTL_DAYS
from .model import Experiment, ExperimentItem, Score
from .sql import ScoreIndex
from .sqlite import SqliteScoreStore

__all__ = ["FilesScoreStore"]

_INDEX = ".index.sqlite"
_SAFE = re.compile(r"[^A-Za-z0-9._-]")
#: What each file holds, by its place under the root.
_KINDS = {"experiments": Experiment, "experiment_items": ExperimentItem, "scores": Score}


def _kind(rel: str) -> Optional[str]:
    if rel == "experiments.jsonl":
        return "experiments"
    if rel.startswith("items/") and rel.endswith(".jsonl"):
        return "experiment_items"
    if rel.startswith("scores/") and rel.endswith(".jsonl"):
        return "scores"
    return None


class FilesScoreStore(SqliteScoreStore):
    """See the module docstring. ``root`` unset → ``<runs root>/scores``
    (``$OPERONX_RUNS_DIR``, else ``<project>/.operonx/runs``); reads look
    for other writers' lines at most every ``refresh_every`` seconds."""

    def __init__(
        self,
        root: Any = "",
        refresh_every: float = 2.0,
        online_ttl_days: float = ONLINE_TTL_DAYS,
    ):
        self.root = Path(root) if root else resolve_root("") / "scores"
        if not self.root.is_absolute():
            self.root = resolve_root("") / self.root
        self.root.mkdir(parents=True, exist_ok=True)
        self.refresh_every = float(refresh_every)
        self.online_ttl_days = float(online_ttl_days)
        self.path = self.root / _INDEX
        self.index = ScoreIndex(self.path)
        self._append_lock = threading.Lock()
        self._last_refresh = 0.0
        self.refresh()

    # -- write: the line first, then the index ------------------------------

    def _file(self, table: str, obj: Any) -> str:
        if table == "experiments":
            return "experiments.jsonl"
        if table == "experiment_items":
            return f"items/{_SAFE.sub('_', obj.experiment_id)}.jsonl"
        return f"scores/{time.strftime('%Y-%m', time.gmtime(obj.created_at))}.jsonl"

    def _put(self, table: str, objs: Sequence[Any]) -> None:
        if not objs:
            return
        now = time.time()
        by_file: Dict[str, List[str]] = {}
        for obj in objs:
            line = json.dumps({**obj.to_dict(), "written_at": now}, ensure_ascii=False, default=str)
            by_file.setdefault(self._file(table, obj), []).append(line + "\n")
        with self._append_lock:
            known = self.index.offsets()
            for rel, lines in by_file.items():
                path = self.root / rel
                path.parent.mkdir(parents=True, exist_ok=True)
                with path.open("ab") as fh:  # one write: whole lines, never interleaved
                    before = fh.tell()
                    fh.write("".join(lines).encode("utf-8"))
                    after = fh.tell()
                if known.get(rel, 0) == before:  # nobody else wrote since: read up to here
                    self.index.set_offset(rel, after)
        self.index.upsert(table, self._rows(table, objs, now))

    # -- read: what other writers appended -------------------------------------

    def _files(self) -> Iterator[Tuple[str, str]]:
        for current, dirs, files in os.walk(self.root):
            dirs[:] = sorted(d for d in dirs if not d.startswith("."))
            for name in sorted(files):
                rel = (Path(current) / name).relative_to(self.root).as_posix()
                kind = _kind(rel)
                if kind is not None:
                    yield rel, kind

    def refresh(self) -> int:
        """Index lines appended since the last read; return how many rows."""
        known = self.index.offsets()
        added = 0
        for rel, kind in self._files():
            path = self.root / rel
            start = known.get(rel, 0)
            try:
                size = path.stat().st_size
            except OSError:
                continue
            if size < start:  # rewritten from scratch: read it all
                start = 0
            if size == start:
                continue
            with path.open("rb") as fh:
                fh.seek(start)
                data = fh.read(size - start)
            end = data.rfind(b"\n")
            if end < 0:
                continue  # a line still being written
            rows = self._parse(kind, data[: end + 1])
            self.index.upsert(kind, rows)
            self.index.set_offset(rel, start + end + 1)
            added += len(rows)
        self._last_refresh = time.monotonic()
        return added

    def _parse(self, kind: str, data: bytes) -> List[List[Any]]:
        cls = _KINDS[kind]
        rows = []
        for raw in data.splitlines():
            try:
                d = json.loads(raw)
                obj = cls.from_dict(d)
                written = float(d["written_at"])
            except Exception:  # noqa: BLE001 — one bad line is not the store failing
                continue
            rows.extend(self._rows(kind, [obj], written))
        return rows

    def _before_read(self) -> None:
        if time.monotonic() - self._last_refresh >= self.refresh_every:
            self.refresh()
