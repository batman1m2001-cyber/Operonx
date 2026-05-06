"""Truncation processor — clip large strings/bytes in event payloads.

Replaces ``TraceFilter.max_io_size``. Walks the payload of each event,
truncates any string/bytes value over ``max_bytes``. Mutates the payload
dict in place since payloads are dicts (events themselves are frozen
dataclasses but their payload dicts are mutable — pragmatic, avoids a
full event copy on every truncation).
"""

from __future__ import annotations

from typing import Any, Dict, Iterable

from operonx.core.tracing.events import TraceEvent


class TruncateIO:
    """Clip strings/bytes in event payload to ``max_bytes``.

    Targets common payload keys (``inputs``, ``outputs``, ``yielded``).
    Truncated values are replaced with ``"<value>...[truncated N bytes]"``.
    Only top-level dict values inside those keys are inspected — deep
    nested structures are walked one level.
    """

    __slots__ = ("max_bytes",)

    def __init__(self, max_bytes: int = 2000) -> None:
        self.max_bytes = max_bytes

    def __call__(self, events: Iterable[TraceEvent]) -> Iterable[TraceEvent]:
        max_bytes = self.max_bytes
        for e in events:
            payload = e.payload
            if not payload:
                yield e
                continue
            # Only walk known I/O keys to keep this fast on the hot path.
            for io_key in ("inputs", "outputs", "yielded", "value"):
                io = payload.get(io_key)
                if isinstance(io, dict):
                    _truncate_dict_inplace(io, max_bytes)
                elif isinstance(io, (str, bytes)):
                    payload[io_key] = _truncate_value(io, max_bytes)
            yield e


def _truncate_dict_inplace(d: Dict[str, Any], max_bytes: int) -> None:
    """Walk one level of a dict, truncating string/bytes values that exceed
    ``max_bytes``. Top-level only — deep nesting is not chased to keep this
    O(n) in immediate values."""
    for k, v in d.items():
        if isinstance(v, (str, bytes)) and len(v) > max_bytes:
            d[k] = _truncate_value(v, max_bytes)


def _truncate_value(v: Any, max_bytes: int) -> str:
    """Build the truncation marker string. Returned as ``str`` regardless of
    input type — exporters serialize as text."""
    head = v[:max_bytes]
    if isinstance(head, bytes):
        head = head.decode("utf-8", errors="replace")
    full_len = len(v)
    return f"{head}...[truncated {full_len - max_bytes} bytes]"
