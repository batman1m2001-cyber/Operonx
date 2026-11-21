"""The prompt store: published prompts, copied to Postgres for running services.

A project's prompts are files (:mod:`operonx.prompts`): the text, its
versions, the labels. A service runs from an image built some time ago, so a
version published since is not in it. The store closes that gap, one way::

    files ──push──▶ Postgres ──read──▶ every running process

* **Push** (:func:`push`; ``operonx prompt publish`` / ``label`` / ``push``
  and the studio's Publish do it) copies every published version and every
  label of the project. A version is never changed there, as it is never
  changed on disk.
* **Read**: ``prompt(name)`` in a process with ``OPERONX_PROMPT_STORE`` set
  gives the version the store's label points at, with the store's text. A
  prompt the store has no label for, or a store that cannot be reached, gives
  what the files give — so the store can be down and nothing stops.

Nothing writes from the store back to the files, and nothing edits a text in
the store: the files stay the one place a prompt is written.

The read never waits on the network after the first one: a process keeps what
the store served last and a background thread asks again every
``OPERONX_PROMPT_STORE_EVERY`` seconds (15). :func:`generation` goes up when
what is served changed — a service that builds its prompts once (to keep the
bytes the same between calls) builds them again when it does.

Where the store is, is a setting of the project — in ``operonx.toml``, the
address itself in the environment (or the project's ``.env``), as a run store's is::

    [prompts]
    store = "${PROMPT_STORE_DSN}"      # postgresql://user:pass@host/db

``OPERONX_PROMPT_STORE`` in the environment names it for a process whatever
the file says. ``OPERONX_PROMPT_PROJECT`` is the name the project's rows are
kept under (default: ``[project] name`` in ``operonx.toml``).
"""

from __future__ import annotations

import hashlib
import os
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from operonx.core import LOGGER

__all__ = [
    "StoreError",
    "configured",
    "dsn_of",
    "generation",
    "project_key",
    "push",
    "refresh",
    "served",
    "status",
]

ENV_DSN = "OPERONX_PROMPT_STORE"
ENV_PROJECT = "OPERONX_PROMPT_PROJECT"
ENV_EVERY = "OPERONX_PROMPT_STORE_EVERY"

_SCHEMA = (
    """CREATE TABLE IF NOT EXISTS operonx_prompt_versions (
        project TEXT NOT NULL, name TEXT NOT NULL, version INTEGER NOT NULL,
        text TEXT NOT NULL, hash TEXT NOT NULL,
        published_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        PRIMARY KEY (project, name, version))""",
    """CREATE TABLE IF NOT EXISTS operonx_prompt_labels (
        project TEXT NOT NULL, name TEXT NOT NULL, label TEXT NOT NULL,
        version INTEGER NOT NULL, moved_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        PRIMARY KEY (project, name, label))""",
)


class StoreError(RuntimeError):
    """The store could not be written (not set, not reachable, or it holds a different text)."""


_DSN_SEEN: Dict[str, Tuple[Tuple[float, float], str]] = {}


def _mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def dsn_of(root: Optional[Path] = None) -> str:
    """The store's address for the project at *root* ("" when none is set):
    ``OPERONX_PROMPT_STORE``, else ``[prompts] store`` in ``operonx.toml``
    with ``${VAR}`` filled from the environment and the project's ``.env``.
    A ``${VAR}`` nothing sets is no store."""
    named = os.environ.get(ENV_DSN, "").strip()
    if named:
        return named
    from operonx.prompts import prompts_dir

    folder = prompts_dir(root).parent
    toml, env = folder / "operonx.toml", folder / ".env"
    stamp = (_mtime(toml), _mtime(env))
    if not stamp[0]:
        return ""
    seen = _DSN_SEEN.get(str(folder))
    if seen is not None and seen[0] == stamp:
        return seen[1]
    from operonx.telemetry.runs.project import _interpolate, _toml, read_dotenv

    value = ""
    missing: List[str] = []
    try:
        raw = (_toml(toml).get("prompts") or {}).get("store")
        if isinstance(raw, str) and raw.strip():
            value = str(_interpolate(raw.strip(), {**read_dotenv(env), **os.environ}, missing))
    except Exception:  # noqa: BLE001 — a toml that does not parse names no store
        value = ""
    if missing:
        value = ""
    _DSN_SEEN[str(folder)] = (stamp, value)
    return value


def configured(root: Optional[Path] = None) -> bool:
    """Is a store set for the project at *root*?"""
    return bool(dsn_of(root))


def project_key(root: Optional[Path] = None) -> str:
    """The name a project's rows are kept under: ``OPERONX_PROMPT_PROJECT``,
    else ``[project] name`` in its ``operonx.toml``, else the folder's name."""
    named = os.environ.get(ENV_PROJECT, "").strip()
    if named:
        return named
    from operonx.prompts import prompts_dir

    folder = prompts_dir(root).parent
    toml = folder / "operonx.toml"
    if toml.is_file():
        try:
            import tomllib
        except ModuleNotFoundError:  # Python 3.10
            import tomli as tomllib  # type: ignore[no-redef]
        try:
            name = (tomllib.loads(toml.read_text(encoding="utf-8")).get("project") or {}).get(
                "name"
            )
            if name:
                return str(name)
        except Exception:  # noqa: BLE001 — a toml that does not parse names nothing
            pass
    return folder.resolve().name


def _hash(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:10]


def _connect(dsn: str) -> Any:
    try:
        import psycopg
    except ImportError as exc:
        raise StoreError(
            "the prompt store is Postgres and psycopg is not installed (pip install 'operonx[postgres]')"
        ) from exc
    return psycopg.connect(dsn, connect_timeout=3)


# ── push: files → store ──────────────────────────────────────────────────


def push(root: Optional[Path] = None, *, dsn: Optional[str] = None) -> Dict[str, Any]:
    """Copy the project's published versions and labels to the store.

    Safe to repeat: a version already there is left alone, a label is moved
    only when it points elsewhere. Returns ``{project, versions, labels}`` —
    the new versions as ``[(name, version)]`` and the labels moved as
    ``[(name, label, version, before)]``. Raises :class:`StoreError` when no
    store is set or it cannot be reached, and when the store holds a
    *different* text for a version (two checkouts published different texts
    under one number): nothing is written then.
    """
    from operonx import prompts as P

    dsn = dsn or dsn_of(root)
    if not dsn:
        raise StoreError(
            "no prompt store is set ([prompts] store in operonx.toml, or OPERONX_PROMPT_STORE)"
        )
    key, base = project_key(root), P.prompts_dir(root)
    mine: Dict[Tuple[str, int], str] = {}
    for name in P.prompt_names(base.parent):
        for version in P.versions(name, base.parent):
            mine[(name, version)] = P._read(base, name, version)
    marks = [
        (name, label, version)
        for name, table in sorted(P.labels(base.parent).items())
        for label, version in sorted(table.items())
        if (name, version) in mine
    ]
    try:
        with _connect(dsn) as conn:
            for stmt in _SCHEMA:
                conn.execute(stmt)
            theirs = {
                (n, v): h
                for n, v, h in conn.execute(
                    "SELECT name, version, hash FROM operonx_prompt_versions WHERE project = %s",
                    (key,),
                )
            }
            clash = sorted(
                k for k, text in mine.items() if k in theirs and theirs[k] != _hash(text)
            )
            if clash:
                said = ", ".join(f"{n} v{v}" for n, v in clash)
                raise StoreError(
                    f"the store holds a different text for {said} (project {key!r}): another checkout "
                    "published under the same number. Nothing was pushed."
                )
            new = sorted(k for k in mine if k not in theirs)
            for name, version in new:
                text = mine[(name, version)]
                conn.execute(
                    "INSERT INTO operonx_prompt_versions (project, name, version, text, hash) "
                    "VALUES (%s, %s, %s, %s, %s) ON CONFLICT DO NOTHING",
                    (key, name, version, text, _hash(text)),
                )
            was = {
                (n, lab): v
                for n, lab, v in conn.execute(
                    "SELECT name, label, version FROM operonx_prompt_labels WHERE project = %s",
                    (key,),
                )
            }
            moved = [
                (n, lab, v, was.get((n, lab))) for n, lab, v in marks if was.get((n, lab)) != v
            ]
            for name, label, version, _before in moved:
                conn.execute(
                    "INSERT INTO operonx_prompt_labels (project, name, label, version) VALUES (%s, %s, %s, %s) "
                    "ON CONFLICT (project, name, label) DO UPDATE SET version = EXCLUDED.version, moved_at = now()",
                    (key, name, label, version),
                )
    except StoreError:
        raise
    except Exception as exc:  # noqa: BLE001 — the driver's errors, said as one
        raise StoreError(f"the prompt store could not be written: {exc}") from exc
    return {"project": key, "versions": new, "labels": moved}


# ── read: store → this process ───────────────────────────────────────────


class _Served:
    """What the store serves for one project, as this process last read it."""

    def __init__(self, dsn: str, key: str) -> None:
        self.dsn, self.key = dsn, key
        self.labels: Dict[str, Dict[str, int]] = {}
        self.texts: Dict[Tuple[str, int], str] = {}
        self.generation = 0
        self.read_at: Optional[float] = None  # the last read that worked
        self.error: Optional[str] = None
        self._warned = False
        self._lock = threading.Lock()
        self._thread: Optional[threading.Thread] = None
        self._pid = os.getpid()

    def load(self) -> None:
        try:
            with _connect(self.dsn) as conn:
                rows = conn.execute(
                    "SELECT l.name, l.label, l.version, v.text FROM operonx_prompt_labels l "
                    "JOIN operonx_prompt_versions v ON v.project = l.project AND v.name = l.name "
                    "AND v.version = l.version WHERE l.project = %s",
                    (self.key,),
                ).fetchall()
        except Exception as exc:  # noqa: BLE001 — unreachable, or no tables yet: serve what we have
            self.error = (
                str(exc).strip().splitlines()[0] if str(exc).strip() else type(exc).__name__
            )
            if not self._warned:
                self._warned = True
                LOGGER.warning(
                    "prompt store: not read (%s) — prompts come from %s",
                    self.error,
                    "what it served last" if self.read_at else "the project's files",
                )
            return
        labels: Dict[str, Dict[str, int]] = {}
        texts: Dict[Tuple[str, int], str] = {}
        for name, label, version, text in rows:
            labels.setdefault(name, {})[label] = version
            texts[(name, version)] = text
        with self._lock:
            if self.read_at is None or labels != self.labels or texts != self.texts:
                self.generation += 1
            self.labels, self.texts = labels, texts
            self.read_at, self.error, self._warned = time.time(), None, False

    def watch(self) -> None:
        """Keep reading in the background (once per process: a fork starts its own)."""
        if self._thread is not None and self._pid == os.getpid() and self._thread.is_alive():
            return
        self._pid = os.getpid()
        try:
            every = max(1.0, float(os.environ.get(ENV_EVERY, "") or 15))
        except ValueError:
            every = 15.0

        def loop() -> None:
            while True:
                time.sleep(every)
                self.load()

        self._thread = threading.Thread(target=loop, name="operonx-prompt-store", daemon=True)
        self._thread.start()


_SERVED: Dict[Tuple[str, str], _Served] = {}
_SERVED_LOCK = threading.Lock()


def _of(root: Optional[Path]) -> Optional[_Served]:
    dsn = dsn_of(root)
    if not dsn:
        return None
    key = project_key(root)
    with _SERVED_LOCK:
        got = _SERVED.get((dsn, key))
        if got is None:
            got = _SERVED[(dsn, key)] = _Served(dsn, key)
            got.load()  # the first read is waited for: a process starts on what is served
        got.watch()
    return got


def served(name: str, label: str, root: Optional[Path] = None) -> Optional[Tuple[int, str]]:
    """``(version, text)`` the store serves for *label* of *name* — ``None``
    when no store is set, it has not been read, or it has no such label."""
    got = _of(root)
    if got is None:
        return None
    version = got.labels.get(name, {}).get(label)
    if version is None:
        return None
    text = got.texts.get((name, version))
    return None if text is None else (version, text)


def generation(root: Optional[Path] = None) -> int:
    """A number that goes up each time what the store serves this project
    changed (0: no store, or never read). Compare it to rebuild what was
    built from prompts."""
    got = _of(root)
    return 0 if got is None else got.generation


def refresh(root: Optional[Path] = None) -> int:
    """Read the store now (not waiting for the background read); the generation after it."""
    got = _of(root)
    if got is None:
        return 0
    got.load()
    return got.generation


def status(root: Optional[Path] = None) -> Dict[str, Any]:
    """What this process knows of the store: is one set, the project's name
    there, when it was last read, the error of the last read that failed,
    and what each label serves."""
    got = _of(root)
    if got is None:
        return {"configured": False}
    serving: List[Dict[str, Any]] = [
        {"name": n, "label": lab, "version": v}
        for n, t in sorted(got.labels.items())
        for lab, v in sorted(t.items())
    ]
    return {
        "configured": True,
        "project": got.key,
        "read_at": got.read_at,
        "error": got.error,
        "generation": got.generation,
        "serving": serving,
    }
