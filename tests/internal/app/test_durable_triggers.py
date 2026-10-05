"""Durable triggers (RUNTIME_R4_PLAN D5, D6): a webhook event written to the
queue before its 202 survives the process dying mid-run; a schedule shared
by two workers fires each tick once."""

from __future__ import annotations

import asyncio
import os
import signal
import subprocess
import sys
import textwrap
import time

import pytest
from starlette.testclient import TestClient

from operonx import END, START, graph, op
from operonx.app import Application, Service, schedule, webhook
from operonx.app.queue import SqliteQueue
from operonx.app.serve import ingress
from operonx.app.serve.triggers import ScheduleTransport

pytestmark = pytest.mark.unit

SEEN: list = []


@op
async def keep(item: dict = None) -> dict:
    SEEN.append(item)
    return {"kept": item}


@graph
def on_event():
    src = ingress()
    k = keep(item=src["item"])
    START >> src >> k >> END


def _wait(predicate, timeout=10.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if predicate():
            return True
        time.sleep(0.02)
    return False


def test_a_queued_webhook_event_runs_and_its_row_ends(tmp_path):
    SEEN.clear()
    db = tmp_path / "q.db"
    service = Service("mail", webhook("/mail", port=8821), graph=on_event, queue=str(db))
    with TestClient(Application("t", services=[service]).asgi()) as client:
        reply = client.post("/mail", json={"subject": "hi"})
        assert reply.status_code == 202
        run_id = reply.json()["run_id"]
        assert _wait(lambda: SqliteQueue(db).get(run_id).status == "done")
    assert SEEN == [{"subject": "hi"}]


def test_the_event_is_in_the_queue_before_the_202(tmp_path):
    """No worker runs it here (the transport's runner never starts): the
    202 still means the event is durable."""
    db = tmp_path / "q.db"
    service = Service("mail", webhook("/mail", port=8822), graph=on_event, queue=str(db))
    app = Application("t", services=[service]).asgi()
    with TestClient(app) as client:
        for runner in app.state.operonx_runners:
            runner.transport._stop_claims.set()  # this replica takes nothing
            runner.transport._claims and runner.transport._claims.wake.set()
        time.sleep(0.05)
        reply = client.post("/mail", json={"n": 1})
        run_id = reply.json()["run_id"]
        row = SqliteQueue(db).get(run_id)
        assert row is not None and row.payload == {"n": 1}


_CHILD = """
import asyncio, os, sys, time
sys.path.insert(0, {root!r})
from starlette.testclient import TestClient
from operonx import END, START, graph, op
from operonx.app import Application, Service, webhook
from operonx.app.queue import SqliteQueue
from operonx.app.serve import egress, ingress

LOG, DB, HOOK = {log!r}, {db!r}, {hook!r}

@op
async def ship(item: dict = None) -> dict:
    with open(LOG, "a") as f:
        f.write(f"ship {{item['order']}}\\n")
    if sys.argv[1] == "start":
        print("READY", flush=True)
        await asyncio.sleep(600)  # killed here
    return {{"shipped": item["order"]}}

@graph
def orders():
    src = ingress()
    s = ship(item=src["item"])
    out = egress(item=s["shipped"])
    START >> src >> s >> out >> END

service = Service("orders", webhook("/orders", port=8823), graph=orders, queue=DB,
                  lease=0.5, poll=0.1, callback_hosts=["127.0.0.1"])
with TestClient(Application("t", services=[service]).asgi()) as client:
    if sys.argv[1] == "start":
        reply = client.post(f"/orders?callback={{HOOK}}", json={{"order": 42}})
        print("ACCEPTED", reply.status_code, reply.json()["run_id"], flush=True)
        time.sleep(600)
    else:
        run_id = sys.argv[2]
        end = time.monotonic() + 30
        while SqliteQueue(DB).get(run_id).status != "done" and time.monotonic() < end:
            time.sleep(0.05)
        row = SqliteQueue(DB).get(run_id)
        print("RESULT", row.status, row.attempts, flush=True)
"""


def test_a_webhook_event_survives_its_process_dying(tmp_path):
    from tests.internal.app._receiver import Receiver

    receiver = Receiver()
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
    log, db = tmp_path / "calls.log", tmp_path / "q.db"
    script = tmp_path / "child.py"
    script.write_text(
        textwrap.dedent(_CHILD.format(root=root, log=str(log), db=str(db), hook=receiver.url))
    )

    proc = subprocess.Popen(
        [sys.executable, str(script), "start"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        seen = {}
        for line in proc.stdout:  # skip what the child logs to stdout
            parts = line.split()
            if parts[:1] == ["ACCEPTED"]:
                seen["accepted"] = parts
            if parts[:1] == ["READY"]:
                seen["ready"] = True
            if "accepted" in seen and "ready" in seen:
                break
        else:
            proc.wait(10)
            pytest.fail(f"the child exited early:\n{proc.stderr.read()}")
        os.kill(proc.pid, signal.SIGKILL)
        proc.wait(10)
    finally:
        if proc.poll() is None:
            proc.kill()
    _, status, run_id = seen["accepted"]
    assert status == "202"
    assert SqliteQueue(db).get(run_id).status == "running"  # its worker is dead

    done = subprocess.run(
        [sys.executable, str(script), "resume", run_id], capture_output=True, text=True, timeout=60
    )
    assert done.returncode == 0, done.stderr
    [result] = [line.split() for line in done.stdout.splitlines() if line.startswith("RESULT")]
    assert result == ["RESULT", "done", "2"]  # the second attempt, on another process
    assert log.read_text().split("\n")[:2] == ["ship 42", "ship 42"]
    # the callback came once, from the process that ended the run
    assert _wait(lambda: receiver.got)
    receiver.close()
    assert [(c["run_id"], c["status"], c["output"]) for c in receiver.got] == [(run_id, "done", 42)]


# ── a schedule shared by two workers ─────────────────────────────────────


class _Spec:
    def __init__(self, **options):
        self.name, self.options, self.max_inflight = "sweep", options, None


def test_a_schedule_fires_once_per_tick_across_two_workers(tmp_path):
    db = str(tmp_path / "q.db")

    async def worker(fired):
        clock = ScheduleTransport(_Spec(every=0.2, queue=db))

        async def stop_later():
            await asyncio.sleep(1.5)
            await clock.close()

        stopper = asyncio.ensure_future(stop_later())
        async for session in clock.sessions():
            fired.append(session.meta["slot"])
            session.finished.set()  # the run ended at once
        await stopper

    async def both():
        a, b = [], []
        await asyncio.gather(worker(a), worker(b))
        return a, b

    a, b = asyncio.run(both())
    slots = a + b
    assert len(slots) >= 5  # ~7 ticks in 1.5 s
    assert len(set(slots)) == len(slots)  # no tick ran twice
