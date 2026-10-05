"""The run journal: what a durable run wrote, step by step.

A run's journal is a header (:class:`RunHeader`) and an append-only list of
:class:`Step` rows. A step is one execution's progress — a yield or its end —
with the cell writes it made since its previous step, so writes and the event
that follows them are durable together (docs/RUNTIME_R3_PLAN.md §2). A
:class:`Journal` stores them; :class:`MemoryJournal` keeps them in a dict,
:class:`SqliteJournal` in one SQLite file.

Values are JSON (:mod:`.codec`): the journal gives back exactly what an op
returned — a tuple stays a tuple, a dataclass that dataclass — or a resumed
run would differ from the one that crashed; and reading a journal runs no
code, so a journal others can write (a shared file, a Postgres table) is not
a way into the workers that resume from it.
"""

from __future__ import annotations

import sqlite3
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Protocol, Sequence, Tuple, Union, runtime_checkable

from . import codec

__all__ = [
    "END",
    "PARKED",
    "Journal",
    "JournalError",
    "MemoryJournal",
    "RunHeader",
    "SqliteJournal",
    "Step",
]

#: A step's ``index`` when it is the execution's end.
END = -1
#: A step's ``index`` when the execution is an interrupt that parked the run:
#: its ``event`` is ``(interrupt_id, payload)``, the question a resume answers.
PARKED = -2
#: Run statuses a journal records.
STATUSES = ("running", "ok", "error", "interrupted", "drained")


class JournalError(RuntimeError):
    """The journal cannot record or read a run."""


@dataclass
class RunHeader:
    """A journalled run: its id, its thread, the fingerprint of the graph it
    ran, the inputs it started with, the cells its thread carried into it,
    and how far it got."""

    run_id: str
    fingerprint: str
    inputs: Dict[str, Any] = field(default_factory=dict)
    thread_id: Optional[str] = None
    status: str = "running"
    created_at: float = 0.0
    updated_at: float = 0.0
    #: The declared cells the run started with from its thread (``carry=``):
    #: kept here so a resume seeds the same values, whatever the thread has
    #: saved since.
    carried: Dict[str, Any] = field(default_factory=dict)


@dataclass
class Step:
    """One execution's progress.

    ``op`` and ``ctx`` name the execution; ``index`` is the yield's number
    (0, 1, …), :data:`END` or :data:`PARKED`. ``writes`` are ``(op, var, ctx, value)`` with
    post-reducer values. ``event`` is what the execution yielded —
    ``(item_ctx, outputs)``, or ``(ctx, Failure)`` on an error edge — and,
    on the end step of an op that yields once, the tuple of its events (held
    until it ended, so they and the end are one step). An end step holds
    ``status`` (``ok``/``error``), the op's ``error`` cell value and its
    ``$errors`` record. ``digest`` checks a repeated yield on resume."""

    op: str
    ctx: Tuple[str, ...]
    index: int
    writes: List[Tuple[str, str, Tuple[str, ...], Any]] = field(default_factory=list)
    event: Optional[Tuple[Any, ...]] = None
    status: Optional[str] = None
    error: Any = None
    error_record: Optional[Dict[str, Any]] = None
    digest: Optional[str] = None
    #: Which start of the op at this ctx inside its parent execution, and that
    #: parent (``None`` at the root): a synthetic loop runs every iteration at
    #: the same ctx, each a new execution of the loop op.
    occurrence: int = 0
    parent: Any = None
    #: What the execution put on the run's stream queue itself since its
    #: last step (a synthetic loop's per-iteration frames), and — when it put
    #: something — the executions it ran inside, innermost first.
    emits: List[Any] = field(default_factory=list)
    within: Tuple[Any, ...] = ()
    #: How a synthetic loop's iteration ended (``(fired, taken)`` by loop
    #: and ctx), so a replayed iteration still continues or ends the loop.
    signals: Dict[Tuple[str, Tuple[str, ...]], Any] = field(default_factory=dict)


@runtime_checkable
class Journal(Protocol):
    """Where durable runs are recorded. ``append`` is atomic per call."""

    def open_run(self, header: RunHeader) -> None: ...

    def append(self, run_id: str, steps: Sequence[Step]) -> None: ...

    def read(self, run_id: str) -> Tuple[RunHeader, List[Step]]: ...

    def set_status(self, run_id: str, status: str) -> None: ...

    def runs(self, status: Optional[str] = None) -> List[RunHeader]: ...

    def load_thread(self, thread_id: str) -> Dict[str, Any]: ...

    def save_thread(self, thread_id: str, values: Dict[str, Any]) -> None: ...


def _check_status(status: str) -> None:
    if status not in STATUSES:
        raise JournalError(f"run status is one of {', '.join(STATUSES)}, not {status!r}")


class MemoryJournal:
    """Runs in a dict — one process, tests, and runs that only need to park."""

    def __init__(self) -> None:
        self._runs: Dict[str, Tuple[RunHeader, List[bytes]]] = {}
        self._threads: Dict[str, bytes] = {}
        self._lock = threading.Lock()

    def open_run(self, header: RunHeader) -> None:
        with self._lock:
            if header.run_id in self._runs:
                raise JournalError(f"run {header.run_id!r} is already in the journal")
            header.created_at = header.updated_at = time.time()
            self._runs[header.run_id] = (header, [])

    def append(self, run_id: str, steps: Sequence[Step]) -> None:
        blobs = [_dump(s) for s in steps]  # encoded now: the caller may change them
        with self._lock:
            self._get(run_id)[1].extend(blobs)

    def read(self, run_id: str) -> Tuple[RunHeader, List[Step]]:
        with self._lock:
            header, blobs = self._get(run_id)
            return header, [_load(b) for b in blobs]

    def set_status(self, run_id: str, status: str) -> None:
        _check_status(status)
        with self._lock:
            header = self._get(run_id)[0]
            header.status, header.updated_at = status, time.time()

    def runs(self, status: Optional[str] = None) -> List[RunHeader]:
        with self._lock:
            return [h for h, _ in self._runs.values() if status is None or h.status == status]

    def load_thread(self, thread_id: str) -> Dict[str, Any]:
        with self._lock:
            blob = self._threads.get(thread_id)
        return _load(blob) if blob is not None else {}

    def save_thread(self, thread_id: str, values: Dict[str, Any]) -> None:
        blob = _dump(values)
        with self._lock:
            self._threads[thread_id] = blob

    def _get(self, run_id: str) -> Tuple[RunHeader, List[bytes]]:
        found = self._runs.get(run_id)
        if found is None:
            raise JournalError(f"no run {run_id!r} in the journal")
        return found


_SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    run_id      TEXT PRIMARY KEY,
    thread_id   TEXT,
    fingerprint TEXT NOT NULL,
    inputs      BLOB NOT NULL,
    status      TEXT NOT NULL,
    created_at  REAL NOT NULL,
    updated_at  REAL NOT NULL,
    carried     BLOB
);
CREATE TABLE IF NOT EXISTS threads (
    thread_id  TEXT PRIMARY KEY,
    vals       BLOB NOT NULL,
    updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS steps (
    run_id TEXT NOT NULL,
    seq    INTEGER NOT NULL,
    step   BLOB NOT NULL,
    PRIMARY KEY (run_id, seq)
);
CREATE INDEX IF NOT EXISTS runs_status ON runs (status);
"""


class SqliteJournal:
    """Runs in one SQLite file (WAL), shared by every process that opens it."""

    def __init__(self, path: Union[str, Path]):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._local = threading.local()
        with self._conn() as c:
            c.executescript(_SCHEMA)

    def _conn(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(self.path, timeout=30, isolation_level=None)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            self._local.conn = conn
        return conn

    def open_run(self, header: RunHeader) -> None:
        now = time.time()
        header.created_at = header.updated_at = now
        try:
            self._conn().execute(
                "INSERT INTO runs VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    header.run_id,
                    header.thread_id,
                    header.fingerprint,
                    _dump(header.inputs),
                    header.status,
                    now,
                    now,
                    _dump(header.carried),
                ),
            )
        except sqlite3.IntegrityError:
            raise JournalError(f"run {header.run_id!r} is already in the journal") from None

    def append(self, run_id: str, steps: Sequence[Step]) -> None:
        if not steps:
            return
        blobs = [_dump(s) for s in steps]
        conn = self._conn()
        conn.execute("BEGIN IMMEDIATE")
        try:
            row = conn.execute(
                "SELECT COALESCE(MAX(seq), -1) FROM steps WHERE run_id = ?", (run_id,)
            ).fetchone()
            first = int(row[0]) + 1
            conn.executemany(
                "INSERT INTO steps VALUES (?, ?, ?)",
                [(run_id, first + i, b) for i, b in enumerate(blobs)],
            )
            conn.execute("COMMIT")
        except BaseException:
            conn.execute("ROLLBACK")
            raise

    def read(self, run_id: str) -> Tuple[RunHeader, List[Step]]:
        conn = self._conn()
        row = conn.execute(
            "SELECT run_id, thread_id, fingerprint, inputs, status, created_at, updated_at, "
            "carried FROM runs WHERE run_id = ?",
            (run_id,),
        ).fetchone()
        if row is None:
            raise JournalError(f"no run {run_id!r} in {self.path}")
        header = RunHeader(
            run_id=row[0],
            thread_id=row[1],
            fingerprint=row[2],
            inputs=_load(row[3]),
            status=row[4],
            created_at=row[5],
            updated_at=row[6],
            carried=_load(row[7]) if row[7] is not None else {},
        )
        steps = [
            _load(b)
            for (b,) in conn.execute(
                "SELECT step FROM steps WHERE run_id = ? ORDER BY seq", (run_id,)
            )
        ]
        return header, steps

    def set_status(self, run_id: str, status: str) -> None:
        _check_status(status)
        cur = self._conn().execute(
            "UPDATE runs SET status = ?, updated_at = ? WHERE run_id = ?",
            (status, time.time(), run_id),
        )
        if cur.rowcount == 0:
            raise JournalError(f"no run {run_id!r} in {self.path}")

    def runs(self, status: Optional[str] = None) -> List[RunHeader]:
        sql = "SELECT run_id, thread_id, fingerprint, status, created_at, updated_at FROM runs"
        args: tuple = ()
        if status is not None:
            sql, args = sql + " WHERE status = ?", (status,)
        return [
            RunHeader(
                run_id=r[0],
                thread_id=r[1],
                fingerprint=r[2],
                status=r[3],
                created_at=r[4],
                updated_at=r[5],
            )
            for r in self._conn().execute(sql + " ORDER BY created_at", args)
        ]

    def load_thread(self, thread_id: str) -> Dict[str, Any]:
        row = (
            self._conn()
            .execute("SELECT vals FROM threads WHERE thread_id = ?", (thread_id,))
            .fetchone()
        )
        return _load(row[0]) if row is not None else {}

    def save_thread(self, thread_id: str, values: Dict[str, Any]) -> None:
        self._conn().execute(
            "INSERT INTO threads VALUES (?, ?, ?) ON CONFLICT (thread_id) DO UPDATE "
            "SET vals = excluded.vals, updated_at = excluded.updated_at",
            (thread_id, _dump(values), time.time()),
        )

    def __repr__(self) -> str:
        return f"SqliteJournal({str(self.path)!r})"


def _dump(value: Any) -> bytes:
    try:
        return codec.dumps(value)
    except codec.CodecError as exc:
        raise JournalError(_unjournalable(value, exc)) from exc


def _load(blob: Any) -> Any:
    try:
        return codec.loads(blob)
    except codec.CodecError as exc:
        raise JournalError(f"the journal holds a value this process cannot read: {exc}") from exc


def _unjournalable(value: Any, exc: BaseException) -> str:
    """Which write or event could not be journalled, by op and var."""
    if isinstance(value, Step):
        for op, var, _ctx, v in value.writes:
            try:
                codec.encode(v)
            except codec.CodecError:
                return (
                    f"{op}.{var} wrote a {type(v).__name__}, which cannot be journalled "
                    f"({exc}). Return plain data, or keep the object in a resource and "
                    "pass its key"
                )
        return f"{value.op} yielded something that cannot be journalled ({exc})"
    return f"a {type(value).__name__} cannot be journalled ({exc})"
