"""The record: two files per run, readable by anyone, resumable by key."""

from __future__ import annotations

import json

from operonx.core.jobs import (
    ITEM_EMPTY,
    ITEM_FAILED,
    ITEM_OK,
    ITEM_SKIPPED,
    RUN_FAILED,
    RUN_OK,
    ItemResult,
    JobRun,
    RunRecord,
    done_keys,
    last_run,
    runs_of,
)


def test_a_record_is_written_as_it_goes_and_finished_once(tmp_path):
    rec = RunRecord(tmp_path, "score", meta={"graph": "g", "source": "s"})
    assert rec.path == tmp_path / "score" / rec.run_id
    in_flight = json.loads((rec.path / "run.json").read_text(encoding="utf-8"))
    assert in_flight["status"] == "running" and in_flight["ended"] is None
    assert in_flight["graph"] == "g"

    rec.item(ItemResult("a", ITEM_OK, trace_id="t1", ms=3.5, sent=1))
    rec.item(ItemResult("b", ITEM_FAILED, error="boom", attempts=2))
    # Readable before finish — what a studio tab polls.
    partial = JobRun.load(rec.path)
    assert partial.status == "running" and [i.key for i in partial.items] == ["a", "b"]

    run = rec.finish(RUN_FAILED)
    assert run.status == RUN_FAILED and run.ended
    assert run.counts == {"ok": 1, "failed": 1, "empty": 0, "skipped": 0, "timeout": 0}
    assert run.failed[0].error == "boom" and run.failed[0].attempts == 2
    assert run.ok[0].trace_id == "t1"
    assert "ok=1 failed=1" in run.summary() and "JobRun(" in repr(run)


def test_last_run_is_the_latest_and_runs_are_chronological(tmp_path):
    ids = []
    for status in (RUN_OK, RUN_FAILED, RUN_OK):
        rec = RunRecord(tmp_path, "score")
        rec.finish(status)
        ids.append(rec.run_id)
    assert [p.name for p in runs_of(tmp_path, "score")] == ids
    assert last_run(tmp_path, "score").run_id == ids[-1]
    assert last_run(tmp_path, "other") is None
    assert runs_of(tmp_path, "other") == []


def test_done_keys_are_what_a_resume_may_skip():
    rec_items = [
        ItemResult("ok", ITEM_OK),
        ItemResult("empty", ITEM_EMPTY),
        ItemResult("skipped", ITEM_SKIPPED),
        ItemResult("failed", ITEM_FAILED),
    ]
    run = JobRun(job="j", run_id="r", path=None, status=RUN_FAILED, started="", ended="",
                 counts={}, items=rec_items)
    assert done_keys(run) == {"ok", "empty", "skipped"}
    assert done_keys(None) == set()


def test_a_source_error_is_kept_on_the_run(tmp_path):
    rec = RunRecord(tmp_path, "score")
    run = rec.finish(RUN_FAILED, error="source: OSError: gone")
    assert run.meta["error"] == "source: OSError: gone"
