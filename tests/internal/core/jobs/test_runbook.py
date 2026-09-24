"""A Runbook: many jobs, one command, a record and never a span.

The gate is `test_nightly_runs_the_tree_and_records_it`: score after
extract, embed and score side by side, cluster after embed; run.json
holds the tree with a status per node and each job's run id; the only
traces are the graph runs the jobs minted.
"""

from __future__ import annotations

import asyncio
import json
from time import perf_counter

import pytest

from operonx.core import END, START, graph, op
from operonx.core.jobs import (
    RUN_FAILED,
    RUN_OK,
    RUN_STOPPED,
    Job,
    JobRun,
    Parallel,
    Runbook,
    RunbookRun,
    Sequential,
)
from operonx.core.serve import egress, ingress
from operonx.telemetry.consumer import Consumer

# -- four small jobs, handing off through shared lists -----------------------------

FAIL: set = set()  # (job, item id) pairs that raise
WINDOWS: dict = {}  # job -> [(start, end)] of every op run


def _record(job: str, t0: float) -> None:
    WINDOWS.setdefault(job, []).append((t0, perf_counter()))


@op(bound="io")
async def extract_op(item: dict = None) -> dict:
    t0 = perf_counter()
    await asyncio.sleep(0.02)
    if ("extract", item["id"]) in FAIL:
        raise ValueError(f"extract {item['id']}")
    _record("extract", t0)
    return {"doc": {"id": item["id"], "text": item["text"]}}


@op(bound="io")
async def embed_op(item: dict = None) -> dict:
    t0 = perf_counter()
    await asyncio.sleep(0.05)
    if ("embed", item["id"]) in FAIL:
        raise ValueError(f"embed {item['id']}")
    _record("embed", t0)
    return {"vec": {"id": item["id"], "dim": len(item["text"])}}


@op(bound="io")
async def score_op(item: dict = None) -> dict:
    t0 = perf_counter()
    await asyncio.sleep(0.05)
    _record("score", t0)
    return {"scored": {"id": item["id"], "score": 1}}


@op(bound="io")
async def cluster_op(item: dict = None) -> dict:
    t0 = perf_counter()
    await asyncio.sleep(0.01)
    _record("cluster", t0)
    return {"member": {"id": item["id"], "cluster": item["dim"] % 2}}


def _flow(name, fn, out_key):
    @graph
    def flow():
        src = ingress()
        step = fn(item=src["item"])
        out = egress(item=step[out_key])
        START >> src >> step >> out >> END

    return flow


class Capture(Consumer):
    def __init__(self):
        super().__init__()
        self.traces = []

    def consume(self, trace):
        self.traces.append(trace)


@pytest.fixture(autouse=True)
def _reset():
    FAIL.clear()
    WINDOWS.clear()
    yield


@pytest.fixture
def jobs(tmp_path):
    """extract → embed → cluster, and score beside embed."""
    cap = Capture()
    raw = [{"id": "a", "text": "xx"}, {"id": "b", "text": "yyy"}, {"id": "c", "text": "z"}]
    docs: list = []
    vecs: list = []
    scores: list = []
    members: list = []
    common = dict(key="id", record_dir=tmp_path / "jobs", trace=[cap], concurrency=4)
    extract = Job(
        "extract", graph=_flow("extract_flow", extract_op, "doc"), source=raw, sink=docs, **common
    )
    embed = Job(
        "embed",
        graph=_flow("embed_flow", embed_op, "vec"),
        source=lambda: docs,
        sink=vecs,
        **common,
    )
    score = Job(
        "score",
        graph=_flow("score_flow", score_op, "scored"),
        source=lambda: docs,
        sink=scores,
        **common,
    )
    cluster = Job(
        "cluster",
        graph=_flow("cluster_flow", cluster_op, "member"),
        source=lambda: vecs,
        sink=members,
        session="stream",
        record_dir=tmp_path / "jobs",
        trace=[cap],
    )
    return dict(
        extract=extract,
        embed=embed,
        score=score,
        cluster=cluster,
        cap=cap,
        docs=docs,
        vecs=vecs,
        scores=scores,
        members=members,
    )


def _overlap(a, b) -> bool:
    return any(s1 < e2 and s2 < e1 for s1, e1 in a for s2, e2 in b)


# -- the gate ----------------------------------------------------------------------


async def test_nightly_runs_the_tree_and_records_it(tmp_path, jobs):
    nightly = Runbook(
        "nightly",
        jobs["extract"] >> [jobs["embed"] >> jobs["cluster"], jobs["score"]],
        record_dir=tmp_path / "jobs",
        description="the plan's example",
    )
    run = await nightly.run()

    assert run.status == RUN_OK
    assert [j.name for j in run.jobs] == ["extract", "embed", "cluster", "score"]
    assert all(j.status == "ok" for j in run.jobs)
    assert len(jobs["docs"]) == 3 and len(jobs["vecs"]) == 3 and len(jobs["scores"]) == 3
    assert len(jobs["members"]) == 3

    # Order: embed and score overlapped; cluster began after embed ended.
    assert _overlap(WINDOWS["embed"], WINDOWS["score"])
    assert min(s for s, _ in WINDOWS["cluster"]) > max(e for _, e in WINDOWS["embed"])
    assert max(e for _, e in WINDOWS["extract"]) < min(
        s for s, _ in WINDOWS["embed"] + WINDOWS["score"]
    )

    # The record: the tree, with a status per node and each job's own run.
    d = json.loads((run.path / "run.json").read_text(encoding="utf-8"))
    assert d["runbook"] == "nightly" and d["status"] == "ok" and d["ended"]
    tree = d["tree"]
    assert tree["kind"] == "sequential" and [c["kind"] for c in tree["children"]] == [
        "job",
        "parallel",
    ]
    branches = tree["children"][1]["children"]
    assert branches[0]["kind"] == "sequential" and [c["name"] for c in branches[0]["children"]] == [
        "embed",
        "cluster",
    ]
    assert branches[1]["name"] == "score"
    for j in run.jobs:
        assert j.run_id and j.path
        own = JobRun.load(j.path)
        assert own.run_id == j.run_id and own.status == RUN_OK
    assert "jobs ok=4 failed=0 skipped=0" in run.summary()

    # Never a span: the only traces are the graph runs the jobs minted —
    # 3 + 3 + 3 per_item runs and 1 stream run — every one tagged with its
    # job, none with the runbook.
    traces = jobs["cap"].traces
    assert len(traces) == 10
    assert sorted({t.metadata["job"] for t in traces}) == ["cluster", "embed", "extract", "score"]
    assert all("runbook" not in t.metadata for t in traces)
    assert not any(t.workflow_name == "nightly" for t in traces)


# -- failure policy ----------------------------------------------------------------


async def test_stop_ends_the_sequence_but_a_parallel_sibling_finishes(tmp_path, jobs):
    FAIL.add(("embed", "b"))
    nightly = Runbook(
        "nightly",
        jobs["extract"] >> [jobs["embed"] >> jobs["cluster"], jobs["score"]],
        record_dir=tmp_path / "jobs",
    )
    run = await nightly.run()
    assert run.status == RUN_STOPPED
    by = {j.name: j for j in run.jobs}
    assert by["extract"].status == "ok"
    assert by["embed"].status == "failed" and "1 failed" in by["embed"].error
    assert by["cluster"].status == "skipped" and by["cluster"].run_id is None
    assert by["score"].status == "ok"  # its branch, its business
    assert jobs["members"] == []


async def test_continue_runs_every_step_and_the_run_is_failed(tmp_path, jobs):
    FAIL.add(("embed", "b"))
    nightly = Runbook(
        "nightly",
        jobs["extract"] >> [jobs["embed"] >> jobs["cluster"], jobs["score"]],
        record_dir=tmp_path / "jobs",
        on_error="continue",
    )
    run = await nightly.run()
    assert run.status == RUN_FAILED
    by = {j.name: j for j in run.jobs}
    assert by["embed"].status == "failed" and by["cluster"].status == "ok"
    assert len(jobs["members"]) == 2  # clustered what embed managed


async def test_a_job_that_raises_is_a_failed_node_not_a_crash(tmp_path):
    broken = Job(
        "broken",
        graph=_flow("broken_flow", extract_op, "doc"),
        source="/nonexistent/x.jsonl",
        record_dir=tmp_path / "jobs",
    )
    run = await Runbook("rb", broken, record_dir=tmp_path / "jobs").run()
    assert run.status == RUN_FAILED
    assert run.jobs[0].status == "failed" and "FileNotFoundError" in run.jobs[0].error


async def test_resume_reaches_per_item_jobs_and_leaves_streams_alone(tmp_path, jobs):
    FAIL.add(("extract", "b"))
    nightly = Runbook(
        "nightly",
        jobs["extract"] >> jobs["cluster"],
        record_dir=tmp_path / "jobs",
        on_error="continue",
    )
    first = await nightly.run()
    assert first.status == RUN_FAILED
    FAIL.clear()
    jobs["vecs"].extend([{"id": d["id"], "dim": 1} for d in jobs["docs"]])
    second = await nightly.run(resume=True)
    assert second.status == RUN_OK
    extract_run = JobRun.load(second.jobs[0].path)
    assert extract_run.counts["skipped"] == 2 and extract_run.counts["ok"] == 1
    cluster_run = JobRun.load(second.jobs[1].path)
    assert cluster_run.resume_from is None  # ran fresh, as a stream must
    assert cluster_run.counts["fed"] == len(jobs["vecs"]) == 2


# -- composition -------------------------------------------------------------------


def test_the_operators_build_the_same_tree_as_the_classes(tmp_path, jobs):
    a, b, c, d = jobs["extract"], jobs["embed"], jobs["cluster"], jobs["score"]
    spelled = Runbook("x", Sequential(a, Parallel(Sequential(b, c), d)), record_dir=tmp_path)
    sugared = Runbook("x", a >> [b >> c, d], record_dir=tmp_path)
    assert spelled.tree() == sugared.tree()
    assert sugared.tree().splitlines() == [
        "sequential",
        "  extract  (per_item)",
        "  parallel",
        "    sequential",
        "      embed  (per_item)",
        "      cluster  (stream)",
        "    score  (per_item)",
    ]
    # `(a >> b) >> c` and `a >> (b >> c)` are one flat sequence.
    assert ((a >> b) >> c).name == (a >> (b >> c)).name == "extract >> embed >> cluster"
    # A list on the left fans in: everything, then c.
    fan_in = [a, b] >> c
    assert isinstance(fan_in, Sequential) and fan_in.children[0].kind == "parallel"
    assert [j.name for j in sugared.jobs] == ["extract", "embed", "cluster", "score"]


def test_what_a_runbook_refuses(tmp_path, jobs):
    a = jobs["extract"]
    with pytest.raises(ValueError, match="appears twice"):
        Runbook("x", a >> a, record_dir=tmp_path)
    with pytest.raises(TypeError, match="made of Jobs"):
        Runbook("x", a >> "embed", record_dir=tmp_path)
    with pytest.raises(ValueError, match="at least one"):
        Sequential()
    with pytest.raises(ValueError, match="at least one"):
        Parallel()
    with pytest.raises(ValueError, match="on_error"):
        Runbook("x", a, on_error="retry", record_dir=tmp_path)
    with pytest.raises(ValueError, match="needs a name"):
        Runbook("", a, record_dir=tmp_path)


async def test_the_record_reads_back(tmp_path, jobs):
    rb = Runbook("rb", jobs["extract"] >> jobs["score"], record_dir=tmp_path / "jobs")
    run = await rb.run()
    again = RunbookRun.load(run.path)
    assert again.summary() == run.summary() and again.report.as_dict() == run.report.as_dict()
    assert again.meta["on_error"] == "stop"
    assert "RunbookRun(" in repr(again)
    assert rb.describe()["jobs"] == ["extract", "score"]
    assert "Runbook('rb'" in repr(rb)


def test_run_sync(tmp_path, jobs):
    run = Runbook("rb", jobs["extract"], record_dir=tmp_path / "jobs").run_sync()
    assert run.status == RUN_OK
