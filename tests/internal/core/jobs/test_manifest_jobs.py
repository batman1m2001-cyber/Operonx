"""`[[job]]` in the manifest, and the phase 2 gate: the same graph is
served by `[[serve]]` and run by `[[job]]` without a line changing."""

from __future__ import annotations

import json
import sys
import textwrap
import uuid

import pytest

from operonx.core.jobs import RUN_OK, Job
from operonx.core.manifest import Manifest, ManifestError, _toml
from operonx.core.serve import MemoryTransport, serve_session

MANIFEST = """
[project]
name = "demo"

[resources]
overlay = "resources.yaml"

[[serve]]
name    = "score"
kind    = "http"
path    = "/score"
graph   = "pipeline:score_flow"

[[job]]
name        = "score_calls"
graph       = "pipeline:score_flow"
source      = "data/calls.jsonl"
sink        = "out/scores.jsonl"
key         = "call_id"
concurrency = 8
on_error    = "retry:2"
trace       = ["trace_local:default"]
schedule    = "0 2 * * *"
description = "nightly"

[[job]]
name    = "score_stream"
graph   = "pipeline:score_flow"
source  = "source:calls"
session = "stream"
max_inflight = 64

[[job]]
name       = "no_doors"
graph      = "pipeline:doubling"
source     = "data/nums.jsonl"
item_input = "val"
inputs     = { scale = 2 }
custom_knob = true
"""


def test_job_blocks_parse_with_their_defaults():
    m = Manifest.from_dict(_toml.loads(MANIFEST))
    assert [j.name for j in m.jobs] == ["score_calls", "score_stream", "no_doors"]

    j = m.job("score_calls")
    assert j.graph == "pipeline:score_flow" and j.session == "per_item"
    assert j.source == "data/calls.jsonl" and j.sink == "out/scores.jsonl"
    assert j.key == "call_id" and j.concurrency == 8 and j.on_error == "retry:2"
    assert j.trace == ("trace_local:default",) and j.schedule == "0 2 * * *"
    assert j.description == "nightly" and j.max_inflight is None

    s = m.job("score_stream")
    assert s.session == "stream" and s.max_inflight == 64 and s.sink is None
    assert s.on_error == "skip" and s.concurrency == 4

    n = m.job("no_doors")
    assert n.item_input == "val" and n.inputs == {"scale": 2}
    assert n.options == {"custom_knob": True}

    with pytest.raises(ManifestError, match="no job named"):
        m.job("nope")
    # The serve block beside them is untouched.
    assert m.serve("score").graph == "pipeline:score_flow"


@pytest.mark.parametrize(
    "block,message",
    [
        ({"graph": "m:g"}, "has no `name`"),
        ({"name": "j"}, "has no `graph`"),
        ({"name": "j", "graph": "pipeline"}, "not a `module:function`"),
        ({"name": "j", "graph": "m:g", "session": "per_request"}, "session"),
        ({"name": "j", "graph": "m:g", "concurrency": 0}, "concurrency"),
        ({"name": "j", "graph": "m:g", "concurrency": True}, "concurrency"),
        ({"name": "j", "graph": "m:g", "on_error": "ignore"}, "on_error"),
        ({"name": "j", "graph": "m:g", "max_inflight": -1}, "max_inflight"),
        ({"name": "j", "graph": "m:g", "inputs": [1]}, "`inputs` must be a table"),
    ],
)
def test_a_bad_job_block_is_a_manifest_error(block, message):
    with pytest.raises(ManifestError, match=message):
        Manifest.from_dict({"job": [block]})


def test_two_jobs_with_one_name_are_refused():
    with pytest.raises(ManifestError, match="both named 'j'"):
        Manifest.from_dict({"job": [{"name": "j", "graph": "m:g"}, {"name": "j", "graph": "m:h"}]})


def test_from_spec_resolves_paths_against_the_manifest_and_keeps_keys(tmp_path):
    m = Manifest.from_dict(_toml.loads(MANIFEST), source=tmp_path / "operonx.toml")
    assert m.root == tmp_path

    job = Job.from_spec(m.job("score_calls"), m.root)
    assert job.source == tmp_path / "data" / "calls.jsonl"
    assert job.sink == tmp_path / "out" / "scores.jsonl"
    assert job.record_dir == tmp_path / "jobs"
    assert job.key == "call_id" and job.concurrency == 8 and job.on_error == "retry:2"
    assert job.trace == ["trace_local:default"] and job.schedule == "0 2 * * *"
    assert job.graph == "pipeline:score_flow"  # imported when it runs

    stream = Job.from_spec(m.job("score_stream"), m.root)
    assert stream.source == "source:calls"  # a resource key, untouched
    assert stream.session == "stream" and stream.max_inflight == 64

    no_doors = Job.from_spec(m.job("no_doors"), m.root)
    assert no_doors.item_input == "val" and no_doors.inputs == {"scale": 2}


# -- the gate -------------------------------------------------------------

PIPELINE = """
from operonx.core import END, START, graph, op
from operonx.core.serve import egress, ingress


@op(bound="sync")
def score(call: dict = None) -> dict:
    return {"result": {"call_id": call["call_id"], "words": len(call["text"].split())}}


@graph
def score_flow():
    src = ingress()
    scored = score(call=src["item"])
    out = egress(item=scored["result"])
    START >> src >> scored >> out >> END
"""

CALLS = [{"call_id": "c1", "text": "one two three"}, {"call_id": "c2", "text": "four"}]


@pytest.fixture
def project(tmp_path, monkeypatch):
    name = f"pipeline_{uuid.uuid4().hex[:6]}"
    (tmp_path / f"{name}.py").write_text(textwrap.dedent(PIPELINE), encoding="utf-8")
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "calls.jsonl").write_text(
        "".join(json.dumps(c) + "\n" for c in CALLS), encoding="utf-8"
    )
    (tmp_path / "operonx.toml").write_text(
        textwrap.dedent(f"""
        [project]
        name = "demo"

        [[serve]]
        name  = "score"
        kind  = "http"
        path  = "/score"
        graph = "{name}:score_flow"

        [[job]]
        name   = "score_calls"
        graph  = "{name}:score_flow"
        source = "data/calls.jsonl"
        sink   = "out/scores.jsonl"
        key    = "call_id"

        [[job]]
        name    = "score_stream"
        graph   = "{name}:score_flow"
        source  = "data/calls.jsonl"
        sink    = "out/stream.jsonl"
        session = "stream"
    """),
        encoding="utf-8",
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    yield name, tmp_path
    sys.modules.pop(name, None)


async def test_the_same_graph_is_served_and_run_as_a_job_unchanged(project):
    """The phase 2 gate."""
    from operonx.core.serve.app import engine_for

    name, root = project
    manifest = Manifest.from_file(root / "operonx.toml")

    # Served: the [[serve]] entry's engine, driven through a session the
    # way the HTTP transport drives it — one request, one run, one reply.
    engine = engine_for(manifest.serve("score"))
    served = []
    for call in CALLS:
        transport = MemoryTransport()
        session = transport.open()
        await session.feed(call)
        session.end_input()
        await serve_session(engine, session)
        served.extend(session.sent)

    # Run as a job: the same entry point, from the same manifest.
    job = Job.from_spec(manifest.job("score_calls"), manifest.root)
    run = await job.run()
    assert run.status == RUN_OK and run.counts["ok"] == 2
    rows = [
        json.loads(line)
        for line in (root / "out" / "scores.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    as_job = [{k: v for k, v in r.items() if k != "_key"} for r in rows]

    assert served == as_job == [{"call_id": "c1", "words": 3}, {"call_id": "c2", "words": 1}]
    assert [r["_key"] for r in rows] == ["c1", "c2"]

    # And once more as one stream: same outputs, one run.
    stream = Job.from_spec(manifest.job("score_stream"), manifest.root)
    srun = await stream.run()
    assert srun.status == RUN_OK and srun.counts["fed"] == 2 and srun.counts["sent"] == 2
    srows = [
        json.loads(line)
        for line in (root / "out" / "stream.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert [{k: v for k, v in r.items() if k != "_key"} for r in srows] == as_job
    assert [r["_key"] for r in srows] == [
        "0",
        "1",
    ]  # outputs, by index: no item identity in a stream


# -- runbooks and deadlines in the manifest ------------------------------------


def test_a_runbook_block_names_its_object_and_nothing_else():
    m = Manifest.from_dict(
        {
            "job": [
                {
                    "name": "nightly",
                    "runbook": "jobs.nightly:nightly",
                    "schedule": "0 3 * * *",
                    "record_dir": "runs",
                    "description": "all of it",
                },
                {"name": "one", "graph": "m:g", "item_timeout": 2.5},
            ]
        }
    )
    rb = m.job("nightly")
    assert rb.runbook == "jobs.nightly:nightly" and rb.graph == ""
    assert rb.schedule == "0 3 * * *" and rb.record_dir == "runs" and rb.description == "all of it"
    assert m.job("one").item_timeout == 2.5 and m.job("one").runbook is None


@pytest.mark.parametrize(
    "block,message",
    [
        ({"name": "n", "runbook": "a:b", "graph": "m:g"}, "both `graph` and `runbook`"),
        ({"name": "n", "runbook": "nightly"}, "not a `module:attr`"),
        ({"name": "n", "runbook": "a:b", "source": "x.jsonl"}, "cannot set source"),
        ({"name": "n", "graph": "m:g", "item_timeout": 0}, "item_timeout"),
        ({"name": "n", "graph": "m:g", "item_timeout": "2"}, "item_timeout"),
        ({"name": "n", "graph": "m:g", "item_timeout": True}, "item_timeout"),
    ],
)
def test_bad_runbook_and_timeout_blocks_are_manifest_errors(block, message):
    with pytest.raises(ManifestError, match=message):
        Manifest.from_dict({"job": [block]})
