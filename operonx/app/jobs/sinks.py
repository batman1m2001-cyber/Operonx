"""Where a job's results go.

A sink is written once per item that reaches ``egress`` and closed once
per run. Files, a list, a callable; a table is one more class. The key
travels with the item — ``write(key, item)`` — because a sink that
cannot say *which* item a row belongs to is the one you cannot join back
to the source when something is wrong.

Like sources, sinks are resources::

    sink:scores:
      kind: jsonl
      path: out/scores.jsonl
"""

from __future__ import annotations

import csv
import inspect
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Callable, ClassVar, Dict, List, Optional, Protocol, Tuple, runtime_checkable

from pydantic import Field

from operonx.core.utils import YamlModel

from ._keys import is_resource_key

__all__ = [
    "Sink",
    "JsonlSink",
    "CsvSink",
    "ListSink",
    "PythonSink",
    "NullSink",
    "DirSink",
    "SinkConfig",
    "as_sink",
    "create_sink",
    "open_sink",
]

#: Where the item's key lands when it is written into a row.
KEY_FIELD = "_key"

#: File sink modes. ``auto`` is decided per run by ``begin()``.
MODES = ("auto", "append", "overwrite")


def _check_mode(mode: str) -> str:
    if mode not in MODES:
        raise ValueError(f"mode must be one of {MODES}, not {mode!r}")
    return mode


@runtime_checkable
class Sink(Protocol):
    async def write(self, key: str, item: Any) -> None: ...

    async def close(self) -> None: ...


def _as_row(key: str, item: Any, key_field: Optional[str]) -> Dict[str, Any]:
    """A dict row with the key first; a non-dict item lands under ``item``."""
    body = dict(item) if isinstance(item, Mapping) else {"item": item}
    if key_field:
        return {key_field: key, **{k: v for k, v in body.items() if k != key_field}}
    return body


class JsonlSink:
    """One JSON object per line, opened on the first write. Writes are
    flushed one at a time: a run that is killed still leaves every item it
    finished.

    ``mode``:

    * ``"auto"`` (the default) — inside a job, a fresh run starts the file
      over and a ``--resume`` run adds to it, so re-running a job never
      doubles its output; used directly, it appends;
    * ``"append"`` — always add;
    * ``"overwrite"`` — always start fresh.
    """

    def __init__(
        self, path: str | Path, *, mode: str = "auto", key_field: Optional[str] = KEY_FIELD
    ):
        self.path = Path(path)
        self.mode = _check_mode(mode)
        self._append = mode != "overwrite"
        self.key_field = key_field
        self._fh = None
        self.written = 0

    def begin(self, *, resume: bool) -> None:
        """Called by the job before the first write."""
        if self.mode == "auto":
            self._append = resume

    def _open(self):
        if self._fh is None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._fh = self.path.open("a" if self._append else "w", encoding="utf-8")
        return self._fh

    async def write(self, key: str, item: Any) -> None:
        fh = self._open()
        fh.write(json.dumps(_as_row(key, item, self.key_field), ensure_ascii=False, default=str))
        fh.write("\n")
        fh.flush()
        self.written += 1

    async def close(self) -> None:
        if self._fh is not None:
            self._fh.close()
            self._fh = None

    def __repr__(self) -> str:
        return f"jsonl({self.path})"


class CsvSink:
    """Header from the first item's fields; later items are written to
    those columns and anything else is dropped rather than shifting a
    column — a CSV with a ragged row is worse than one with a hole.

    ``mode``:

    * ``"auto"`` (the default) — inside a job, a fresh run starts the file
      over and a ``--resume`` run adds to it, so re-running a job never
      doubles its output; used directly, it appends;
    * ``"append"`` — always add;
    * ``"overwrite"`` — always start fresh.
    """

    def __init__(
        self, path: str | Path, *, mode: str = "auto", key_field: Optional[str] = KEY_FIELD
    ):
        self.path = Path(path)
        self.mode = _check_mode(mode)
        self._append = mode != "overwrite"
        self.key_field = key_field
        self._fh = None
        self._writer = None
        self.written = 0

    def begin(self, *, resume: bool) -> None:
        """Called by the job before the first write."""
        if self.mode == "auto":
            self._append = resume

    async def write(self, key: str, item: Any) -> None:
        row = _as_row(key, item, self.key_field)
        if self._writer is None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            fresh = not self._append or not self.path.exists() or self.path.stat().st_size == 0
            self._fh = self.path.open("a" if self._append else "w", encoding="utf-8", newline="")
            self._writer = csv.DictWriter(
                self._fh, fieldnames=list(row.keys()), extrasaction="ignore"
            )
            if fresh:
                self._writer.writeheader()
        self._writer.writerow(row)
        self._fh.flush()
        self.written += 1

    async def close(self) -> None:
        if self._fh is not None:
            self._fh.close()
            self._fh = None
            self._writer = None

    def __repr__(self) -> str:
        return f"csv({self.path})"


class ListSink:
    """Collect into a list — the sink tests and ``engine.batch`` shapes use.

    ``target`` may be a list the caller already holds; items are appended
    to it in the order they were written. ``pairs`` keeps the keys.
    """

    def __init__(self, target: Optional[List[Any]] = None):
        self.values: List[Any] = target if target is not None else []
        self.pairs: List[Tuple[str, Any]] = []
        self.closed = False

    async def write(self, key: str, item: Any) -> None:
        self.values.append(item)
        self.pairs.append((key, item))

    async def close(self) -> None:
        self.closed = True

    def __repr__(self) -> str:
        return f"list({len(self.pairs)})"


class PythonSink:
    """A callable ``fn(key, item)``, sync or async, or ``"module:attr"``."""

    def __init__(self, fn: Callable[[str, Any], Any] | str):
        self._fn = fn
        self.written = 0

    def _resolve(self) -> Callable[[str, Any], Any]:
        if isinstance(self._fn, str):
            from operonx.app.serve.registry import load_object

            self._fn = load_object(self._fn, field="sink")
        return self._fn

    async def write(self, key: str, item: Any) -> None:
        result = self._resolve()(key, item)
        if inspect.isawaitable(result):
            await result
        self.written += 1

    async def close(self) -> None:
        fn = self._fn
        closer = getattr(fn, "close", None)
        if callable(closer):
            result = closer()
            if inspect.isawaitable(result):
                await result

    def __repr__(self) -> str:
        fn = self._fn
        return (
            f"python({fn if isinstance(fn, str) else getattr(fn, '__name__', type(fn).__name__)})"
        )


class DirSink:
    """One JSON file per item: ``<path>/<key><suffix>``.

    The shape a batch scorer writes — one output per input, named after
    it. A file is written whole (to a temporary name, then renamed), so a
    killed run never leaves half a file where a finished one is expected.

    ``skip_existing=True`` makes the job skip any key whose file is
    already there — re-running over a directory does only what is
    missing, with no run record needed. ``write_errors=True`` writes a
    failed item's file too, as ``{"error": "..."}``, so every input has
    an output a reader can check. ``indent`` / ``ensure_ascii`` go to
    ``json.dumps``.
    """

    def __init__(
        self,
        path: str | Path,
        *,
        suffix: str = ".json",
        skip_existing: bool = False,
        write_errors: bool = False,
        indent: Optional[int] = 2,
        ensure_ascii: bool = False,
    ):
        self.path = Path(path)
        self.suffix = suffix
        self.skip_existing = skip_existing
        self.write_errors = write_errors
        self.indent = indent
        self.ensure_ascii = ensure_ascii
        self.written = 0

    def file_for(self, key: str) -> Path:
        return self.path / f"{key}{self.suffix}"

    def exists(self, key: str) -> bool:
        """True when the job should skip *key* — only under ``skip_existing``."""
        return self.skip_existing and self.file_for(key).exists()

    async def fail(self, key: str, error: str) -> None:
        """A failed item's file, under ``write_errors``."""
        if self.write_errors:
            await self._put(key, {"error": error})

    async def write(self, key: str, item: Any) -> None:
        await self._put(key, item)
        self.written += 1

    async def _put(self, key: str, item: Any) -> None:
        self.path.mkdir(parents=True, exist_ok=True)
        target = self.file_for(key)
        tmp = target.with_name(target.name + ".part")
        tmp.write_text(
            json.dumps(item, ensure_ascii=self.ensure_ascii, indent=self.indent, default=str),
            encoding="utf-8",
        )
        tmp.replace(target)

    async def close(self) -> None:
        return None

    def __repr__(self) -> str:
        return f"dir({self.path}/*{self.suffix})"


class NullSink:
    """Drops everything and counts it. What a job without a sink gets."""

    def __init__(self):
        self.written = 0

    async def write(self, key: str, item: Any) -> None:
        self.written += 1

    async def close(self) -> None:
        return None

    def __repr__(self) -> str:
        return "null"


# -- as a resource ---------------------------------------------------------


class SinkConfig(YamlModel):
    """A ``sink:<name>`` block in resources.yaml."""

    _category: ClassVar[str] = "sink"

    kind: str
    path: Optional[str] = None
    entry: Optional[str] = None
    mode: str = "auto"
    options: Dict[str, Any] = Field(default_factory=dict)


def open_sink(
    kind: str,
    *,
    path: Optional[str] = None,
    entry: Optional[str] = None,
    mode: str = "auto",
    **options: Any,
) -> Sink:
    if kind == "jsonl":
        if not path:
            raise ValueError("sink kind 'jsonl' needs `path`")
        return JsonlSink(path, mode=mode, **options)
    if kind == "csv":
        if not path:
            raise ValueError("sink kind 'csv' needs `path`")
        return CsvSink(path, mode=mode, **options)
    if kind == "python":
        if not entry:
            raise ValueError("sink kind 'python' needs `entry` (module:attr)")
        return PythonSink(entry)
    if kind == "null":
        return NullSink()
    if kind == "dir":
        if not path:
            raise ValueError("sink kind 'dir' needs `path`")
        return DirSink(path, **options)
    raise ValueError(f"unknown sink kind {kind!r} (have: jsonl, csv, dir, python, null)")


def create_sink(config: SinkConfig) -> Sink:
    return open_sink(
        config.kind, path=config.path, entry=config.entry, mode=config.mode, **config.options
    )


def _is_sink(obj: Any) -> bool:
    return (
        callable(getattr(obj, "write", None))
        and callable(getattr(obj, "close", None))
        and not isinstance(obj, (str, bytes, Path))
    )


def _by_extension(path: Path) -> Sink:
    if path.is_dir():
        return DirSink(path)
    ext = path.suffix.lower()
    if ext == ".jsonl":
        return JsonlSink(path)
    if ext == ".csv":
        return CsvSink(path)
    raise ValueError(
        f"cannot tell a sink from {path}: expected an existing directory, .jsonl or .csv, "
        "or declare it under `sink:` in resources.yaml"
    )


def as_sink(obj: Any) -> Sink:
    """Turn what a Job was given into a sink.

    ``None`` is a :class:`NullSink`: a job that only needs its record — or
    whose graph writes its own output — declares no sink and nothing is
    lost silently, because the record still counts what egress sent.
    """
    if obj is None:
        return NullSink()
    if _is_sink(obj):
        return obj
    if isinstance(obj, list):
        return ListSink(obj)
    if isinstance(obj, str):
        if is_resource_key(obj):
            from operonx.app.jobs import register
            from operonx.core.registry import ResourceHub

            register()
            resolved = ResourceHub.instance().get(obj)
            if not _is_sink(resolved):
                raise TypeError(
                    f"{obj!r} resolved to {type(resolved).__name__}, which is not a sink"
                )
            return resolved
        return _by_extension(Path(obj))
    if isinstance(obj, Path):
        return _by_extension(obj)
    if callable(obj):
        return PythonSink(obj)
    raise TypeError(f"cannot use {type(obj).__name__} as a sink")
