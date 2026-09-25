"""Where a job's items come from.

A source is anything with an async ``items()``. Three ship — a JSONL
file, a CSV file, and "whatever Python hands me" — and they are enough
to prove the seam: a table or a queue is one more class against the same
one-method protocol, added where that backend's client already lives.

A source is a *resource*. ``resources.yaml`` declares what a project
reaches out to, and a folder or a table is that::

    source:calls_today:
      kind: jsonl
      path: data/calls.jsonl

so ``Job(source="source:calls_today")`` resolves through the hub the way
``trace="trace_local:default"`` does. Handing a path, a list or a
generator function straight to the Job works too — the same
:func:`as_source` turns each into a source.
"""

from __future__ import annotations

import csv
import inspect
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any, AsyncIterator, ClassVar, Dict, Optional, Protocol, runtime_checkable

from pydantic import Field

from operonx.core.utils import YamlModel

from ._keys import is_resource_key

__all__ = [
    "Source",
    "JsonlSource",
    "CsvSource",
    "PythonSource",
    "SourceConfig",
    "as_source",
    "create_source",
    "open_source",
]


@runtime_checkable
class Source(Protocol):
    """One method. Yield the items, then stop; stopping *is* end-of-input."""

    def items(self) -> AsyncIterator[Any]: ...


class JsonlSource:
    """One JSON value per line. Blank lines are skipped, a bad line names
    its number rather than surfacing as a decode error from nowhere."""

    def __init__(self, path: str | Path):
        self.path = Path(path)

    async def items(self) -> AsyncIterator[Any]:
        with self.path.open("r", encoding="utf-8") as fh:
            for n, line in enumerate(fh, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    yield json.loads(line)
                except json.JSONDecodeError as exc:
                    raise ValueError(f"{self.path}:{n}: not JSON — {exc.msg}") from None

    def __repr__(self) -> str:
        return f"jsonl({self.path})"


class CsvSource:
    """Header row names the fields; every item is a dict of strings."""

    def __init__(self, path: str | Path, **reader_options: Any):
        self.path = Path(path)
        self.reader_options = reader_options

    async def items(self) -> AsyncIterator[Dict[str, str]]:
        with self.path.open("r", encoding="utf-8", newline="") as fh:
            for row in csv.DictReader(fh, **self.reader_options):
                yield dict(row)

    def __repr__(self) -> str:
        return f"csv({self.path})"


class PythonSource:
    """Anything Python can iterate.

    A list, an iterator, an async iterator, a function returning any of
    those (sync or async), or a ``"module:attr"`` string naming one. A
    function is called once per run, so a generator function is the
    natural way to write a source that reads lazily.
    """

    def __init__(self, obj: Any):
        self._obj = obj

    async def items(self) -> AsyncIterator[Any]:
        obj = self._obj
        if isinstance(obj, str):
            from operonx.core.serve.registry import load_object

            obj = load_object(obj, field="source")
        if callable(obj) and not hasattr(obj, "__iter__") and not hasattr(obj, "__aiter__"):
            obj = obj()
        if inspect.isawaitable(obj):
            obj = await obj
        if hasattr(obj, "__aiter__"):
            async for item in obj:
                yield item
            return
        if hasattr(obj, "__iter__") and not isinstance(obj, (str, bytes)):
            for item in obj:
                yield item
            return
        raise TypeError(f"source {self._obj!r} is not iterable")

    def __repr__(self) -> str:
        obj = self._obj
        name = obj if isinstance(obj, str) else getattr(obj, "__name__", type(obj).__name__)
        return f"python({name})"


# -- as a resource ---------------------------------------------------------


class SourceConfig(YamlModel):
    """A ``source:<name>`` block in resources.yaml.

    ``kind`` picks the class; ``path`` serves the file kinds and ``entry``
    the python one; anything else goes to the class as keyword options.
    """

    _category: ClassVar[str] = "source"

    kind: str
    path: Optional[str] = None
    entry: Optional[str] = None
    options: Dict[str, Any] = Field(default_factory=dict)


def open_source(
    kind: str, *, path: Optional[str] = None, entry: Optional[str] = None, **options: Any
) -> Source:
    """Build a source by kind. The one place the names are spelled out."""
    if kind == "jsonl":
        if not path:
            raise ValueError("source kind 'jsonl' needs `path`")
        return JsonlSource(path)
    if kind == "csv":
        if not path:
            raise ValueError("source kind 'csv' needs `path`")
        return CsvSource(path, **options)
    if kind == "python":
        if not entry:
            raise ValueError("source kind 'python' needs `entry` (module:attr)")
        return PythonSource(entry)
    raise ValueError(f"unknown source kind {kind!r} (have: jsonl, csv, python)")


def create_source(config: SourceConfig) -> Source:
    """The factory the resource registry calls."""
    return open_source(config.kind, path=config.path, entry=config.entry, **config.options)


def _is_source(obj: Any) -> bool:
    # `runtime_checkable` would accept a dict — it has `.items` — which is
    # exactly the value a caller most plausibly passes by mistake.
    return (
        not isinstance(obj, Mapping)
        and callable(getattr(obj, "items", None))
        and not isinstance(obj, (str, bytes, Path))
    )


def _by_extension(path: Path) -> Source:
    ext = path.suffix.lower()
    if ext == ".jsonl":
        return JsonlSource(path)
    if ext == ".csv":
        return CsvSource(path)
    raise ValueError(
        f"cannot tell a source from {path}: expected .jsonl or .csv, "
        "or declare it under `source:` in resources.yaml"
    )


def as_source(obj: Any) -> Source:
    """Turn what a Job was given into a source.

    * a :class:`Source` — as is;
    * ``"source:name"`` — resolved through the resource hub;
    * a path (str or Path) — by extension;
    * anything else — :class:`PythonSource`.
    """
    if obj is None:
        raise TypeError(
            "a job needs a source: a `source:` resource key, a file path, or an iterable"
        )
    if _is_source(obj):
        return obj
    if isinstance(obj, str):
        if is_resource_key(obj):
            from operonx.core.jobs import register
            from operonx.core.registry import ResourceHub

            register()
            resolved = ResourceHub.instance().get(obj)
            if not _is_source(resolved):
                raise TypeError(
                    f"{obj!r} resolved to {type(resolved).__name__}, which is not a source"
                )
            return resolved
        return _by_extension(Path(obj))
    if isinstance(obj, Path):
        return _by_extension(obj)
    return PythonSource(obj)
