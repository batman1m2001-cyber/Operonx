"""Every run knows where it came from — P0 of the platform plan.

The gates:

* a service's runs carry ``origin=service`` and the service's name, a
  job's carry ``origin=job``, a job of steps' steps carry its run;
* services and jobs inherit the application's consumers, and a job with
  none anywhere still records (its item links must point somewhere);
* every run carries the code's version, read once per process;
* the local consumer files a run under its origin, below the project.
"""

from __future__ import annotations

import asyncio
import json
import subprocess
from pathlib import Path

import pytest

from operonx.app import Application, Service, websocket
from operonx.app.declare import inherit_trace
from operonx.app.jobs import Job
from operonx.app.manifest import Manifest, ServeSpec, _toml
from operonx.app.origin import (
    code_version,
    current_runbook,
    in_runbook,
    origin_metadata,
    stamp_process,
)
from operonx.app.serve import MemoryTransport, ServeRunner, egress, ingress
from operonx.core import END, START, Operon, graph, op
from operonx.core.workflow_trace import (
    WorkflowTrace,
    project_root,
    run_metadata,
    set_project_root,
    set_run_metadata,
)
from operonx.telemetry.consumer import Consumer
from operonx.telemetry.consumers.local import LocalConsumer, resolve_root, run_path


class Capture(Consumer):
    def __init__(self):
        super().__init__()
        self.traces = []

    def consume(self, trace):
        self.traces.append(trace)


@pytest.fixture(autouse=True)
def _clean_process():
    """Process-wide run defaults must not leak between tests."""
    before_meta, before_root = run_metadata(), project_root()
    yield
    for key in list(run_metadata()):
        set_run_metadata(**{key: None})
    set_run_metadata(**before_meta)
    set_project_root(before_root)


@op(bound="io")
async def shout(item: dict = None) -> dict:
    return {"loud": {"id": item["id"], "text": str(item.get("text", "")).upper()}}


@graph
def shout_flow():
    src = ingress()
    step = shout(item=src["item"])
    out = egress(item=step["loud"])
    START >> src >> step >> out >> END


# -- the tags ---------------------------------------------------------------


def test_origin_metadata_names_the_origin_and_mirrors_it_as_tags():
    md = origin_metadata("service", service="call", transport="websocket", variant=None)
    assert md == {
        "origin": "service",
        "service": "call",
        "transport": "websocket",
        "tags": ["origin:service", "service:call", "transport:websocket"],
    }
    with pytest.raises(ValueError, match="unknown origin"):
        origin_metadata("cron")


def test_in_runbook_scopes_the_runbook_run_and_restores_it():
    assert current_runbook() is None
    with in_runbook("nightly", "r1"):
        assert current_runbook() == ("nightly", "r1")
    assert current_runbook() is None


async def test_a_service_run_carries_its_service():
    cap = Capture()
    engine = Operon(shout_flow, trace=[cap])
    spec = ServeSpec(name="shouter", kind="memory", graph="x:y", max_inflight=8)
    transport = MemoryTransport(max_inflight=8)
    runner = ServeRunner(engine, spec, transport=transport)
    session = transport.open()
    session.feed_nowait({"id": 1, "text": "hi"})
    session.end_input()
    transport.stop()
    await runner.run()
    await asyncio.sleep(0.05)  # consumers run in a thread at the run's end
    assert session.sent == [{"id": 1, "text": "HI"}]
    (trace,) = cap.traces
    md = trace.metadata
    assert md["origin"] == "service" and md["service"] == "shouter" and md["transport"] == "memory"
    assert "origin:service" in md["tags"] and "service:shouter" in md["tags"]


async def test_the_steps_of_a_job_carry_its_run(tmp_path):
    cap = Capture()
    a = Job(
        "a",
        graph=shout_flow,
        items=[{"id": 1}],
        key="id",
        trace=[cap],
        record_dir=tmp_path,
    )
    b = Job(
        "b",
        graph=shout_flow,
        items=[{"id": 2}],
        key="id",
        trace=[cap],
        record_dir=tmp_path,
    )
    run = await Job("nightly", steps=[a, b], record_dir=tmp_path).run()
    assert run.status == "ok" and len(cap.traces) == 2
    for t in cap.traces:
        assert t.metadata["origin"] == "job"
        assert t.metadata["runbook"] == "nightly" and t.metadata["runbook_run"] == run.run_id
        assert f"runbook_run:{run.run_id}" in t.metadata["tags"]
    # run on its own, the same job's runs carry no parent
    cap.traces.clear()
    await a.run()
    assert "runbook" not in cap.traces[0].metadata


# -- the default consumers -------------------------------------------------------


def _svc(name, **kw):
    return Service(name, websocket(f"/{name}", port=9101), graph=shout_flow, max_inflight=8, **kw)


def test_services_inherit_the_applications_consumers_unless_they_name_their_own(tmp_path):
    app = Application(
        "demo",
        root=tmp_path,
        trace=["trace_local:default"],
        services=[_svc("plain"), _svc("own", trace=["trace_langfuse:x"]), _svc("silent", trace=[])],
    )
    by = {s.name: s.options.get("trace") for s in app.services}
    assert by == {"plain": ["trace_local:default"], "own": ["trace_langfuse:x"], "silent": []}


def test_without_an_application_default_a_service_stays_as_declared(tmp_path):
    app = Application("demo", root=tmp_path, services=[_svc("plain")])
    assert "trace" not in app.services[0].options


def test_project_trace_in_the_manifest_reaches_serves_and_jobs():
    m = Manifest.from_dict(
        _toml.loads("""
[project]
name = "demo"
trace = ["trace_local:default"]

[[serve]]
name = "a"
kind = "http"
path = "/a"
graph = "m:f"

[[serve]]
name = "b"
kind = "http"
path = "/b"
graph = "m:f"
trace = ["trace_langfuse:x"]
""")
    )
    assert m.serve("a").options["trace"] == ["trace_local:default"]
    assert m.serve("b").options["trace"] == ["trace_langfuse:x"]
    assert m.project["trace"] == ["trace_local:default"]


def test_a_job_inherits_the_app_default_or_records_locally_but_keeps_an_explicit_choice(tmp_path):
    none = Job("none", graph=shout_flow, items=[], record_dir=tmp_path)
    own = Job(
        "own",
        graph=shout_flow,
        items=[],
        record_dir=tmp_path,
        trace=["trace_langfuse:x"],
    )
    silent = Job("silent", graph=shout_flow, items=[], record_dir=tmp_path, trace=[])
    inherit_trace(none, None)
    inherit_trace(own, None)
    inherit_trace(silent, None)
    assert len(none.trace) == 1 and isinstance(none.trace[0], LocalConsumer)
    assert own.trace == ["trace_langfuse:x"] and silent.trace == []

    fresh = Job("fresh", graph=shout_flow, items=[], record_dir=tmp_path)
    app = Application("demo", root=tmp_path, trace=["trace_local:default"], jobs=[fresh])
    assert app.job("fresh").trace == ["trace_local:default"]


def test_a_job_of_steps_passes_the_default_to_each_step(tmp_path):
    a = Job("a", graph=shout_flow, items=[], record_dir=tmp_path)
    b = Job("b", graph=shout_flow, items=[], record_dir=tmp_path, trace=[])
    rb = Job("rb", steps=[a, b], record_dir=tmp_path)
    Application("demo", root=tmp_path, trace=["trace_local:default"], jobs=[rb]).jobs
    assert a.trace == ["trace_local:default"] and b.trace == []


# -- the version ------------------------------------------------------------------


def _git(root, *args):
    subprocess.run(
        ["git", *args],
        cwd=root,
        check=True,
        capture_output=True,
        env={
            "GIT_AUTHOR_NAME": "t",
            "GIT_AUTHOR_EMAIL": "t@t",
            "GIT_COMMITTER_NAME": "t",
            "GIT_COMMITTER_EMAIL": "t@t",
            "PATH": "/usr/bin:/bin",
        },
    )


def test_code_version_reads_the_commit_and_whether_the_tree_is_dirty(tmp_path):
    assert code_version(tmp_path) == {}  # not a checkout
    _git(tmp_path, "init", "-q")
    (tmp_path / "f.py").write_text("x = 1\n")
    _git(tmp_path, "add", "f.py")
    _git(tmp_path, "commit", "-q", "-m", "one")
    v = code_version(tmp_path)
    assert len(v["version"]) == 12 and v["version_dirty"] is False
    (tmp_path / "f.py").write_text("x = 2\n")
    assert code_version(tmp_path)["version_dirty"] is True


async def test_every_run_carries_the_process_metadata_and_its_own_ids_win(tmp_path):
    stamp_process(tmp_path, "demo")
    set_run_metadata(version="abc123def456", request_id="must-not-win")
    cap = Capture()
    engine = Operon(shout_flow, trace=[cap])
    handle = engine.start(inputs={}, request_id="r-1")
    await handle.collect()
    await asyncio.sleep(0.05)
    md = cap.traces[0].metadata
    assert md["project"] == "demo" and md["version"] == "abc123def456"
    assert md["request_id"] == "r-1"


# -- the layout -------------------------------------------------------------------


def _trace(**meta):
    return WorkflowTrace(
        trace_id="t-1",
        workflow_name="flow",
        started_at=0.0,
        ended_at=1.0,
        wall_started_at=1790467200.0,
        metadata=meta,
    )  # 2026-09-27 UTC


@pytest.mark.parametrize(
    "meta, expected",
    [
        (dict(origin="service", service="call"), "services/call/2026-09-27/t-1"),
        (dict(origin="job", job="qc", job_run="R1", key="k"), "jobs/qc/R1/t-1"),
        (dict(origin="eval", job="qc_eval", job_run="R2"), "evals/qc_eval/R2/t-1"),
        (dict(origin="playground", service="call"), "playground/2026-09-27/t-1"),
        (dict(), "adhoc/flow/2026-09-27/t-1"),
        (dict(origin="service", service="../../etc"), "services/.._.._etc/2026-09-27/t-1"),
    ],
)
def test_runs_are_filed_by_origin(meta, expected):
    assert run_path(_trace(**meta)).as_posix() == expected


def test_flat_and_template_layouts():
    t = _trace(origin="job", job="qc", job_run="R1", key="k")
    assert run_path(t, "flat").as_posix() == "t-1"
    assert run_path(t, "{origin}/{job}/{key}/{trace_id}").as_posix() == "job/qc/k/t-1"


def test_the_root_resolves_env_then_project_then_tmp(tmp_path, monkeypatch):
    monkeypatch.delenv("OPERONX_RUNS_DIR", raising=False)
    set_project_root(None)
    assert resolve_root("") == Path("/tmp/operonx_traces")
    set_project_root(tmp_path)
    assert resolve_root("") == tmp_path / ".operonx" / "runs"
    assert resolve_root("traces") == tmp_path / "traces"  # relative → the project
    assert resolve_root("/abs") == Path("/abs")
    monkeypatch.setenv("OPERONX_RUNS_DIR", str(tmp_path / "env"))
    assert resolve_root("") == tmp_path / "env"


def test_the_consumer_writes_under_the_origin_and_points_latest_at_it(tmp_path):
    out = LocalConsumer(config={"root": tmp_path}).consume(_trace(origin="service", service="call"))
    assert out == tmp_path / "services" / "call" / "2026-09-27" / "t-1"
    assert json.loads((out / "meta.json").read_text())["metadata"]["origin"] == "service"
    assert (tmp_path / "latest").resolve() == out.resolve()


# -- end to end: a job run through its application records every item's trace -----


def test_an_applications_job_records_its_items_under_the_project(tmp_path, monkeypatch):
    monkeypatch.delenv("OPERONX_RUNS_DIR", raising=False)
    job = Job(
        "score",
        graph=shout_flow,
        items=[{"id": 1}, {"id": 2}],
        key="id",
        record_dir=tmp_path / "records",
    )
    app = Application("demo", root=tmp_path, jobs=[job])  # no trace declared anywhere
    run = app.run_sync("score")
    assert run.status == "ok" and len(run.items) == 2
    runs = tmp_path / ".operonx" / "runs" / "jobs" / "score" / run.run_id
    on_disk = sorted(p.name for p in runs.iterdir())
    assert on_disk == sorted(i.trace_id for i in run.items)  # every item link resolves
    meta = json.loads((runs / run.items[0].trace_id / "meta.json").read_text())["metadata"]
    assert meta["origin"] == "job" and meta["project"] == "demo"


def test_a_service_pins_its_key_ops_and_describes_them(tmp_path):
    from operonx.app.declare import describe_service

    spec = _svc("call", key_ops=["stt", "reply"])
    assert spec.options["key_ops"] == ["stt", "reply"]
    assert describe_service(spec)["key_ops"] == ["stt", "reply"]
    assert describe_service(_svc("plain"))["key_ops"] == []
