"""Alerts on runs (`operonx.telemetry.runs.alerts`) — P9.

Gates: each metric is computed from the store's summaries over the
trailing window of one service — error rate, p95 of runs or of one op,
cost per hour (unpriced never counted as $0), too few runs; a window
under ``min_runs`` is not judged; a crossing sends ``firing`` once, a
``reminder`` only after ``repeat_min``, ``resolved`` when it comes back;
the message reads as a sentence and is posted to a webhook as JSON.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from operonx.core.workflow_trace import OpExecution, WorkflowTrace
from operonx.telemetry.runs.alerts import Alert, AlertState, deliver, evaluate, message, step
from operonx.telemetry.runs.sqlite import SqliteRunStore

NOW = 1_790_500_000.0


def _run(tid, age_s, *, ms=100.0, error=False, cost=0.001, llm_ms=50.0, service="call"):
    t0 = 10.0
    nodes = [
        OpExecution(
            op_id=f"g.llm#{tid}",
            op_name="llm",
            op_full_name="g.llm",
            ctx=("main",),
            start_time=t0,
            end_time=t0 + llm_ms / 1000,
            inputs={},
            upstreams=[],
            op_type="llm",
            outputs={
                "content": "x",
                "cost_usd": cost,
                "usage": {"prompt_tokens": 1, "completion_tokens": 1},
            },
        ),
        OpExecution(
            op_id=f"g.out#{tid}",
            op_name="out",
            op_full_name="g.out",
            ctx=("main",),
            start_time=t0,
            end_time=t0 + ms / 1000,
            inputs={},
            outputs={},
            upstreams=[],
            status="error" if error else "ok",
            error="boom" if error else None,
        ),
    ]
    return WorkflowTrace(
        trace_id=tid,
        workflow_name="flow",
        started_at=t0,
        ended_at=t0 + ms / 1000,
        nodes=nodes,
        metadata={"origin": "service", "service": service},
        wall_started_at=NOW - age_s,
    )


@pytest.fixture()
def store(tmp_path):
    s = SqliteRunStore(path=tmp_path / "runs.sqlite")
    for i in range(8):  # the last 15 minutes: 8 calls, 2 failing, one slow LLM
        s.consume(
            _run(
                f"c{i}",
                60 * (i + 1),
                error=i in (1, 5),
                llm_ms=900 if i == 3 else 50,
                cost=None if i == 7 else 0.002,
            )
        )
    s.consume(_run("old", 3600, error=True))  # outside the window
    s.consume(_run("x", 60, error=True, service="other"))  # another service
    return s


def _alert(**kw):
    base = dict(
        name="a", origin="service", target="call", metric="error_rate", threshold=0.1, window_min=15
    )
    base.update(kw)
    return Alert(**base)


def test_each_metric_over_the_window(store):
    st = evaluate(store, _alert(), NOW)
    assert st.runs == 8 and st.value == pytest.approx(0.25) and st.firing
    assert not evaluate(store, _alert(threshold=0.3), NOW).firing

    p95 = evaluate(store, _alert(metric="p95_ms", threshold=500), NOW)
    assert p95.value == pytest.approx(100, abs=1) and not p95.firing
    llm = evaluate(store, _alert(metric="p95_ms", op="llm", threshold=500), NOW)
    assert llm.value > 500 and llm.firing  # the slow call shows in the op's p95

    cost = evaluate(store, _alert(metric="cost_per_hour", threshold=0.1), NOW)
    assert cost.value == pytest.approx(7 * 0.002 * 4) and cost.unpriced == 1 and not cost.firing

    quiet = evaluate(store, _alert(metric="runs", threshold=20), NOW)
    assert quiet.value == 8 and quiet.firing  # fewer runs than expected: the service went quiet


def test_a_thin_window_is_not_judged(store):
    st = evaluate(store, _alert(window_min=2.5, min_runs=5), NOW)
    assert st.runs == 2 and st.value is None and not st.firing and "not judged" in st.note


def test_firing_reminding_resolving():
    a = _alert(repeat_min=60)
    s1 = AlertState(firing=True, value=0.3, evaluated_at=1000)
    assert step(a, None, s1) == "firing" and s1.fired_at == 1000
    s2 = AlertState(firing=True, value=0.3, evaluated_at=1000 + 600)
    assert step(a, s1, s2) is None  # still over, but no reminder yet
    s3 = AlertState(firing=True, value=0.4, evaluated_at=1000 + 3700)
    assert step(a, s2, s3) == "reminder" and s3.sent_at == 4700
    s4 = AlertState(firing=False, value=0.01, evaluated_at=5000)
    assert step(a, s3, s4) == "resolved" and s4.fired_at is None
    assert step(a, s4, AlertState(firing=False, value=0.0, evaluated_at=5100)) is None


def test_messages_and_webhooks():
    got = []

    class H(BaseHTTPRequestHandler):
        def do_POST(self):
            got.append(json.loads(self.rfile.read(int(self.headers["content-length"]))))
            self.send_response(200)
            self.end_headers()

        def log_message(self, *a):
            pass

    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        a = _alert()
        body = message(
            a,
            AlertState(firing=True, value=0.25, runs=8),
            "firing",
            project="callbot",
            link="http://studio/p/x",
        )
        assert body["text"] == (
            "[FIRING] callbot · service call: error rate 25.0% (> 10.0%) over the last 15 min, "
            "8 runs — http://studio/p/x"
        )
        assert deliver(f"http://127.0.0.1:{srv.server_port}/hook", body) == 200
        assert got[0]["alert"] == "a" and got[0]["state"] == "firing"
    finally:
        srv.shutdown()
    with pytest.raises(ValueError, match="http"):
        deliver("file:///etc/passwd", {})


def test_alerts_say_what_they_cannot_be():
    with pytest.raises(ValueError, match="metric is one of"):
        _alert(metric="vibes")
    with pytest.raises(ValueError, match="p95_ms only"):
        _alert(metric="error_rate", op="llm")
    a = Alert.from_dict({**_alert().to_dict(), "unknown": 1})
    assert a.name == "a"
