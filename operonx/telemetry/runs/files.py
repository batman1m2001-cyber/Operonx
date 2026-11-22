"""The files store — run directories on disk, summaries in a SQLite file beside them.

The default, and zero setup: each run is the directory
:class:`~operonx.telemetry.consumers.local.LocalConsumer` writes
(``meta.json``, ``nodes.jsonl``, ``view.txt``, ``media/``), filed by
origin under the root; ``<root>/.index.sqlite`` holds one summary row
per run and one rollup row per op per run, so listing a month of calls
never opens a trace.

Runs written by a plain ``LocalConsumer`` (or a project's subclass of
it) — without going through this store — reach the index two ways:

* **the change journal** (``<root>/.changes``): every writer appends the
  run directory it wrote; a reader indexes the lines added since it last
  looked. This is what keeps the store current, and it costs what
  *changed*, not what is *kept*.
* **a walk of the root** (:meth:`FilesRunStore.refresh`): finds what no
  journal line told of — runs left by an older writer, directories
  removed by hand — and forgets rows whose directory is gone. On a small
  root it runs on the reader's thread, as it always did. On a big one it
  never does: it runs in the background, no more than 5% of the time, and
  skips every directory that has not changed since the walk before. (A
  root of 780,000 runs was walked whole on every read: minutes each, with
  every reader waiting.)

Old flat roots (``<root>/<trace_id>/``) index the same way.
"""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import threading
import time
from collections import Counter
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional

from operonx.telemetry.consumers.local import CHANGES, LocalConsumer, resolve_root

from .base import RunStore
from .model import STOPPED, OpRollup, Page, RunFilter, RunRecord, RunSummary, summarize
from .sql import SqlIndex

__all__ = ["FilesRunStore", "read_run_dir"]

_INDEX = ".index.sqlite"
#: Past this many indexed runs — or once a walk has taken this long — a
#: walk is never run on a reader's thread.
_BIG_RUNS = 5000
_SLOW_WALK = 0.2
#: A fully read journal past this size is emptied.
_ROTATE = 4 << 20


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
        self._refreshing = threading.Lock()
        self._journal_at: Optional[int] = None  # how far into .changes the index has been brought
        self._walk_cost: Optional[float] = None  # seconds the last walk took; None: not known yet
        self._walk_gate = threading.Lock()  # one background walk at a time
        self._next_walk = 0.0
        # each indexed run's meta.json mtime, as last seen: a run written
        # again (a journalled run resumed under its own id) is read again
        self._seen_at: Dict[str, float] = {}
        self.root.mkdir(parents=True, exist_ok=True)
        path = self.root / _INDEX

        def connect() -> sqlite3.Connection:
            conn = sqlite3.connect(str(path), timeout=30)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            return conn

        self.index = SqlIndex(connect)
        self.index.create()
        with self.index._tx() as cur:
            cur.execute("CREATE TABLE IF NOT EXISTS files_meta (key TEXT PRIMARY KEY, value TEXT)")

    def _meta(self, key: str) -> Optional[str]:
        with self.index._tx() as cur:
            cur.execute("SELECT value FROM files_meta WHERE key = ?", (key,))
            row = cur.fetchone()
            return row[0] if row else None

    def _set_meta(self, key: str, value: Any) -> None:
        with self.index._tx() as cur:
            cur.execute(
                "INSERT OR REPLACE INTO files_meta (key, value) VALUES (?, ?)", (key, str(value))
            )

    # -- write -------------------------------------------------------------

    def put_trace(self, trace: Any) -> RunSummary:
        run_dir = Path(self._writer.consume(trace))
        return self._index_dir(run_dir)

    def _index_dir(self, run_dir: Path) -> RunSummary:
        summary, rollups = self._summarize_dir(run_dir)
        self.index.put(summary, rollups)
        return summary

    def _summarize_dir(self, run_dir: Path):
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
        return summary, rollups

    # -- discovering runs other writers left ------------------------------

    def _apply_journal(self) -> int:
        """Index the run directories the journal names since it was last
        read. Costs the lines added, whatever the root holds."""
        path = self.root / CHANGES
        try:
            size = path.stat().st_size
        except OSError:
            return 0
        if self._journal_at is None:
            try:
                self._journal_at = int(self._meta("journal_at") or 0)
            except ValueError:
                self._journal_at = 0
        at = self._journal_at if self._journal_at <= size else 0  # emptied since: from the top
        if size == at:
            return 0
        try:
            with path.open("rb") as fh:
                fh.seek(at)
                data = fh.read(size - at)
        except OSError:
            return 0
        cut = data.rfind(b"\n") + 1  # a line still being written waits for its end
        batch: list = []
        removed: list = []
        for loc in dict.fromkeys(data[:cut].decode("utf-8", "replace").split("\n")):
            if not loc or loc.startswith("/") or ".." in loc.split("/"):
                continue
            run_dir = self.root / loc
            if not (run_dir / "nodes.jsonl").is_file():
                # removed (a job's keep_runs, a hand): its row goes with it
                self._seen_at.pop(loc, None)
                removed.append(loc.rsplit("/", 1)[-1])
                continue
            try:
                written = (run_dir / "meta.json").stat().st_mtime
            except OSError:
                written = 0.0
            if self._seen_at.get(loc) == written:
                continue  # this store wrote it, and indexed it then
            self._seen_at[loc] = written
            try:
                batch.append(self._summarize_dir(run_dir))
            except Exception:  # noqa: BLE001 — one bad directory is not the store failing
                continue
        self.index.put_many(batch)
        for i in range(0, len(removed), 500):
            self.index.delete(RunFilter(trace_ids=removed[i : i + 500]))
        at += cut
        if at == size and size > _ROTATE:
            try:
                os.truncate(path, 0)
                at = 0
            except OSError:
                pass
        self._journal_at = at
        self._set_meta("journal_at", at)
        return len(batch)

    def _open_locations(self) -> set:
        """Runs that may still be written to: running, or stopped on purpose
        (a stopped run is resumed under its own id)."""
        out = set()
        for status in STOPPED + ("running",):
            page = self.index.list(RunFilter(status=status), "started_desc", 10_000, None)
            out.update(r.location for r in page.items if r.location)
        return out

    def refresh(self) -> int:
        """Walk the root: index run directories the index does not have,
        read again the ones written since, forget rows whose directory is
        gone. Returns how many were (re)indexed.

        A known run directory is never opened, and a directory holding only
        known runs is skipped unread when it has not changed since the walk
        before (its mtime is older, and its link count says it holds exactly
        the runs the index has for it) — so a walk after the first costs one
        ``stat`` per such directory, not a listing of it and a ``stat`` of
        every run in it."""
        t0 = time.monotonic()
        started = time.time()
        known = self.index.locations()
        open_ = self._open_locations()
        try:
            before_walk = float(self._meta("walked_at") or 0.0)
        except ValueError:
            before_walk = 0.0
        settled_before = before_walk - 5.0 if before_walk else 0.0
        kids = Counter(loc.rpartition("/")[0] for loc in known) if settled_before else {}
        seen: set = set()
        settled: set = set()
        added = 0
        batch: list = []

        def read(loc: str, run_dir: Path, force: bool) -> None:
            nonlocal added, batch
            try:
                written = (run_dir / "meta.json").stat().st_mtime
            except OSError:
                written = 0.0
            before = self._seen_at.get(loc)
            self._seen_at[loc] = written
            if not force and before == written:
                return
            try:
                batch.append(self._summarize_dir(run_dir))
            except Exception:  # noqa: BLE001 — one bad directory is not the store failing
                return
            if len(batch) >= 200:
                self.index.put_many(batch)
                added += len(batch)
                batch = []

        stack = [(str(self.root), "")]
        while stack:
            path, rel = stack.pop()
            try:
                with os.scandir(path) as it:
                    entries = list(it)
            except OSError:
                continue
            if any(e.name == "nodes.jsonl" for e in entries) and not path.endswith(".tmp"):
                loc = (
                    rel or "."
                )  # a run directory the index did not have (its own media/ is never walked)
                seen.add(loc)
                read(loc, Path(path), True)
                continue
            for e in entries:
                name = e.name
                if name.startswith(".") or name.endswith(".tmp"):
                    continue
                try:
                    if not e.is_dir(follow_symlinks=False):
                        continue
                except OSError:
                    continue
                child = f"{rel}/{name}" if rel else name
                if child in known:
                    seen.add(child)
                    if child in open_:
                        read(child, Path(e.path), self._seen_at.get(child) is None)
                    elif settled_before:
                        try:  # replaced where it stood (a rerun under the same id) since the walk before
                            if e.stat(follow_symlinks=False).st_mtime >= settled_before:
                                read(child, Path(e.path), False)
                        except OSError:
                            pass
                    continue
                if child in kids:
                    try:
                        st = e.stat(follow_symlinks=False)
                        if st.st_mtime < settled_before and st.st_nlink == 2 + kids[child]:
                            settled.add(
                                child
                            )  # the same runs as the walk before found: not listed again
                            continue
                    except OSError:
                        pass
                stack.append((e.path, child))
        self.index.put_many(batch)
        added += len(batch)
        gone = [
            tid
            for loc, tid in known.items()
            if loc not in seen and loc.rpartition("/")[0] not in settled
        ]
        if gone:
            self.index.delete(RunFilter(trace_ids=gone))
        self._set_meta("walked_at", started)
        self._walk_cost = time.monotonic() - t0
        self._last_refresh = time.monotonic()
        return added

    def _costly(self) -> bool:
        """Is a walk of this root too much for a reader to wait for?"""
        if self._walk_cost is None:
            with self.index._tx() as cur:
                cur.execute(f"SELECT COUNT(*) FROM {self.index.runs}")
                n = int(cur.fetchone()[0])
            return n > _BIG_RUNS
        return self._walk_cost > _SLOW_WALK

    def _walk_behind(self) -> None:
        """Start a walk in the background, unless one is running or it is
        not time: at most 5% of the time goes to walking."""
        if time.monotonic() < self._next_walk or not self._walk_gate.acquire(blocking=False):
            return

        def run() -> None:
            try:
                self.refresh()
            except Exception:  # noqa: BLE001 — the index stays as it was; the next walk tries again
                pass
            finally:
                self._next_walk = time.monotonic() + max(30.0, 20.0 * (self._walk_cost or 0.0))
                self._walk_gate.release()

        threading.Thread(target=run, name="run-store-walk", daemon=True).start()

    def _maybe_refresh(self) -> None:
        # The journal first: what was written since the last look, for the
        # price of those lines. One reader does it for all that arrive
        # together (a page asks three things at once).
        with self._refreshing:
            self._apply_journal()
            if time.monotonic() - self._last_refresh < self.refresh_every:
                return
            if not self._costly():
                self.refresh()  # a small root: walked here, as it always was
                return
            self._last_refresh = time.monotonic()
        self._walk_behind()

    def _look_again(self) -> None:
        """A run the index does not have: read the journal, and walk for it
        only where a walk is cheap."""
        with self._refreshing:
            self._apply_journal()
            if not self._costly():
                self.refresh()
                return
        self._walk_behind()

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
            self._look_again()
            summary = self.index.get(trace_id)
        if summary is None or not summary.location:
            return None
        run_dir = self.root / summary.location
        if not (run_dir / "nodes.jsonl").is_file():
            return None
        try:
            written = (run_dir / "meta.json").stat().st_mtime
        except OSError:
            written = 0.0
        if self._seen_at.get(summary.location, written) != written:
            summary = self._index_dir(run_dir)  # written again since: a resumed run
        self._seen_at[summary.location] = written
        got = read_run_dir(run_dir)
        return RunRecord(
            summary=summary, nodes=got["nodes"], meta=got["meta"], media_root=str(run_dir)
        )

    def groups(
        self, where: Optional[RunFilter] = None, by=("origin", "name")
    ) -> List[Dict[str, Any]]:
        from .base import _check_by

        self._maybe_refresh()
        return self.index.groups(where, _check_by(by))

    def rollups(self, where: Optional[RunFilter] = None) -> List[OpRollup]:
        self._maybe_refresh()
        return self.index.rollups(where)

    def run_dir(self, trace_id: str) -> Optional[Path]:
        """The directory holding a run — for readers that want its files."""
        summary = self.index.get(trace_id)
        if summary is None:
            self._look_again()
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
