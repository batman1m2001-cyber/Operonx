"""What a job loops over: ``Job(items=...)``.

Three forms, nothing else:

* **a function** returning an iterable or an async iterable — a generator
  function is the usual one. It is called on every run, so a job that
  runs again reads its data again. This is the custom loader;
* **an iterable or an async iterable** — a list, or any object with
  ``__iter__`` / ``__aiter__``;
* **a path to a ``.jsonl`` file** — one item per line.

``None`` is one empty item: a job that does one thing once (create a
table, write a report) runs its graph a single time.

A synchronous iterator is advanced in a worker thread, one item at a
time, so a loader that reads a database or a disk does not stall the
runs already in flight.
"""

from __future__ import annotations

import asyncio
import inspect
import json
from pathlib import Path
from typing import Any, AsyncIterator, Optional

__all__ = ["check_items", "iter_items"]

_END = object()


def check_items(items: Any, job: str) -> None:
    """Refuse at declaration what `iter_items` could never read."""
    if items is None or callable(items):
        return
    if isinstance(items, (str, Path)):
        if Path(items).suffix.lower() != ".jsonl":
            raise ValueError(
                f"job {job!r}: items={str(items)!r} — a path must be a .jsonl file; "
                "for anything else pass a function that yields the items"
            )
        return
    if isinstance(items, (dict, bytes)):
        raise TypeError(
            f"job {job!r}: items is a {type(items).__name__}; pass a list of items, "
            "or a function that yields them"
        )
    if not (hasattr(items, "__iter__") or hasattr(items, "__aiter__")):
        raise TypeError(
            f"job {job!r}: items is a {type(items).__name__} — pass a list, an iterable, "
            "a function that yields the items, or a .jsonl path"
        )


def _jsonl(path: Path):
    with path.open("r", encoding="utf-8") as fh:
        for n, line in enumerate(fh, 1):
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path}:{n}: not JSON ({exc.msg})") from None


async def iter_items(items: Any) -> AsyncIterator[Any]:
    """Every item, in order, as an async iterator."""
    if items is None:
        yield {}
        return
    source: Optional[Any] = items
    if isinstance(items, (str, Path)):
        source = _jsonl(Path(items))
    elif callable(items) and not (hasattr(items, "__iter__") or hasattr(items, "__aiter__")):
        source = items()
        if inspect.isawaitable(source):
            source = await source
    if hasattr(source, "__aiter__"):
        async for item in source:
            yield item
        return
    it = iter(source)
    while True:
        item = await asyncio.to_thread(next, it, _END)
        if item is _END:
            return
        yield item
