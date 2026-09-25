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
    "SinkConfig",
    "as_sink",
    "create_sink",
    "open_sink",
]

#: Where the item's key lands when it is written into a row.
KEY_FIELD = "_key"


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
    """One JSON object per line, opened on the first write.

    ``mode="append"`` is the default so two runs of the same job add to
    one file; ``"overwrite"`` starts it fresh. Writes are flushed one at
    a time: a run that is killed still leaves every item it finished.
    """

    def __init__(
        self, path: str | Path, *, mode: str = "append", key_field: Optional[str] = KEY_FIELD
    ):
        if mode not in ("append", "overwrite"):
            raise ValueError(f"mode must be 'append' or 'overwrite', not {mode!r}")
        self.path = Path(path)
        self.mode = mode
        self.key_field = key_field
        self._fh = None
        self.written = 0

    def _open(self):
        if self._fh is None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._fh = self.path.open("a" if self.mode == "append" else "w", encoding="utf-8")
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
    column — a CSV with a ragged row is worse than one with a hole."""

    def __init__(
        self, path: str | Path, *, mode: str = "append", key_field: Optional[str] = KEY_FIELD
    ):
        if mode not in ("append", "overwrite"):
            raise ValueError(f"mode must be 'append' or 'overwrite', not {mode!r}")
        self.path = Path(path)
        self.mode = mode
        self.key_field = key_field
        self._fh = None
        self._writer = None
        self.written = 0

    async def write(self, key: str, item: Any) -> None:
        row = _as_row(key, item, self.key_field)
        if self._writer is None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            fresh = (
                self.mode == "overwrite" or not self.path.exists() or self.path.stat().st_size == 0
            )
            self._fh = self.path.open(
                "a" if self.mode == "append" else "w", encoding="utf-8", newline=""
            )
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
            from operonx.core.serve.registry import load_object

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
    mode: str = "append"
    options: Dict[str, Any] = Field(default_factory=dict)


def open_sink(
    kind: str,
    *,
    path: Optional[str] = None,
    entry: Optional[str] = None,
    mode: str = "append",
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
    raise ValueError(f"unknown sink kind {kind!r} (have: jsonl, csv, python, null)")


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
    ext = path.suffix.lower()
    if ext == ".jsonl":
        return JsonlSink(path)
    if ext == ".csv":
        return CsvSink(path)
    raise ValueError(
        f"cannot tell a sink from {path}: expected .jsonl or .csv, "
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
            from operonx.core.jobs import register
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
