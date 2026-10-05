"""R4b (RUNTIME_R4_PLAN D8–D10): a second message on a busy thread, a stream
read again after it dropped, and a callback when a queued run ends."""

from __future__ import annotations

import asyncio
import json
import time

import pytest
from starlette.testclient import TestClient

from operonx import END, START, graph, op
from operonx.app import Application, ManifestError, Service, http, webhook
from operonx.app.queue import SqliteQueue
from operonx.app.serve import egress, ingress
from tests.internal.app._receiver import Receiver

pytestmark = pytest.mark.unit

SEEN: list = []


@op
async def slow(item: dict = None) -> dict:
    SEEN.append(("start", item["n"]))
    await asyncio.sleep(item.get("sleep", 0.3))
    SEEN.append(("end", item["n"]))
    return {"out": item["n"]}


@graph
def work():
    src = ingress()
    s = slow(item=src["item"])
    out = egress(item=s["out"])
    START >> src >> s >> out >> END


def _wait(predicate, timeout=10.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if predicate():
            return True
        time.sleep(0.02)
    return False


def _app(db, port, **options):
    service = Service(
        "jobs", webhook("/jobs", port=port), graph=work, queue=str(db), poll=0.05, **options
    )
    return Application("t", services=[service]).asgi()


@pytest.fixture(autouse=True)
def _clear():
    SEEN.clear()


# ── a second message on a busy thread (D8) ─────────────────────────────────


def test_reject_answers_409_while_the_thread_is_busy(tmp_path):
    with TestClient(_app(tmp_path / "q.db", 8831, multitask="reject")) as client:
        first = client.post("/jobs?thread_id=t", json={"n": 1})
        second = client.post("/jobs?thread_id=t", json={"n": 2})
        other = client.post("/jobs?thread_id=u", json={"n": 3})
        assert first.status_code == 202 and other.status_code == 202
        assert second.status_code == 409 and second.json()["thread_id"] == "t"
        assert _wait(lambda: ("end", 1) in SEEN and ("end", 3) in SEEN)
        third = client.post("/jobs", headers={"x-operonx-thread": "t"}, json={"n": 4})
        assert third.status_code == 202  # the thread is free again


def test_enqueue_runs_a_threads_messages_one_after_another(tmp_path):
    db = tmp_path / "q.db"
    with TestClient(_app(db, 8832, multitask="enqueue")) as client:
        ids = [client.post("/jobs?thread_id=t", json={"n": n}).json()["run_id"] for n in (1, 2)]
        assert _wait(lambda: all(SqliteQueue(db).get(i).status == "done" for i in ids))
    assert SEEN == [("start", 1), ("end", 1), ("start", 2), ("end", 2)]


@pytest.mark.parametrize(("how", "ended"), [("interrupt", "stopped"), ("rollback", "discarded")])
def test_a_newer_message_stops_the_running_one(tmp_path, how, ended):
    db = tmp_path / "q.db"
    with TestClient(_app(db, 8833, multitask=how)) as client:
        old = client.post("/jobs?thread_id=t", json={"n": 1, "sleep": 30}).json()["run_id"]
        assert _wait(lambda: ("start", 1) in SEEN)
        t0 = time.monotonic()
        new = client.post("/jobs?thread_id=t", json={"n": 2}).json()["run_id"]
        assert _wait(lambda: SqliteQueue(db).get(new).status == "done")
        assert time.monotonic() - t0 < 10  # not after the old run's 30 s
    assert SqliteQueue(db).get(old).status == ended
    assert ("end", 1) not in SEEN and ("end", 2) in SEEN


def test_a_multitask_policy_needs_a_queue():
    with pytest.raises(ManifestError, match="queue="):
        Application(
            "t",
            services=[Service("jobs", webhook("/j", port=8834), graph=work, multitask="reject")],
        ).asgi()


# ── a stream read again after it dropped (D9) ──────────────────────────────


@op
async def count(item: dict = None):
    for i in range(item["n"]):
        await asyncio.sleep(0.05)
        yield {"i": i}


@graph
def counting():
    src = ingress()
    c = count(item=src["item"])
    out = egress(item=c["i"])
    START >> src >> c >> out >> END


def _events(lines):
    """``(id, data)`` of each server-sent event in *lines*."""
    out, seq = [], None
    for line in lines:
        if line.startswith("id: "):
            seq = int(line[4:])
        elif line.startswith("data: "):
            out.append((seq, json.loads(line[6:])))
    return out


def test_a_dropped_stream_is_read_again_from_where_it_stopped():
    service = Service("count", http("POST", "/count", port=8835), graph=counting)
    app = Application("t", services=[service]).asgi()
    sse = {"accept": "text/event-stream"}
    with TestClient(app) as client:
        lines, run_id = [], None
        with client.stream("POST", "/count", headers=sse, json={"n": 6}) as reply:
            run_id = reply.headers["x-operonx-trace-id"]
            for line in reply.iter_lines():
                lines.append(line)
                if len(_events(lines)) == 2:
                    break  # the connection drops here
        got = _events(lines)
        assert got == [(1, 0), (2, 1)]

        with client.stream("POST", f"/count?run_id={run_id}&after_seq=2", headers=sse) as reply:
            rest = _events(reply.iter_lines())
        assert rest == [(3, 2), (4, 3), (5, 4), (6, 5)]

        again = client.post(f"/count?run_id={run_id}", headers={**sse, "last-event-id": "5"})
        assert _events(again.text.splitlines()) == [(6, 5)]

        gone = client.post("/count?run_id=nope&after_seq=0", headers=sse)
        assert gone.status_code == 404


# ── a callback when a queued run ends (D10) ────────────────────────────────


def test_a_queued_run_calls_back_when_it_ends(tmp_path):
    receiver = Receiver()
    try:
        app = _app(tmp_path / "q.db", 8836, callback_hosts=["127.0.0.1"])
        with TestClient(app) as client:
            refused = client.post("/jobs?callback=http://10.0.0.1/x", json={"n": 1})
            assert refused.status_code == 400 and "callback" in refused.json()["error"]
            run_id = client.post(f"/jobs?callback={receiver.url}", json={"n": 7}).json()["run_id"]
            assert _wait(lambda: receiver.got)
        [call] = receiver.got
        assert call["run_id"] == run_id and call["status"] == "done"
        assert call["output"] == 7 and call["service"] == "jobs"
    finally:
        receiver.close()


def test_a_callback_needs_its_host_allowed(tmp_path):
    with TestClient(_app(tmp_path / "q.db", 8837)) as client:
        reply = client.post("/jobs?callback=http://127.0.0.1:9/x", json={"n": 1})
        assert reply.status_code == 400
