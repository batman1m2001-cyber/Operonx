"""Triggers: a webhook answers before the run; a schedule ticks on its own.

A webhook sender — a mail server, Slack, a CRM — waits seconds at most,
and the flow it starts can take minutes. An `http` service answered only
when the run ended. A `webhook` answers ``202`` with a run id at once and
the run goes on in the background, traced like any other.

A schedule has no caller at all. Its two silent failures are a backlog of
overlapping runs and a clock that dies on the first error; both are tested.
"""

from __future__ import annotations

import asyncio
import time

import pytest
from starlette.testclient import TestClient

from operonx import END, START, graph, op
from operonx.app import Application, ManifestError, Service, schedule, webhook
from operonx.app.serve import egress, ingress

pytestmark = pytest.mark.unit

SEEN: list = []


@op
async def slow_record(item: dict = None) -> dict:
    await asyncio.sleep(0.3)
    SEEN.append(item)
    return {"done": item}


@op
def record(item: dict = None) -> dict:
    SEEN.append(item)
    if item and item.get("tick") == 1:
        raise RuntimeError("the first tick fails; the clock must go on")
    return {"done": item}


@graph
def on_event():
    src = ingress()
    r = slow_record(item=src["item"])
    out = egress(item=r["done"])
    START >> src >> r >> out >> END


@graph
def on_tick():
    src = ingress()
    r = record(item=src["item"])
    START >> src >> r >> END


@pytest.fixture(autouse=True)
def _clear():
    SEEN.clear()
    yield
    SEEN.clear()


def wait_for(predicate, timeout=5.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if predicate():
            return True
        time.sleep(0.02)
    return False


def test_webhook_answers_202_before_the_run_finishes():
    app = Application("t", services=[Service("mail", webhook("/mail", port=8811), graph=on_event)])
    with TestClient(app.asgi()) as client:
        t0 = time.monotonic()
        reply = client.post("/mail", json={"subject": "hello"})
        elapsed = time.monotonic() - t0
        assert reply.status_code == 202
        body = reply.json()
        assert body["accepted"] is True and body["run_id"]
        assert elapsed < 0.3  # the run takes 0.3 s; the answer did not wait for it
        assert wait_for(lambda: SEEN == [{"subject": "hello"}])  # and the run still happened


def test_webhook_keeps_a_run_id_the_sender_chose():
    app = Application("t", services=[Service("mail", webhook("/mail", port=8812), graph=on_event)])
    with TestClient(app.asgi()) as client:
        reply = client.post("/mail?trace_id=msg-42", json={"subject": "hi"})
        assert reply.json()["run_id"] == "msg-42"


def test_webhook_refuses_beyond_max_inflight():
    app = Application(
        "t", services=[Service("mail", webhook("/mail", port=8813), graph=on_event, max_inflight=1)]
    )
    asgi_app = app.asgi()
    with TestClient(asgi_app) as client:
        transport = asgi_app.state.operonx_runners[0].transport
        first = client.post("/mail", json={"n": 1})
        second = client.post("/mail", json={"n": 2})
        assert first.status_code == 202
        assert second.status_code == 429  # one run pending: the sender retries later
        # the session closes just after the op records: wait for the run's end, not the op's
        assert wait_for(lambda: all(s.finished.is_set() for s in transport._pending))
        assert client.post("/mail", json={"n": 3}).status_code == 202  # room again
        assert transport.refused == 1


def test_schedule_ticks_and_survives_a_failing_run():
    app = Application(
        "t", services=[Service("sweep", schedule(every=0.05, port=8814), graph=on_tick)]
    )
    with TestClient(app.asgi()):
        assert wait_for(lambda: len(SEEN) >= 3)  # tick 1 raised; ticks 2 and 3 still came
    assert [s["tick"] for s in SEEN[:3]] == [1, 2, 3]


def test_schedule_skips_a_tick_while_the_last_run_is_going():
    app = Application(
        "t", services=[Service("sweep", schedule(every=0.05, port=8815), graph=on_event)]
    )
    asgi_app = app.asgi()
    with TestClient(asgi_app):
        assert wait_for(lambda: len(SEEN) >= 2)
    transport = asgi_app.state.operonx_runners[0].transport
    assert transport.skipped > 0  # each run takes 0.3 s, the clock ticks every 0.05 s
    assert [s["tick"] for s in SEEN] == list(range(1, len(SEEN) + 1))  # never two at once


@pytest.mark.parametrize(
    "kwargs", [{}, {"every": 1, "at": "08:00"}, {"every": "soon"}, {"every": 0}, {"at": "25:00"}]
)
def test_schedule_refuses_a_clock_it_cannot_read(kwargs):
    with pytest.raises((ManifestError, ValueError)):
        schedule(**kwargs)


def test_schedule_reads_every_and_at():
    assert schedule(every="5m").options == {"every": "5m"}
    assert schedule(at="08:00").options == {"at": "08:00"}
