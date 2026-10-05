"""Alerts on scores (EVALS_PLAN §14 D75): an online eval's quality, watched
like a service's error rate — the same rules, state machine and webhooks."""

from __future__ import annotations

import pytest

from operonx.telemetry.runs.alerts import Alert, evaluate, step
from operonx.telemetry.runs.sqlite import SqliteRunStore
from operonx.telemetry.scores import Score, open_score_store

NOW = 10_000.0


@pytest.fixture
def runs(tmp_path):
    return SqliteRunStore(path=tmp_path / "runs.sqlite")


@pytest.fixture
def scores(tmp_path):
    return open_score_store({"backend": "sqlite", "path": str(tmp_path / "scores.sqlite")})


def _judged(scores, passed, *, name="polite", at=NOW - 60, value=None, service="call"):
    rows = [
        Score(
            score_name=name,
            target="trace",
            source="judge",
            data_type="numeric" if value is not None else "bool",
            passed=p,
            value=value,
            trace_id=f"{service}-{name}-{i}-{at}",
            origin="service",
            name=service,
            evaluator_version="v1",
            created_at=at,
        )
        for i, p in enumerate(passed)
    ]
    scores.put_scores(rows)


def _alert(metric, threshold, **kw):
    return Alert(
        name="q", origin="service", target="call", metric=metric, threshold=threshold, **kw
    )


def test_a_fail_rate_fires_above_its_threshold_and_resolves(runs, scores):
    _judged(scores, [True] * 6 + [False] * 4)
    _judged(scores, [False] * 10, service="other")  # another service's scores do not count
    alert = _alert("score_fail_rate:polite", 0.3)

    st = evaluate(runs, alert, NOW, scores=scores)
    assert st.value == pytest.approx(0.4) and st.firing and st.runs == 10
    assert step(alert, None, st) == "firing"

    later = NOW + 3600  # the window moved past the failures
    _judged(scores, [True] * 10, at=later - 60)
    st2 = evaluate(runs, alert, later, scores=scores)
    assert st2.value == 0.0 and not st2.firing
    assert step(alert, st, st2) == "resolved"


def test_a_mean_fires_when_it_drops_below(runs, scores):
    for v in (0.9, 0.8, 0.4, 0.3, 0.2):
        _judged(scores, [None], name="helpful", value=v, at=NOW - 60 - v)
    st = evaluate(runs, _alert("score_mean:helpful", 0.6), NOW, scores=scores)

    assert st.value == pytest.approx(0.52) and st.firing


def test_too_few_scores_are_not_judged(runs, scores):
    _judged(scores, [False, False])
    st = evaluate(runs, _alert("score_fail_rate:polite", 0.1), NOW, scores=scores)

    assert st.value is None and not st.firing and "fewer than 5" in st.note


def test_a_score_metric_needs_a_score_store_and_a_name(runs):
    st = evaluate(runs, _alert("score_mean:polite", 0.5), NOW)
    assert not st.firing and "score store" in st.note
    with pytest.raises(ValueError, match="score_mean:<score name>"):
        _alert("score_mean:", 0.5)
    with pytest.raises(ValueError, match="metric is one of"):
        _alert("score_median:polite", 0.5)
