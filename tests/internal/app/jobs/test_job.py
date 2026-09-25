"""A Job: one run per item, a record per run, and the graph unchanged.

The gate is `test_resume_runs_only_what_failed`: three items, one
failing, the record says 2 ok / 1 failed, and a resumed run touches only
the one — the shape the plan promised for phase 1.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from operonx.app.jobs import (
    ITEM_EMPTY,
    ITEM_FAILED,
    ITEM_OK,
    ITEM_SKIPPED,
    ITEM_TIMEOUT,
    RUN_FAILED,
    RUN_OK,
    RUN_STOPPED,
    Job,
    ListSink,
    last_run,
)
from operonx.app.serve import egress, ingress
from operonx.core import END, PARENT, START, graph, op

# -- the graph under test ---------------------------------------------------

FAIL: set = set()  # item ids that raise every time
FAIL_ONCE: set = set()  # item ids that raise on their first attempt only
ATTEMPTS: dict = {}  # id -> how many times `score` ran for it
INFLIGHT = {"now": 0, "max": 0}


@op(bound="io")
async def score(item: dict = None) -> dict:
    ATTEMPTS[item["id"]] = ATTEMPTS.get(item["id"], 0) + 1
    INFLIGHT["now"] += 1
    INFLIGHT["max"] = max(INFLIGHT["max"], INFLIGHT["now"])
    try:
        await asyncio.sleep(0.01)
        if item["id"] in FAIL_ONCE:
            FAIL_ONCE.discard(item["id"])
            raise ValueError(f"flaky {item['id']}")
        if item["id"] in FAIL:
            raise ValueError(f"cannot score {item['id']}")
        return {"scored": {"id": item["id"], "score": len(item["text"])}}
    finally:
        INFLIGHT["now"] -= 1


@graph
def score_flow():
    src = ingress()
    scored = score(item=src["item"])
    out = egress(item=scored["scored"])
    START >> src >> scored >> out >> END


@graph
def no_egress_flow():
    src = ingress()
    scored = score(item=src["item"])
    START >> src >> scored >> END


@op(bound="sync")
def double(x: int = 0) -> dict:
    return {"result": x * 2}


@graph
def doubling(val):
    d = double(x=val)
    START >> d >> END


ITEMS = [
    {"id": "a", "text": "xx"},
    {"id": "b", "text": "yyy"},
    {"id": "c", "text": "z"},
]


@pytest.fixture(autouse=True)
def _reset_op_state():
    FAIL.clear()
    FAIL_ONCE.clear()
    ATTEMPTS.clear()
    INFLIGHT["now"] = INFLIGHT["max"] = 0
    yield


def make_job(tmp_path, **overrides) -> Job:
    kwargs = dict(
        graph=score_flow,
        source=list(ITEMS),
        sink=None,
        key="id",
        record_dir=tmp_path / "jobs",
        concurrency=1,
    )
    kwargs.update(overrides)
    return Job("score", **kwargs)


def _lines(path):
    return [
        json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
    ]


# -- the record ---------------------------------------------------------------


async def test_per_item_runs_every_item_and_records_each(tmp_path):
    FAIL.add("b")
    got: list = []
    job = make_job(tmp_path, sink=got)

    run = await job.run()

    assert run.status == RUN_FAILED
    assert run.counts[ITEM_OK] == 2 and run.counts[ITEM_FAILED] == 1
    assert [g["id"] for g in got] == ["a", "c"]

    (failed,) = run.failed
    assert failed.key == "b"
    assert failed.error.startswith("scored: ") and "cannot score b" in failed.error
    assert failed.trace_id  # the run is findable in the traces
    assert all(i.trace_id for i in run.items)
    assert all(i.sent == 1 for i in run.ok)

    # On disk: what a second process (the studio, a cron mail) reads.
    assert run.path.is_dir()
    meta = json.loads((run.path / "run.json").read_text(encoding="utf-8"))
    assert meta["status"] == "failed" and meta["ended"]
    assert meta["counts"] == {"ok": 2, "failed": 1, "empty": 0, "skipped": 0, "timeout": 0}
    assert meta["graph"] == "score_flow" and meta["key"] == "id"
    rows = _lines(run.path / "items.jsonl")
    assert [(r["key"], r["status"]) for r in rows] == [("a", "ok"), ("b", "failed"), ("c", "ok")]


async def test_resume_runs_only_what_failed(tmp_path):
    """The phase 1 gate."""
    FAIL.add("b")
    job = make_job(tmp_path)
    first = await job.run()
    assert (first.counts[ITEM_OK], first.counts[ITEM_FAILED]) == (2, 1)

    FAIL.clear()  # "fixed"
    ATTEMPTS.clear()
    second = await job.run(resume=True)

    assert second.status == RUN_OK
    assert second.resume_from == first.run_id
    assert second.counts == {"ok": 1, "failed": 0, "empty": 0, "skipped": 2, "timeout": 0}
    assert ATTEMPTS == {"b": 1}  # a and c never ran again
    assert last_run(tmp_path / "jobs", "score").run_id == second.run_id


async def test_resume_with_no_earlier_run_runs_everything(tmp_path):
    run = await make_job(tmp_path).run(resume=True)
    assert run.resume_from is None
    assert run.counts[ITEM_OK] == 3


async def test_a_resumed_run_is_itself_resumable(tmp_path):
    """Skipped counts as done: three runs, and the third touches nothing."""
    FAIL.add("b")
    job = make_job(tmp_path)
    await job.run()
    FAIL.clear()
    await job.run(resume=True)
    ATTEMPTS.clear()
    third = await job.run(resume=True)
    assert third.counts[ITEM_SKIPPED] == 3 and ATTEMPTS == {}


# -- failure policy -----------------------------------------------------------


async def test_skip_carries_on_and_the_run_is_failed(tmp_path):
    FAIL.update({"a", "c"})
    run = await make_job(tmp_path, on_error="skip").run()
    assert run.status == RUN_FAILED
    assert [i.status for i in run.items] == [ITEM_FAILED, ITEM_OK, ITEM_FAILED]


async def test_stop_starts_nothing_after_a_failure(tmp_path):
    FAIL.add("b")
    run = await make_job(tmp_path, on_error="stop", concurrency=1).run()
    assert run.status == RUN_STOPPED
    assert [i.key for i in run.items] == ["a", "b"]
    assert "c" not in ATTEMPTS  # never dispatched


async def test_retry_tries_again_and_counts_the_attempts(tmp_path):
    FAIL_ONCE.add("b")
    run = await make_job(tmp_path, on_error="retry:2").run()
    assert run.status == RUN_OK
    by_key = {i.key: i for i in run.items}
    assert by_key["b"].status == ITEM_OK and by_key["b"].attempts == 2
    assert by_key["a"].attempts == 1


async def test_retry_exhausted_is_a_failure_and_the_run_goes_on(tmp_path):
    FAIL.add("b")
    run = await make_job(tmp_path, on_error="retry:1").run()
    assert run.status == RUN_FAILED
    by_key = {i.key: i for i in run.items}
    assert by_key["b"].status == ITEM_FAILED and by_key["b"].attempts == 2
    assert ATTEMPTS == {"a": 1, "b": 2, "c": 1}


def test_a_bad_policy_is_refused_at_declaration(tmp_path):
    with pytest.raises(ValueError, match="on_error"):
        make_job(tmp_path, on_error="ignore")
    with pytest.raises(ValueError, match="retry"):
        make_job(tmp_path, on_error="retry:x")
    with pytest.raises(ValueError, match="session"):
        make_job(tmp_path, session="per_request")


# -- what "nothing came out" looks like ---------------------------------------


async def test_a_run_that_sends_nothing_is_empty_not_ok(tmp_path):
    """The Analyze bug: 90 of 90 wrote nothing and the runner said OK."""
    got: list = []
    run = await make_job(tmp_path, graph=no_egress_flow, sink=got).run()
    assert run.status == RUN_OK  # nothing failed…
    assert run.counts[ITEM_EMPTY] == 3  # …but the record says nothing came out
    assert got == []
    assert all(i.sent == 0 for i in run.items)


async def test_a_sink_that_cannot_write_fails_the_item(tmp_path):
    class Broken:
        async def write(self, key, item):
            raise OSError("disk full")

        async def close(self):
            pass

    run = await make_job(tmp_path, sink=Broken()).run()
    assert run.status == RUN_FAILED
    assert all(i.status == ITEM_FAILED and "disk full" in i.error for i in run.items)


# -- identity -------------------------------------------------------------------


async def test_key_from_a_function_and_from_nothing(tmp_path):
    by_fn = await make_job(tmp_path, key=lambda it: it["id"].upper()).run()
    assert [i.key for i in by_fn.items] == ["A", "B", "C"]

    anonymous = await make_job(tmp_path, key=None).run()
    keys = [i.key for i in anonymous.items]
    assert len(set(keys)) == 3 and all(len(k) == 12 for k in keys)


async def test_an_item_without_its_key_is_a_failed_item(tmp_path):
    items = [{"id": "a", "text": "x"}, {"text": "no id"}, {"id": "", "text": "blank"}]
    run = await make_job(tmp_path, source=items).run()
    assert run.counts[ITEM_OK] == 1 and run.counts[ITEM_FAILED] == 2
    errors = [i.error for i in run.failed]
    assert any("no field 'id'" in e for e in errors)
    assert any("is empty" in e for e in errors)


# -- concurrency and the no-door shape -------------------------------------------


async def test_concurrency_bounds_items_in_flight(tmp_path):
    items = [{"id": str(n), "text": "t"} for n in range(8)]
    await make_job(tmp_path, source=items, concurrency=3).run()
    assert INFLIGHT["max"] == 3


async def test_a_graph_without_doors_takes_the_item_as_an_input(tmp_path):
    """`engine.batch()` as a Job: the run's result is the item's result."""
    got: list = []
    job = Job(
        "double",
        graph=doubling(val=PARENT["val"]),
        source=[1, 2, 3],
        sink=got,
        item_input="val",
        record_dir=tmp_path / "jobs",
    )
    run = await job.run()
    assert run.status == RUN_OK and run.counts[ITEM_OK] == 3
    assert [g["result"] for g in got] == [2, 4, 6]


async def test_the_sink_is_closed_once_after_the_last_item(tmp_path):
    sink = ListSink()
    await make_job(tmp_path, sink=sink).run()
    assert sink.closed and len(sink.pairs) == 3


def test_run_sync_from_a_script(tmp_path):
    run = make_job(tmp_path).run_sync()
    assert run.status == RUN_OK


def test_describe_names_things_without_leaking_values(tmp_path):
    job = make_job(tmp_path, source="data/calls.jsonl", sink="sink:scores", description="nightly")
    d = job.describe()
    assert d["graph"] == "score_flow" and d["source"] == "data/calls.jsonl"
    assert d["sink"] == "sink:scores" and d["key"] == "id" and d["description"] == "nightly"
    assert "Job('score'" in repr(job)


# -- stream mode ----------------------------------------------------------------


async def test_stream_feeds_every_item_through_one_run(tmp_path):
    got: list = []
    run = await make_job(tmp_path, session="stream", sink=got).run()
    assert run.status == RUN_OK
    assert run.counts["fed"] == 3 and run.counts["sent"] == 3
    assert run.items == []  # no per-item outcomes in one run
    assert run.meta["trace_id"]  # …but the one trace is named
    assert [g["id"] for g in got] == ["a", "b", "c"]
    assert set(ATTEMPTS) == {"a", "b", "c"}
    assert "fed=3 sent=3" in run.summary()
    meta = json.loads((run.path / "run.json").read_text(encoding="utf-8"))
    assert meta["counts"]["fed"] == 3 and meta["session"] == "stream"


async def test_stream_failure_names_the_op_and_fails_the_run(tmp_path):
    FAIL.add("b")
    got: list = []
    run = await make_job(tmp_path, session="stream", sink=got).run()
    assert run.status == RUN_FAILED
    assert run.meta["error"].startswith("scored: ") and "cannot score b" in run.meta["error"]
    assert run.counts["fed"] == 3 and run.counts["sent"] == 2
    assert [g["id"] for g in got] == ["a", "c"]


async def test_stream_cannot_resume(tmp_path):
    with pytest.raises(ValueError, match="cannot resume"):
        await make_job(tmp_path, session="stream").run(resume=True)


async def test_stream_bound_is_the_sessions_bound(tmp_path):
    items = [{"id": str(n), "text": "t"} for n in range(20)]
    run = await make_job(tmp_path, session="stream", source=items, max_inflight=2).run()
    assert run.status == RUN_OK and run.counts["fed"] == 20 and run.counts["sent"] == 20
    with pytest.raises(ValueError, match="max_inflight"):
        make_job(tmp_path, session="stream", max_inflight=0)


# -- what the trace carries ----------------------------------------------------------


class Capture:
    """A trace consumer that keeps every trace it is handed."""

    def __init__(self):
        from operonx.telemetry.consumer import Consumer

        outer = self

        class _C(Consumer):
            def consume(self, trace):
                outer.traces.append(trace)

        self.traces: list = []
        self.consumer = _C()


async def test_every_run_carries_the_job_on_its_trace(tmp_path):
    cap = Capture()
    run = await make_job(tmp_path, trace=[cap.consumer]).run()
    assert len(cap.traces) == 3  # one per item, and all of them flushed
    by_id = {t.trace_id: t for t in cap.traces}
    for item in run.items:
        t = by_id[item.trace_id]  # the record's trace id is the trace's
        md = t.metadata
        assert md["job"] == "score" and md["job_run"] == run.run_id and md["key"] == item.key
        assert md["tags"] == ["job:score", f"job_run:{run.run_id}", f"key:{item.key}"]


async def test_a_stream_run_is_one_trace_tagged_without_a_key(tmp_path):
    cap = Capture()
    run = await make_job(tmp_path, session="stream", trace=[cap.consumer]).run()
    assert len(cap.traces) == 1
    (t,) = cap.traces
    assert t.trace_id == run.meta["trace_id"]
    assert t.metadata["job"] == "score" and t.metadata["job_run"] == run.run_id
    assert "key" not in t.metadata
    assert t.metadata["tags"] == ["job:score", f"job_run:{run.run_id}"]


# -- a deadline per item --------------------------------------------------------------


@op(bound="io")
async def slow(item: dict = None) -> dict:
    await asyncio.sleep(0.4)
    return {"scored": item}


@graph
def slow_flow():
    src = ingress()
    s = slow(item=src["item"])
    out = egress(item=s["scored"])
    START >> src >> s >> out >> END


async def test_an_item_past_its_deadline_is_recorded_timeout(tmp_path):
    got: list = []
    run = await make_job(
        tmp_path, graph=slow_flow, sink=got, item_timeout=0.05, concurrency=3
    ).run()
    assert run.status == RUN_FAILED
    assert run.counts["timeout"] == 3 and run.counts["ok"] == 0
    assert all(i.error == "run exceeded 0.05s" for i in run.timed_out)
    assert "timeout=3" in run.summary()
    assert got == []
    assert all(i.ms < 300 for i in run.items)  # cancelled, not waited out


async def test_a_timeout_is_retried_and_stops_like_a_failure(tmp_path):
    run = await make_job(tmp_path, graph=slow_flow, item_timeout=0.05, on_error="retry:1").run()
    assert all(i.status == ITEM_TIMEOUT and i.attempts == 2 for i in run.items)

    stopped = await make_job(tmp_path, graph=slow_flow, item_timeout=0.05, on_error="stop").run()
    assert stopped.status == RUN_STOPPED and len(stopped.items) == 1


async def test_a_timed_out_item_runs_again_on_resume(tmp_path):
    job = make_job(tmp_path, graph=slow_flow, item_timeout=0.05)
    await job.run()
    job.item_timeout = None  # "fixed": no deadline
    again = await job.run(resume=True)
    assert again.counts["ok"] == 3 and again.counts["skipped"] == 0


def test_item_timeout_must_be_positive(tmp_path):
    with pytest.raises(ValueError, match="item_timeout"):
        make_job(tmp_path, item_timeout=0)
    assert make_job(tmp_path, item_timeout=2).describe()["item_timeout"] == 2.0
