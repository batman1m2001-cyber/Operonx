"""How long runs are kept, per origin.

A policy maps an origin to days, or to ``None`` for "forever"; an origin
the policy does not name is kept. The defaults are the platform plan's:
services 30 days, jobs and evals forever, the playground 7 days, ad hoc
30 days. A project overrides them (the studio's Settings writes
``[studio.retention]`` in ``operonx.toml``); :func:`apply_retention`
enforces whichever applies.
"""

from __future__ import annotations

import time
from typing import Dict, Mapping, Optional

from .base import RunStore
from .model import RunFilter

__all__ = ["DEFAULT_RETENTION", "apply_retention", "plan_retention"]

DEFAULT_RETENTION: Dict[str, Optional[float]] = {
    "service": 30,
    "job": None,
    "eval": None,
    "playground": 7,
    "adhoc": 30,
}


def _cutoffs(policy: Mapping[str, Optional[float]], now: float) -> Dict[str, float]:
    out = {}
    for origin, days in policy.items():
        if days is None:
            continue
        days = float(days)
        if days < 0:
            raise ValueError(f"retention for {origin!r} is {days} days; must be >= 0 or None")
        out[origin] = now - days * 86400.0
    return out


def plan_retention(
    store: RunStore,
    policy: Optional[Mapping[str, Optional[float]]] = None,
    now: Optional[float] = None,
) -> Dict[str, int]:
    """How many runs per origin *policy* would delete — without deleting."""
    now = time.time() if now is None else now
    policy = DEFAULT_RETENTION if policy is None else policy
    return {
        origin: store.count(RunFilter(origin=origin, until=cut))
        for origin, cut in _cutoffs(policy, now).items()
    }


def apply_retention(
    store: RunStore,
    policy: Optional[Mapping[str, Optional[float]]] = None,
    now: Optional[float] = None,
) -> Dict[str, int]:
    """Delete runs older than *policy* allows; return how many per origin."""
    now = time.time() if now is None else now
    policy = DEFAULT_RETENTION if policy is None else policy
    if not store.writable:
        return {}
    return {
        origin: store.delete_runs(RunFilter(origin=origin, until=cut))
        for origin, cut in _cutoffs(policy, now).items()
    }
