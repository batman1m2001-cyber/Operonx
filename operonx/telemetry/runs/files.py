"""The files store — run directories on disk, summaries in a SQLite file beside them.

The default, and zero setup: each run is the directory
:class:`~operonx.telemetry.consumers.local.LocalConsumer` writes
(``meta.json``, ``nodes.jsonl``, ``view.txt``, ``media/``), filed by
origin under the root; ``<root>/.index.sqlite`` holds one summary row
per run and one rollup row per op per run, so listing a month of calls
never opens a trace.

Runs written by a plain ``LocalConsumer`` (or a project's subclass of
it) — without going through this store — are picked up by
:meth:`FilesRunStore.refresh`, which indexes every run directory the
index has not seen, and forgets rows whose directory is gone. Old flat
roots (``<root>/<trace_id>/``) index the same way.
"""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import time
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional

from operonx.telemetry.consumers.local import LocalConsumer, resolve_root

from .base import RunStore
from .model import OpRollup, Page, RunFilter, RunRecord, RunSummary, summarize
from .sql import SqlIndex

__all__ = ["FilesRunStore", "read_run_dir"]

_INDEX = ".index.sqlite"


def _read_rows(path: Path) -> Iterator[Dict[str, Any]]:
    try:
        fh = path.open(encoding="utf-8")
    except OSError:
        return
    with fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue


def read_run_dir(run_dir: Path) -> Dict[str, Any]:
    """``{"meta": …, "nodes": […]}`` for one run directory."""
    try:
        meta = json.loads((run_dir / "meta.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        meta = {}
    return {"meta": meta, "nodes": list(_read_rows(run_dir / "nodes.jsonl"))}


class FilesRunStore(RunStore):
    """See the module docstring. ``root`` resolves like the local
    consumer's (unset → ``$OPERONX_RUNS_DIR`` → ``<project>/.operonx/runs``)."""

    def __init__(
        self,
        root: Any = "",
        layout: str = "origin",
        refresh_every: float = 2.0,
        consumer: Optional[LocalConsumer] = None,
    ):
        super().__init__(config={"root": str(root), "layout": layout})
        self.root = resolve_root(root)
        self.layout = layout
        self.refresh_every = float(refresh_every)
        self._writer = consumer or LocalConsumer(config={"root": str(self.root), "layout": layout})
        self._last_refresh = 0.0
        self.root.mkdir(parents=True, exist_ok=True)
        path = self.root / _INDEX

        def connect() -> sqlite3.Connection:
            conn = sqlite3.connect(str(path), timeout=30)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            return conn

        self.index = SqlIndex(connect)
        self.index.create()

    # -- write -------------------------------------------------------------

    def put_trace(self, trace: Any) -> RunSummary:
        run_dir = Path(self._writer.consume(trace))
        return self._index_dir(run_dir)

    def _index_dir(self, run_dir: Path) -> RunSummary:
        got = read_run_dir(run_dir)
        meta = got["meta"]
        trace_id = str(meta.get("trace_id") or run_dir.name)
        location = run_dir.relative_to(self.root).as_posix()
        summary, rollups = summarize(trace_id, got["nodes"], meta, location=location)
        if not summary.started_at:
            try:
                summary.started_at = (run_dir / "nodes.jsonl").stat().st_mtime
            except OSError:
                pass
        self.index.put(summary, rollups)
        return summary

    # -- discovering runs other writers left ------------------------------

    def _run_dirs(self) -> Iterator[Path]:
        """Every run directory under the root: one holding ``nodes.jsonl``.
        A run's own subdirectories (``media/``) are never walked."""
        for current, dirs, files in os.walk(self.root, followlinks=False):
            here = Path(current)
            if "nodes.jsonl" in files and not here.name.endswith(".tmp"):
                dirs[:] = []
                yield here
                continue
            dirs[:] = [d for d in dirs if not d.startswith(".") and not d.endswith(".tmp")]

    def refresh(self) -> int:
        known = self.index.locations()
        seen = set()
        added = 0
        for run_dir in self._run_dirs():
            loc = run_dir.relative_to(self.root).as_posix()
            seen.add(loc)
            if loc in known:
                continue
            try:
                self._index_dir(run_dir)
                added += 1
            except Exception:  # noqa: BLE001 — one bad directory is not the store failing
                continue
        gone = [tid for loc, tid in known.items() if loc not in seen]
        if gone:
            self.index.delete(RunFilter(trace_ids=gone))
        self._last_refresh = time.monotonic()
        return added

    def _maybe_refresh(self) -> None:
        if time.monotonic() - self._last_refresh >= self.refresh_every:
            self.refresh()

    # -- read --------------------------------------------------------------

    def list_runs(
        self,
        where: Optional[RunFilter] = None,
        order: str = "started_desc",
        limit: int = 50,
        cursor: Optional[str] = None,
    ) -> Page:
        self._maybe_refresh()
        return self.index.list(where, order, limit, cursor)

    def get_run(self, trace_id: str) -> Optional[RunRecord]:
        summary = self.index.get(trace_id)
        if summary is None:
            self.refresh()
            summary = self.index.get(trace_id)
        if summary is None or not summary.location:
            return None
        run_dir = self.root / summary.location
        if not (run_dir / "nodes.jsonl").is_file():
            return None
        got = read_run_dir(run_dir)
        return RunRecord(
            summary=summary, nodes=got["nodes"], meta=got["meta"], media_root=str(run_dir)
        )

    def rollups(self, where: Optional[RunFilter] = None) -> List[OpRollup]:
        self._maybe_refresh()
        return self.index.rollups(where)

    def run_dir(self, trace_id: str) -> Optional[Path]:
        """The directory holding a run — for readers that want its files."""
        summary = self.index.get(trace_id)
        if summary is None:
            self.refresh()
            summary = self.index.get(trace_id)
        return (self.root / summary.location) if summary and summary.location else None

    # -- housekeeping ------------------------------------------------------

    def delete_runs(self, where: RunFilter) -> int:
        gone = self.index.delete(where)
        for row in gone:
            loc = row.get("location")
            if not loc:
                continue
            path = (self.root / loc).resolve()
            if self.root.resolve() in path.parents and path.is_dir():
                shutil.rmtree(path, ignore_errors=True)
                _prune_empty(path.parent, self.root)
        return len(gone)


def _prune_empty(path: Path, stop: Path) -> None:
    """Remove now-empty parents (a day folder whose last run went)."""
    stop = stop.resolve()
    path = path.resolve()
    while path != stop and stop in path.parents:
        try:
            path.rmdir()
        except OSError:
            return
        path = path.parent
