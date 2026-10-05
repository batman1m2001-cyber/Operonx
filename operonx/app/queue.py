"""A durable run queue: work a replica accepted, run by whichever worker
claims it (docs/RUNTIME_R4_PLAN.md D1–D6).

A row is one run to make: the service it is for, the payload a door
received, its thread. A worker *claims* a row with a lease and renews it
while the run goes on; a worker that dies stops renewing, the lease lapses,
and another worker claims the row again — at least once, with the same run
id each attempt, so its trace and ``run_context().idempotency_key`` repeat
(what a side effect deduplicates on). A row past ``max_attempts`` lapses
into ``failed``.

Rows of one thread run one at a time and in order, across every worker:
``claim`` takes a row only when no earlier row of its thread is queued or
running — in the same statement, which is what makes it hold across
replicas.

Two backends share one SQL core: :class:`SqliteQueue` (one host, any number
of processes — WAL and ``BEGIN IMMEDIATE``) and :class:`PostgresQueue`
(replicas — ``FOR UPDATE SKIP LOCKED``). Payload and meta are JSON: what a
door receives over HTTP is JSON already, and a row stays readable with
``sqlite3`` or ``psql``.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Union

__all__ = [
    "LeaseLost",
    "PostgresQueue",
    "QueueItem",
    "RunQueue",
    "SqliteQueue",
    "open_queue",
]

#: A row's states. ``stopped``/``discarded`` are a run another message on
#: its thread stopped (R4b).
STATUSES = ("queued", "running", "done", "failed", "stopped", "discarded")
#: How a run is asked to stop for a newer message on its thread.
STOPS = ("interrupt", "rollback")


class LeaseLost(RuntimeError):
    """The worker no longer holds the row: its lease lapsed and another
    worker claimed it, or it ended."""


@dataclass
class QueueItem:
    """One row: a run to make."""

    id: str
    service: str
    payload: Any = None
    meta: Dict[str, Any] = field(default_factory=dict)
    thread_id: Optional[str] = None
    status: str = "queued"
    attempts: int = 0
    max_attempts: int = 3
    available_at: float = 0.0
    lease_until: Optional[float] = None
    worker: Optional[str] = None
    stop: Optional[str] = None
    error: Optional[str] = None
    created_at: float = 0.0
    updated_at: float = 0.0


_COLUMNS = (
    "id, service, payload, meta, thread_id, status, attempts, max_attempts, available_at, "
    "lease_until, worker, stop, error, created_at, updated_at"
)


def _item(row: Any) -> QueueItem:
    return QueueItem(
        id=row[0],
        service=row[1],
        payload=json.loads(row[2]) if row[2] is not None else None,
        meta=json.loads(row[3]) if row[3] else {},
        thread_id=row[4],
        status=row[5],
        attempts=int(row[6]),
        max_attempts=int(row[7]),
        available_at=float(row[8]),
        lease_until=float(row[9]) if row[9] is not None else None,
        worker=row[10],
        stop=row[11],
        error=row[12],
        created_at=float(row[13]),
        updated_at=float(row[14]),
    )


class RunQueue:
    """The queue's operations, over a SQL connection a backend provides.

    Every method is blocking: call it from a thread (``asyncio.to_thread``)
    on an event loop.
    """

    #: The backend's placeholder and its row lock for a claim.
    _P = "?"
    _LOCK = ""

    @contextmanager
    def _tx(self) -> Iterator[Any]:  # pragma: no cover - each backend's
        raise NotImplementedError
        yield

    def _sql(self, text: str) -> str:
        return text.replace("?", self._P) if self._P != "?" else text

    # -- writing --------------------------------------------------------

    def put(
        self,
        service: str,
        payload: Any = None,
        *,
        meta: Optional[Dict[str, Any]] = None,
        thread_id: Optional[str] = None,
        id: Optional[str] = None,  # noqa: A002 — the row's name
        max_attempts: int = 3,
        delay: float = 0.0,
    ) -> QueueItem:
        """Queue one run; it is durable when this returns."""
        if max_attempts < 1:
            raise ValueError(f"max_attempts={max_attempts!r}: at least 1")
        now = time.time()
        item = QueueItem(
            id=id or uuid.uuid4().hex,
            service=service,
            payload=payload,
            meta=dict(meta or {}),
            thread_id=thread_id,
            max_attempts=max_attempts,
            available_at=now + delay,
            created_at=now,
            updated_at=now,
        )
        try:
            body = json.dumps(payload)
            meta_json = json.dumps(item.meta)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"queue {service!r}: payload and meta must be JSON ({exc})") from exc
        with self._tx() as cur:
            cur.execute(
                self._sql(
                    "INSERT INTO run_queue (id, service, payload, meta, thread_id, status, "
                    "attempts, max_attempts, available_at, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, 'queued', 0, ?, ?, ?, ?)"
                ),
                (
                    item.id,
                    service,
                    body,
                    meta_json,
                    thread_id,
                    max_attempts,
                    item.available_at,
                    now,
                    now,
                ),
            )
        return item

    def claim(self, service: str, worker: str, *, lease_s: float = 30.0) -> Optional[QueueItem]:
        """The oldest runnable row of *service*, now running under
        *worker*'s lease — or None. Runnable: queued and due, or running
        with a lapsed lease; and no earlier row of its thread is queued or
        running. A lapsed row past its attempts is marked ``failed``."""
        now = time.time()
        with self._tx() as cur:
            cur.execute(
                self._sql(
                    "UPDATE run_queue SET status = 'failed', error = ?, lease_until = NULL, "
                    "updated_at = ? WHERE service = ? AND status = 'running' "
                    "AND lease_until < ? AND attempts >= max_attempts"
                ),
                ("its worker stopped renewing the lease on every attempt", now, service, now),
            )
            cur.execute(
                self._sql(
                    "SELECT id FROM run_queue q WHERE q.service = ? AND ("
                    "(q.status = 'queued' AND q.available_at <= ?) "
                    "OR (q.status = 'running' AND q.lease_until < ?)) "
                    "AND (q.thread_id IS NULL OR NOT EXISTS (SELECT 1 FROM run_queue o "
                    "WHERE o.service = q.service AND o.thread_id = q.thread_id AND o.id <> q.id "
                    "AND ((o.status = 'running' AND o.lease_until >= ?) "
                    "OR (o.status IN ('queued', 'running') AND o.seq < q.seq)))) "
                    "ORDER BY q.seq LIMIT 1" + self._LOCK
                ),
                (service, now, now, now),
            )
            row = cur.fetchone()
            if row is None:
                return None
            cur.execute(
                self._sql(
                    "UPDATE run_queue SET status = 'running', worker = ?, lease_until = ?, "
                    "attempts = attempts + 1, updated_at = ? WHERE id = ?"
                ),
                (worker, now + lease_s, now, row[0]),
            )
            cur.execute(self._sql(f"SELECT {_COLUMNS} FROM run_queue WHERE id = ?"), (row[0],))
            return _item(cur.fetchone())

    def renew(self, id: str, worker: str, *, lease_s: float = 30.0) -> Optional[str]:  # noqa: A002
        """Extend *worker*'s lease on the row; return its stop request
        (``None``, ``"interrupt"`` or ``"rollback"``).

        Raises:
            LeaseLost: the row is no longer *worker*'s.
        """
        now = time.time()
        with self._tx() as cur:
            cur.execute(
                self._sql(
                    "UPDATE run_queue SET lease_until = ?, updated_at = ? "
                    "WHERE id = ? AND worker = ? AND status = 'running'"
                ),
                (now + lease_s, now, id, worker),
            )
            if cur.rowcount != 1:
                raise LeaseLost(f"run {id!r} is no longer held by worker {worker!r}")
            cur.execute(self._sql("SELECT stop FROM run_queue WHERE id = ?"), (id,))
            return cur.fetchone()[0]

    def finish(
        self,
        id: str,  # noqa: A002
        worker: str,
        status: str = "done",
        *,
        error: Optional[str] = None,
    ) -> bool:
        """Mark *worker*'s row ended. False when it was no longer its
        (another worker had claimed it): that worker's end counts."""
        if status not in STATUSES or status in ("queued", "running"):
            raise ValueError(f"finish status is done, failed, stopped or discarded, not {status!r}")
        now = time.time()
        with self._tx() as cur:
            cur.execute(
                self._sql(
                    "UPDATE run_queue SET status = ?, error = ?, lease_until = NULL, "
                    "updated_at = ? WHERE id = ? AND worker = ? AND status = 'running'"
                ),
                (status, error, now, id, worker),
            )
            return cur.rowcount == 1

    def request_stop(self, id: str, how: str) -> bool:  # noqa: A002
        """Ask the running row's worker to stop it (seen on its next
        renew). A queued row is ended at once: it never started."""
        if how not in STOPS:
            raise ValueError(f"stop is one of {', '.join(STOPS)}, not {how!r}")
        ended = "stopped" if how == "interrupt" else "discarded"
        now = time.time()
        with self._tx() as cur:
            cur.execute(
                self._sql(
                    "UPDATE run_queue SET status = ?, updated_at = ? "
                    "WHERE id = ? AND status = 'queued'"
                ),
                (ended, now, id),
            )
            if cur.rowcount == 1:
                return True
            cur.execute(
                self._sql(
                    "UPDATE run_queue SET stop = ?, updated_at = ? "
                    "WHERE id = ? AND status = 'running'"
                ),
                (how, now, id),
            )
            return cur.rowcount == 1

    def requeue(self, id: str) -> bool:  # noqa: A002
        """Put an ended row back in the queue — a failed run retried by a
        person, same run id, its attempts counted from zero again. Only a
        row that ended without finishing (failed, stopped, discarded)."""
        now = time.time()
        with self._tx() as cur:
            cur.execute(
                self._sql(
                    "UPDATE run_queue SET status = 'queued', attempts = 0, error = NULL, "
                    "stop = NULL, worker = NULL, lease_until = NULL, available_at = ?, "
                    "updated_at = ? WHERE id = ? AND status IN ('failed', 'stopped', 'discarded')"
                ),
                (now, now, id),
            )
            return cur.rowcount == 1

    def counts(self, service: str) -> Dict[str, Any]:
        """How many of *service*'s rows are in each status, and how long the
        oldest queued one has waited (seconds; None when none waits)."""
        with self._tx() as cur:
            cur.execute(
                self._sql(
                    "SELECT status, COUNT(*), MIN(created_at) FROM run_queue "
                    "WHERE service = ? GROUP BY status"
                ),
                (service,),
            )
            rows = cur.fetchall()
        out: Dict[str, Any] = {status: 0 for status in STATUSES}
        oldest = None
        for status, n, first in rows:
            out[status] = int(n)
            if status == "queued" and first is not None:
                oldest = time.time() - float(first)
        out["oldest_queued_s"] = oldest
        return out

    def fire_once(self, name: str, slot: str) -> bool:
        """True for exactly one caller per ``(name, slot)``, across every
        process sharing the queue: a schedule's tick fires on one replica."""
        with self._tx() as cur:
            cur.execute(
                self._sql(
                    "INSERT INTO run_queue_fires (name, slot, fired_at) VALUES (?, ?, ?) "
                    "ON CONFLICT (name, slot) DO NOTHING"
                ),
                (name, slot, time.time()),
            )
            return cur.rowcount == 1

    # -- reading --------------------------------------------------------

    def get(self, id: str) -> Optional[QueueItem]:  # noqa: A002
        with self._tx() as cur:
            cur.execute(self._sql(f"SELECT {_COLUMNS} FROM run_queue WHERE id = ?"), (id,))
            row = cur.fetchone()
        return _item(row) if row is not None else None

    def items(
        self,
        service: Optional[str] = None,
        *,
        status: Optional[str] = None,
        thread_id: Optional[str] = None,
        limit: int = 100,
    ) -> List[QueueItem]:
        """Rows, oldest first, optionally of one service, status or thread."""
        where, args = [], []
        for column, value in (("service", service), ("status", status), ("thread_id", thread_id)):
            if value is not None:
                where.append(f"{column} = ?")
                args.append(value)
        sql = f"SELECT {_COLUMNS} FROM run_queue"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY seq LIMIT ?"
        with self._tx() as cur:
            cur.execute(self._sql(sql), (*args, int(limit)))
            return [_item(r) for r in cur.fetchall()]


_SQLITE_SCHEMA = """
CREATE TABLE IF NOT EXISTS run_queue (
    seq          INTEGER PRIMARY KEY AUTOINCREMENT,
    id           TEXT NOT NULL UNIQUE,
    service      TEXT NOT NULL,
    payload      TEXT,
    meta         TEXT,
    thread_id    TEXT,
    status       TEXT NOT NULL,
    attempts     INTEGER NOT NULL,
    max_attempts INTEGER NOT NULL,
    available_at REAL NOT NULL,
    lease_until  REAL,
    worker       TEXT,
    stop         TEXT,
    error        TEXT,
    created_at   REAL NOT NULL,
    updated_at   REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS run_queue_claim ON run_queue (service, status, seq);
CREATE INDEX IF NOT EXISTS run_queue_thread ON run_queue (service, thread_id, status);
CREATE TABLE IF NOT EXISTS run_queue_fires (
    name     TEXT NOT NULL,
    slot     TEXT NOT NULL,
    fired_at REAL NOT NULL,
    PRIMARY KEY (name, slot)
);
"""


class SqliteQueue(RunQueue):
    """A queue in one SQLite file, shared by every process on the host."""

    def __init__(self, path: Union[str, Path]):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._local = threading.local()
        self._conn().executescript(_SQLITE_SCHEMA)

    def _conn(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(self.path, timeout=30, isolation_level=None)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            self._local.conn = conn
        return conn

    @contextmanager
    def _tx(self) -> Iterator[Any]:
        conn = self._conn()
        conn.execute("BEGIN IMMEDIATE")  # one writer: a claim is atomic
        cur = conn.cursor()
        try:
            yield cur
            conn.execute("COMMIT")
        except BaseException:
            conn.execute("ROLLBACK")
            raise
        finally:
            cur.close()

    def __repr__(self) -> str:
        return f"SqliteQueue({str(self.path)!r})"


_POSTGRES_SCHEMA = """
CREATE TABLE IF NOT EXISTS run_queue (
    seq          BIGSERIAL PRIMARY KEY,
    id           TEXT NOT NULL UNIQUE,
    service      TEXT NOT NULL,
    payload      TEXT,
    meta         TEXT,
    thread_id    TEXT,
    status       TEXT NOT NULL,
    attempts     INTEGER NOT NULL,
    max_attempts INTEGER NOT NULL,
    available_at DOUBLE PRECISION NOT NULL,
    lease_until  DOUBLE PRECISION,
    worker       TEXT,
    stop         TEXT,
    error        TEXT,
    created_at   DOUBLE PRECISION NOT NULL,
    updated_at   DOUBLE PRECISION NOT NULL
);
CREATE INDEX IF NOT EXISTS run_queue_claim ON run_queue (service, status, seq);
CREATE INDEX IF NOT EXISTS run_queue_thread ON run_queue (service, thread_id, status);
CREATE TABLE IF NOT EXISTS run_queue_fires (
    name     TEXT NOT NULL,
    slot     TEXT NOT NULL,
    fired_at DOUBLE PRECISION NOT NULL,
    PRIMARY KEY (name, slot)
);
"""


class PostgresQueue(RunQueue):
    """A queue in Postgres, for replicas: a claim locks its row with
    ``FOR UPDATE SKIP LOCKED``, so workers never wait on each other."""

    _P = "%s"
    _LOCK = " FOR UPDATE SKIP LOCKED"

    def __init__(self, dsn: str):
        try:
            import psycopg  # noqa: F401
        except ImportError as exc:  # pragma: no cover - the postgres extra
            raise ImportError(
                'PostgresQueue needs psycopg: pip install "operonx[postgres]"'
            ) from exc
        self.dsn = dsn
        self._local = threading.local()
        with self._tx() as cur:
            # one schema creation at a time across replicas starting together
            cur.execute("SELECT pg_advisory_xact_lock(4180417)")
            cur.execute(_POSTGRES_SCHEMA)

    def _conn(self) -> Any:
        import psycopg

        conn = getattr(self._local, "conn", None)
        if conn is None or conn.closed:
            conn = psycopg.connect(self.dsn)
            self._local.conn = conn
        return conn

    @contextmanager
    def _tx(self) -> Iterator[Any]:
        conn = self._conn()
        with conn.transaction(), conn.cursor() as cur:
            yield cur

    def __repr__(self) -> str:
        return "PostgresQueue(<dsn>)"


def open_queue(spec: Any, *, root: Optional[Union[str, Path]] = None) -> RunQueue:
    """A queue from a door's ``queue =``: a :class:`RunQueue` as is; a path
    (``"runs.db"``, relative to *root*) for SQLite; ``{url =
    "postgresql://…"}`` or a ``postgresql://`` string for Postgres."""
    if isinstance(spec, RunQueue):
        return spec
    if isinstance(spec, dict):
        url = spec.get("url")
        if not url:
            raise ValueError(f'queue = {spec!r}: expected url = "postgresql://…" or a path')
        spec = url
    text = str(spec)
    if text.startswith(("postgresql://", "postgres://")):
        return PostgresQueue(text)
    path = Path(text)
    if root is not None and not path.is_absolute():
        path = Path(root) / path
    return SqliteQueue(path)
