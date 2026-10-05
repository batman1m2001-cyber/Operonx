"""The run queue's contract (RUNTIME_R4_PLAN D1–D4), on every backend:
SQLite always, Postgres when ``OPERONX_TEST_PG_DSN`` names a throwaway
database."""

from __future__ import annotations

import os
import threading
import time
import uuid

import pytest

from operonx.app.queue import LeaseLost, PostgresQueue, SqliteQueue, open_queue

PG_DSN = os.environ.get("OPERONX_TEST_PG_DSN", "")


@pytest.fixture(params=["sqlite", "postgres"])
def queue(request, tmp_path):
    if request.param == "sqlite":
        return SqliteQueue(tmp_path / "q.db")
    if not PG_DSN:
        pytest.skip("set OPERONX_TEST_PG_DSN to a throwaway Postgres")
    return PostgresQueue(PG_DSN)


@pytest.fixture
def svc():
    """A service name of the test's own, so Postgres rows never collide."""
    return f"svc-{uuid.uuid4().hex[:8]}"


def test_a_row_is_claimed_once_and_ends(queue, svc):
    put = queue.put(svc, {"text": "hi"}, meta={"query": {"a": "1"}})
    got = queue.claim(svc, "w1")
    assert got.id == put.id and got.payload == {"text": "hi"} and got.meta["query"] == {"a": "1"}
    assert got.status == "running" and got.attempts == 1 and got.worker == "w1"
    assert queue.claim(svc, "w2") is None  # held

    assert queue.finish(got.id, "w1", "done")
    assert queue.get(got.id).status == "done"
    assert queue.claim(svc, "w2") is None


def test_a_lapsed_lease_is_claimed_again_with_the_same_id(queue, svc):
    put = queue.put(svc, 1, max_attempts=2)
    queue.claim(svc, "dead", lease_s=0.05)
    time.sleep(0.1)  # the worker died: nobody renews
    again = queue.claim(svc, "w2")
    assert again.id == put.id and again.attempts == 2
    with pytest.raises(LeaseLost):
        queue.renew(put.id, "dead")
    assert not queue.finish(put.id, "dead", "done")  # its end does not count
    assert queue.renew(put.id, "w2") is None


def test_a_row_lapsing_on_its_last_attempt_fails(queue, svc):
    put = queue.put(svc, 1, max_attempts=1)
    queue.claim(svc, "dead", lease_s=0.05)
    time.sleep(0.1)
    assert queue.claim(svc, "w2") is None
    gone = queue.get(put.id)
    assert gone.status == "failed" and "lease" in gone.error


def test_a_thread_runs_one_row_at_a_time_in_order(queue, svc):
    a = queue.put(svc, "a", thread_id="t")
    b = queue.put(svc, "b", thread_id="t")
    other = queue.put(svc, "c", thread_id="u")

    first = queue.claim(svc, "w1")
    assert first.id == a.id
    assert queue.claim(svc, "w2").id == other.id  # another thread is free
    assert queue.claim(svc, "w3") is None  # b waits for a
    queue.finish(a.id, "w1")
    assert queue.claim(svc, "w3").id == b.id


def test_a_delayed_row_waits(queue, svc):
    queue.put(svc, 1, delay=0.2)
    assert queue.claim(svc, "w") is None
    time.sleep(0.25)
    assert queue.claim(svc, "w") is not None


def test_many_workers_claim_each_row_exactly_once(queue, svc, tmp_path):
    ids = {queue.put(svc, i).id for i in range(40)}
    got, lock = [], threading.Lock()

    def worker(name):
        q = SqliteQueue(tmp_path / "q.db") if isinstance(queue, SqliteQueue) else queue
        if isinstance(queue, PostgresQueue):
            q = PostgresQueue(PG_DSN)
        while True:
            item = q.claim(svc, name)
            if item is None:
                return
            with lock:
                got.append(item.id)
            q.finish(item.id, name)

    threads = [threading.Thread(target=worker, args=(f"w{i}",)) for i in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(30)
    assert sorted(got) == sorted(ids)


def test_a_slot_fires_once(queue, svc):
    assert queue.fire_once(svc, "2026-10-05T08:00")
    assert not queue.fire_once(svc, "2026-10-05T08:00")
    assert queue.fire_once(svc, "2026-10-06T08:00")


def test_a_stop_ends_a_queued_row_and_flags_a_running_one(queue, svc):
    waiting = queue.put(svc, 1, thread_id="t")
    assert queue.request_stop(waiting.id, "rollback")
    assert queue.get(waiting.id).status == "discarded"

    running = queue.put(svc, 2)
    queue.claim(svc, "w")
    assert queue.request_stop(running.id, "interrupt")
    assert queue.renew(running.id, "w") == "interrupt"
    queue.finish(running.id, "w", "stopped")
    assert queue.get(running.id).status == "stopped"


def test_only_json_is_queued(queue, svc):
    with pytest.raises(ValueError, match="must be JSON"):
        queue.put(svc, object())


def test_items_lists_by_service_and_status(queue, svc):
    a = queue.put(svc, 1)
    queue.put(svc, 2)
    queue.claim(svc, "w")
    assert [i.id for i in queue.items(svc, status="running")] == [a.id]
    assert len(queue.items(svc)) == 2


def test_open_queue_reads_a_door_setting(tmp_path):
    assert isinstance(open_queue("runs.db", root=tmp_path), SqliteQueue)
    assert (tmp_path / "runs.db").exists()
    with pytest.raises(ValueError, match="url"):
        open_queue({"path": "x"})


def test_a_failed_row_is_requeued_by_hand(queue, svc):
    put = queue.put(svc, 1, max_attempts=1)
    queue.claim(svc, "dead", lease_s=0.05)
    time.sleep(0.1)
    assert queue.claim(svc, "w") is None  # failed: its only attempt lapsed
    assert not queue.requeue("nope")
    assert queue.requeue(put.id)
    again = queue.claim(svc, "w")
    assert again.id == put.id and again.attempts == 1 and again.error is None
    assert not queue.requeue(put.id)  # running: not an ended row


def test_counts_by_status(queue, svc):
    queue.put(svc, 1)
    queue.put(svc, 2)
    queue.claim(svc, "w")
    counts = queue.counts(svc)
    assert counts["queued"] == 1 and counts["running"] == 1 and counts["failed"] == 0
    assert counts["oldest_queued_s"] >= 0
