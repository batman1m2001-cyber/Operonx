"""A runbook wired over several lines, like a graph body — a DAG of wires.

The gate is the plan's example::

    with Runbook("nightly") as nightly:
        fetch >> [score, audit]
        score >> [report, export]
        [report, audit] >> notify

A job starts when every job wired into it has finished; `audit` runs
beside `score`; a failed `score` skips `report`, `export` and `notify`
and nothing else.
"""

from __future__ import annotations

import asyncio
from time import perf_counter

import pytest

from operonx.app import Application
from operonx.app.jobs import RUN_FAILED, RUN_OK, RUN_STOPPED, Job, Runbook
from operonx.app.serve import egress, ingress
from operonx.core import END, START, graph, op

WINDOWS: dict = {}  # job -> (start, end)
FAIL: set = set()  # job names that fail their item


def _job(name: str, record_dir, seconds: float = 0.03) -> Job:
    @op(bound="io")
    async def work(item: dict = None) -> dict:
        t0 = perf_counter()
        await asyncio.sleep(seconds)
        WINDOWS[name] = (t0, perf_counter())
        if name in FAIL:
            raise ValueError(f"{name} failed")
        return {"out": {"id": item["id"], "by": name}}

    @graph
    def flow():
        src = ingress()
        step = work(item=src["item"])
        out = egress(item=step["out"])
        START >> src >> step >> out >> END

    return Job(name, graph=flow, source=[{"id": "x"}], sink=[], key="id", record_dir=record_dir)


@pytest.fixture(autouse=True)
def _reset():
    WINDOWS.clear()
    FAIL.clear()
    yield


@pytest.fixture
def six(tmp_path):
    return {
        n: _job(n, tmp_path / "jobs")
        for n in ("fetch", "score", "audit", "report", "export", "notify")
    }


def _nightly(j, tmp_path, **kw) -> Runbook:
    with Runbook("nightly", record_dir=tmp_path / "jobs", **kw) as nightly:
        j["fetch"] >> [j["score"], j["audit"]]
        j["score"] >> [j["report"], j["export"]]
        [j["report"], j["audit"]] >> j["notify"]
    return nightly


def _after(later: str, *earlier: str) -> bool:
    return all(WINDOWS[later][0] >= WINDOWS[e][1] for e in earlier)


def _overlap(a: str, b: str) -> bool:
    (s1, e1), (s2, e2) = WINDOWS[a], WINDOWS[b]
    return s1 < e2 and s2 < e1


def test_the_block_is_the_union_of_its_lines(tmp_path, six):
    nightly = _nightly(six, tmp_path)
    assert [j.name for j in nightly.jobs] == [
        "fetch",
        "score",
        "audit",
        "report",
        "export",
        "notify",
    ]
    assert set(nightly.wires) == {
        ("fetch", "score"),
        ("fetch", "audit"),
        ("score", "report"),
        ("score", "export"),
        ("report", "notify"),
        ("audit", "notify"),
    }
    # printed as its wires — one line per source, never `|`
    assert nightly.tree().splitlines() == [
        "fetch >> [score, audit]",
        "score >> [report, export]",
        "[audit, report] >> notify",
    ]


async def test_each_job_starts_when_its_inbound_wires_have_finished(tmp_path, six):
    run = await _nightly(six, tmp_path).run()
    assert run.status == RUN_OK and run.counts()["ok"] == 6
    assert _after("score", "fetch") and _after("audit", "fetch")
    assert _overlap("score", "audit")  # side by side
    assert _after("report", "score") and _after("export", "score")
    assert _after("notify", "report", "audit")  # waits for both
    assert run.wires == [
        ("fetch", "score"),
        ("fetch", "audit"),
        ("score", "report"),
        ("score", "export"),
        ("audit", "notify"),
        ("report", "notify"),
    ]


async def test_stop_skips_only_what_is_downstream_of_the_failure(tmp_path, six):
    FAIL.add("score")
    run = await _nightly(six, tmp_path).run()
    by = {j.name: j.status for j in run.jobs}
    assert by == {
        "fetch": "ok",
        "score": "failed",
        "audit": "ok",
        "report": "skipped",
        "export": "skipped",
        "notify": "skipped",
    }
    assert run.status == RUN_STOPPED
    assert "notify" not in WINDOWS  # never started


async def test_continue_runs_downstream_anyway(tmp_path, six):
    FAIL.add("score")
    run = await _nightly(six, tmp_path, on_error="continue").run()
    assert run.status == RUN_FAILED
    assert [j.status for j in run.jobs].count("ok") == 5


def test_the_one_expression_form_wires_the_same(tmp_path, six):
    a, b, c = six["fetch"], six["score"], six["audit"]
    with Runbook("qc", record_dir=tmp_path) as block:
        a >> [b, c]
    assert block.wires == Runbook("qc", a >> [b, c], record_dir=tmp_path).wires


def test_a_line_outside_the_block_wires_nothing_into_it(tmp_path, six):
    a, b, c = six["fetch"], six["score"], six["audit"]
    with Runbook("qc", record_dir=tmp_path) as qc:
        a >> b
    b >> c  # after the block closed: an expression, not a wire of qc
    assert qc.wires == [("fetch", "score")]


def test_a_schedule_is_declared_and_described(tmp_path, six):
    with Runbook("qc", record_dir=tmp_path, schedule="0 3 * * *", description="nightly QC") as qc:
        six["fetch"] >> [six["score"], six["audit"]]
    assert qc.describe()["schedule"] == "0 3 * * *"
    app = Application("demo", jobs=[six["fetch"], qc])
    listed = {j["name"]: j for j in app.describe()["jobs"]}
    assert listed["qc"]["kind"] == "runbook" and listed["qc"]["schedule"] == "0 3 * * *"
    assert app.job("qc") is qc
