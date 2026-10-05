"""Alerts on runs: a threshold per service or job, checked on the summaries.

An alert watches one origin and name (``service:call``, ``job:score_calls``)
over a trailing window and fires when a number crosses its threshold:

* ``error_rate`` — the share of runs that failed;
* ``p95_ms`` — the 95th-percentile run duration, or one op's when ``op`` is
  set (a key op: time to first audio, the LLM call);
* ``cost_per_hour`` — priced cost in the window, per hour (unpriced runs
  are counted beside it, never as $0);
* ``runs`` — fires when there are *fewer* than the threshold (a service that
  went quiet);
* ``score_mean:<score>`` — the mean of a score an online eval wrote for those
  runs; fires when it *drops below* the threshold (quality going down);
* ``score_fail_rate:<score>`` — the share of those scores that failed.

The score metrics read a score store (``evaluate(..., scores=)``) over the
scores written in the window; ``min_runs`` counts scores there. It is evaluated from what the run store already keeps — summaries and op
rollups, no extra recording. ``min_runs`` keeps a window of two runs from
paging anyone. :func:`step` turns an evaluation into what to send: a
``firing`` once when it crosses, a reminder every ``repeat_min`` while it
stays over, a ``resolved`` when it comes back. :func:`deliver` posts the
message to a webhook as ``{"text": …}`` plus the fields — the shape Slack
and Teams incoming webhooks take, and any relay can read.
"""

from __future__ import annotations

import json
import time
import urllib.request
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, Optional

from .base import RunStore
from .model import RunFilter, percentile

__all__ = [
    "METRICS",
    "SCORE_METRICS",
    "Alert",
    "AlertState",
    "deliver",
    "evaluate",
    "message",
    "step",
]

METRICS = ("error_rate", "p95_ms", "cost_per_hour", "runs")
#: Metrics over a score, named ``<metric>:<score name>``.
SCORE_METRICS = ("score_mean", "score_fail_rate")


def score_metric(metric: str) -> Optional[tuple]:
    """``("score_mean", "polite")`` for ``"score_mean:polite"``; ``None``
    for a run metric."""
    kind, sep, name = metric.partition(":")
    return (kind, name) if sep and kind in SCORE_METRICS else None


@dataclass
class Alert:
    name: str
    origin: str
    target: str
    metric: str
    threshold: float
    op: Optional[str] = None
    window_min: float = 15.0
    min_runs: int = 5
    repeat_min: float = 60.0
    webhook: str = ""
    enabled: bool = True

    def __post_init__(self) -> None:
        scored = score_metric(self.metric)
        if scored is not None and not scored[1]:
            raise ValueError(
                f"alert {self.name!r}: {self.metric!r} names no score; "
                f"write {scored[0]}:<score name>"
            )
        if self.metric not in METRICS and scored is None:
            raise ValueError(
                f"alert {self.name!r}: metric is one of {', '.join(METRICS)}, "
                f"or {' / '.join(f'{m}:<score name>' for m in SCORE_METRICS)}"
            )
        if self.metric != "p95_ms" and self.op:
            raise ValueError(f"alert {self.name!r}: `op` narrows p95_ms only")
        self.threshold = float(self.threshold)
        self.window_min = max(1.0, float(self.window_min))
        self.min_runs = max(0, int(self.min_runs))

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Alert":
        known = set(cls.__dataclass_fields__)  # type: ignore[attr-defined]
        return cls(**{k: v for k, v in d.items() if k in known})

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class AlertState:
    """What an evaluation found, and what has been sent about it."""

    firing: bool = False
    value: Optional[float] = None
    runs: int = 0
    unpriced: int = 0
    since: float = 0.0
    until: float = 0.0
    evaluated_at: float = 0.0
    fired_at: Optional[float] = None
    sent_at: Optional[float] = None
    note: str = ""
    extra: Dict[str, Any] = field(default_factory=dict)


def evaluate(
    store: RunStore, alert: Alert, now: Optional[float] = None, *, scores: Any = None
) -> AlertState:
    """The alert's number over its window, and whether it crosses. A score
    metric reads *scores* (a ScoreStore)."""
    now = time.time() if now is None else now
    since = now - alert.window_min * 60
    scored = score_metric(alert.metric)
    if scored is not None:
        return _evaluate_scores(scores, alert, scored, since, now)
    f = RunFilter(origin=alert.origin, name=alert.target, since=since, until=now)
    runs = store.list_runs(f, "started_desc", 5000).items
    st = AlertState(runs=len(runs), since=since, until=now, evaluated_at=now)
    if alert.metric == "runs":
        st.value = float(len(runs))
        st.firing = len(runs) < alert.threshold
        return st
    if len(runs) < max(1, alert.min_runs):
        st.note = f"{len(runs)} runs in the window — fewer than {alert.min_runs}, not judged"
        return st
    if alert.metric == "error_rate":
        st.value = sum(1 for r in runs if r.status == "error") / len(runs)
    elif alert.metric == "p95_ms":
        if alert.op:
            rolls = [r for r in store.rollups(f) if r.op == alert.op]
            samples = [x for r in rolls for x in (r.samples or [])] or [r.max_ms for r in rolls]
            if not samples:
                st.note = f"no {alert.op} in the window"
                return st
            st.value = percentile(samples, 95)
        else:
            st.value = percentile([r.duration_ms for r in runs], 95)
    elif alert.metric == "cost_per_hour":
        priced = [r.cost_usd for r in runs if r.cost_usd is not None]
        st.unpriced = sum(1 for r in runs if r.cost_usd is None)
        if not priced:
            st.note = "nothing in the window was priced"
            return st
        st.value = sum(priced) / (alert.window_min / 60)
    st.firing = st.value is not None and st.value > alert.threshold
    return st


def _evaluate_scores(
    scores: Any, alert: Alert, scored: tuple, since: float, now: float
) -> AlertState:
    st = AlertState(since=since, until=now, evaluated_at=now)
    if scores is None:
        st.note = f"{alert.metric} reads a score store, and none was given"
        return st
    from operonx.telemetry.scores import ScoreFilter

    kind, name = scored
    where = ScoreFilter(
        score_name=name, origin=alert.origin, name=alert.target, since=since, until=now
    )
    rows = scores.scores(where)
    if kind == "score_fail_rate":
        rows = [s for s in rows if s.passed is not None]
    else:
        rows = [s for s in rows if s.value is not None]
    st.runs = len(rows)
    if len(rows) < max(1, alert.min_runs):
        st.note = f"{len(rows)} {name} scores in the window — fewer than {alert.min_runs}, not judged"
        return st
    if kind == "score_fail_rate":
        st.value = sum(1 for s in rows if not s.passed) / len(rows)
        st.firing = st.value > alert.threshold
    else:
        st.value = sum(float(s.value) for s in rows) / len(rows)
        st.firing = st.value < alert.threshold
    return st


def step(alert: Alert, prev: Optional[AlertState], cur: AlertState) -> Optional[str]:
    """What to send now: ``"firing"``, ``"reminder"``, ``"resolved"`` or
    nothing. Carries ``fired_at`` / ``sent_at`` over from *prev* into *cur*."""
    was = bool(prev and prev.firing)
    if prev is not None:
        cur.fired_at, cur.sent_at = prev.fired_at, prev.sent_at
    if cur.firing and not was:
        cur.fired_at = cur.sent_at = cur.evaluated_at
        return "firing"
    if cur.firing and was:
        if cur.sent_at is None or cur.evaluated_at - cur.sent_at >= alert.repeat_min * 60:
            cur.sent_at = cur.evaluated_at
            return "reminder"
        return None
    if was and not cur.firing and cur.value is not None:
        cur.fired_at = None
        cur.sent_at = cur.evaluated_at
        return "resolved"
    return None


def _fmt(metric: str, value: Optional[float]) -> str:
    if value is None:
        return "—"
    if metric == "error_rate":
        return f"{100 * value:.1f}%"
    if metric == "p95_ms":
        return f"{value:.0f} ms"
    if metric == "cost_per_hour":
        return f"${value:.4f}/h"
    return f"{value:g}"


def message(
    alert: Alert, st: AlertState, kind: str, *, project: str = "", link: str = ""
) -> Dict[str, Any]:
    """The webhook body: a ``text`` line people read, and the fields."""
    what = (
        f"p95 of {alert.op}"
        if alert.op
        else {
            "error_rate": "error rate",
            "p95_ms": "p95 duration",
            "cost_per_hour": "cost per hour",
            "runs": "runs",
        }[alert.metric]
    )
    head = {"firing": "FIRING", "reminder": "STILL FIRING", "resolved": "RESOLVED", "test": "TEST"}[
        kind
    ]
    cmp = "<" if alert.metric == "runs" else ">"
    text = (
        f"[{head}] {project + ' · ' if project else ''}{alert.origin} {alert.target}: {what} "
        f"{_fmt(alert.metric, st.value)} ({cmp} {_fmt(alert.metric, alert.threshold)}) over the last "
        f"{alert.window_min:g} min, {st.runs} runs" + (f" — {link}" if link else "")
    )
    return {
        "text": text,
        "alert": alert.name,
        "state": kind,
        "metric": alert.metric,
        "op": alert.op,
        "value": st.value,
        "threshold": alert.threshold,
        "runs": st.runs,
        "since": st.since,
        "until": st.until,
        "origin": alert.origin,
        "target": alert.target,
        "project": project,
        "link": link,
    }


def deliver(webhook: str, body: Dict[str, Any], timeout: float = 5.0) -> int:
    """POST *body* as JSON to *webhook*; the HTTP status."""
    if not webhook.startswith(("http://", "https://")):
        raise ValueError("a webhook is an http(s) URL")
    req = urllib.request.Request(
        webhook,
        data=json.dumps(body).encode("utf-8"),
        method="POST",
        headers={"content-type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as res:
        return int(res.status)
