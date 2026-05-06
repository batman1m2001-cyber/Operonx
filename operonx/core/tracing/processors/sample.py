"""Sampling processor — keep a random subset of calls by request_id.

Decision is per ``request_id``: an entire call's events are either kept or
dropped together. Hashing the request_id to a stable [0, 1) float and
comparing against rate gives deterministic sampling — same request_id
always lands the same way, useful for reproducing prod issues.
"""

from __future__ import annotations

import hashlib
from typing import Iterable

from operonx.core.tracing.events import TraceEvent


class Sample:
    """Keep events for a fraction of request_ids; drop the rest entirely.

    ``rate=1.0`` keeps all (no-op), ``rate=0.0`` drops all, ``rate=0.1``
    keeps ~10% of calls. Decision is stable per request_id.
    """

    __slots__ = ("rate",)

    def __init__(self, rate: float = 1.0) -> None:
        if not 0.0 <= rate <= 1.0:
            raise ValueError(f"rate must be in [0.0, 1.0], got {rate}")
        self.rate = rate

    def __call__(self, events: Iterable[TraceEvent]) -> Iterable[TraceEvent]:
        rate = self.rate
        if rate >= 1.0:
            yield from events
            return
        if rate <= 0.0:
            return
        # Cache decisions per request_id — events from the same call share state.
        decisions: dict[str, bool] = {}
        for e in events:
            req_id = e.request_id
            keep = decisions.get(req_id)
            if keep is None:
                keep = _hash_unit(req_id) < rate
                decisions[req_id] = keep
            if keep:
                yield e


def _hash_unit(s: str) -> float:
    """Hash a string to [0.0, 1.0) deterministically.

    Uses MD5 (fast, plenty for sampling — not a security boundary).
    """
    h = hashlib.md5(s.encode("utf-8")).digest()
    # Use first 8 bytes as uint64, divide by 2^64
    n = int.from_bytes(h[:8], "big")
    return n / (1 << 64)
