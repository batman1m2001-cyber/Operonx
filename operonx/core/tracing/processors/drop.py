"""Filtering processors — drop events from the stream.

All four are stateless one-pass filters. Each maps `Iterable[TraceEvent]`
to a smaller subset. Composable; order matters only for performance (cheap
drops first save downstream work).

See ``docs/TRACING_REDESIGN_PLAN.md`` §3.3.
"""

from __future__ import annotations

from typing import Collection, Iterable, Optional

from operonx.core.tracing.events import EventKind, TraceEvent


class DropOps:
    """Drop events whose ``op_name`` is in the configured set.

    Replaces ``TraceFilter.exclude_ops``. Matches by exact full_name (e.g.
    ``"g.picker"``); user can pass either bare names or qualified names.
    """

    __slots__ = ("_names",)

    def __init__(self, names: Collection[str]) -> None:
        self._names = frozenset(names)

    def __call__(self, events: Iterable[TraceEvent]) -> Iterable[TraceEvent]:
        names = self._names
        for e in events:
            if e.op_name is not None and (e.op_name in names or _short_name(e.op_name) in names):
                continue
            yield e


class KeepOps:
    """Keep only events whose ``op_name`` is in the configured set.

    Synthetic events (``op_name=None`` — group_start/group_end) always pass.
    Replaces ``TraceFilter.include_ops``.
    """

    __slots__ = ("_names",)

    def __init__(self, names: Collection[str]) -> None:
        self._names = frozenset(names)

    def __call__(self, events: Iterable[TraceEvent]) -> Iterable[TraceEvent]:
        names = self._names
        for e in events:
            if e.op_name is None:
                yield e
            elif e.op_name in names or _short_name(e.op_name) in names:
                yield e


class DropKinds:
    """Drop events whose ``kind`` is in the configured set.

    Replaces ``TraceFilter.exclude_kinds``. Useful for stripping noisy
    kinds the exporter doesn't render (rare in v1 — most kinds are useful).
    """

    __slots__ = ("_kinds",)

    def __init__(self, kinds: Collection[EventKind]) -> None:
        self._kinds = frozenset(kinds)

    def __call__(self, events: Iterable[TraceEvent]) -> Iterable[TraceEvent]:
        kinds = self._kinds
        for e in events:
            if e.kind in kinds:
                continue
            yield e


class DropEmpty:
    """Drop ``OP_END`` events whose outputs are all-None / empty.

    Replaces ``TraceFilter.skip_empty``. Pre-yields all non-OP_END events
    untouched; for OP_END, skips when ``payload["outputs"]`` is empty or
    every value is None.
    """

    def __call__(self, events: Iterable[TraceEvent]) -> Iterable[TraceEvent]:
        for e in events:
            if e.kind is EventKind.OP_END:
                outputs = e.payload.get("outputs") or {}
                if not outputs or all(v is None for v in outputs.values()):
                    continue
            yield e


def _short_name(full_name: Optional[str]) -> str:
    """``"g.picker"`` → ``"picker"``. Allows users to write ``DropOps(["picker"])``
    without knowing the graph qualifier."""
    if not full_name:
        return ""
    return full_name.rsplit(".", 1)[-1]
