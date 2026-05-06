"""PII / secrets redaction processor.

Replaces matching keys with a marker string. Walks payload dicts one level
deep (same as TruncateIO — keeps the hot path fast).
"""

from __future__ import annotations

from typing import Any, Collection, Dict, Iterable

from operonx.core.tracing.events import TraceEvent


class RedactKeys:
    """Replace values for matching keys with ``<redacted>`` (or custom marker).

    Match is by exact key name in any payload dict that's a top-level value
    of a known I/O key (``inputs``, ``outputs``, ``yielded``).
    """

    __slots__ = ("_keys", "marker")

    def __init__(self, keys: Collection[str], marker: str = "<redacted>") -> None:
        self._keys = frozenset(keys)
        self.marker = marker

    def __call__(self, events: Iterable[TraceEvent]) -> Iterable[TraceEvent]:
        keys = self._keys
        marker = self.marker
        for e in events:
            payload = e.payload
            if payload:
                for io_key in ("inputs", "outputs", "yielded", "value"):
                    io = payload.get(io_key)
                    if isinstance(io, dict):
                        _redact_dict_inplace(io, keys, marker)
            yield e


def _redact_dict_inplace(d: Dict[str, Any], keys: frozenset, marker: str) -> None:
    """Walk one level, redact values whose key is in the set."""
    for k in d.keys() & keys:
        d[k] = marker
