"""A script run inside a project can be traced into the project (C14, F12).

``trace="local"`` inside a project wrote to ``/tmp/operonx_traces/adhoc``:
``resolve_root`` knew the project only when an ``Application`` had set it,
and Studio reads ``<project>/.operonx/runs``. ``trace="project"`` traces a
script where the project's own ``[tracing]`` says, the way its services
and jobs are traced.

``trace=None`` stays off, inside a project too: tracing every ``Operon()``
in a project by default would trace each test and each engine a service
builds inside an op, at a cost nobody asked for.
"""

import pytest

from operonx.core import END, START, Operon, graph, op
from operonx.core.workflow_trace import active_project, project_root, set_project_root
from operonx.telemetry.consumer import Consumer
from operonx.telemetry.consumers.local import LocalConsumer, resolve_root
from operonx.telemetry.runs.files import FilesRunStore


@op
def double(x: int = 0) -> dict:
    return {"result": x * 2}


@graph
def doubling(val):
    d = double(x=val)
    START >> d >> END


class Capture(Consumer):
    def __init__(self):
        super().__init__()
        self.traces = []

    def consume(self, trace):
        self.traces.append(trace)


def _project(tmp_path, monkeypatch, toml: str = ""):
    """A project with its manifest at the top and the script one level down."""
    (tmp_path / "operonx.toml").write_text('[project]\nname = "demo"\n' + toml)
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    monkeypatch.chdir(scripts)
    return tmp_path


@pytest.fixture(autouse=True)
def _no_process_project(monkeypatch):
    monkeypatch.delenv("OPERONX_RUNS_DIR", raising=False)
    before = project_root()
    set_project_root(None)
    yield
    set_project_root(before)


def test_the_root_is_found_by_searching_up_for_operonx_toml(tmp_path, monkeypatch):
    project = _project(tmp_path, monkeypatch)
    assert resolve_root() == project / ".operonx" / "runs"
    assert active_project() == project
    assert project_root() is None  # nothing set it: it was found


async def test_no_trace_is_still_no_trace_inside_a_project(tmp_path, monkeypatch):
    project = _project(tmp_path, monkeypatch)
    engine = Operon(doubling, params={"val": None})
    assert engine._trace_consumers == []
    await engine.run(inputs={"val": 1})
    assert not (project / ".operonx").exists()


async def test_trace_local_writes_to_the_project_and_studio_lists_it(tmp_path, monkeypatch):
    project = _project(tmp_path, monkeypatch)
    out = await Operon(doubling, params={"val": None}, trace="local").run(inputs={"val": 2})
    assert out["result"] == 4

    (meta,) = (project / ".operonx" / "runs").rglob("meta.json")
    assert meta.parent.parent.parent.parent.name == "adhoc"  # adhoc/<name>/<day>/<id>
    (run,) = FilesRunStore(root=str(project / ".operonx" / "runs")).list_runs().items
    assert run.workflow == "doubling" and run.origin == "adhoc" and run.status == "ok"


def test_trace_project_uses_the_projects_tracing_sinks(tmp_path, monkeypatch):
    _project(tmp_path, monkeypatch, '\n[tracing]\nsinks = ["local"]\n')
    (consumer,) = Operon(doubling, params={"val": None}, trace="project")._trace_consumers
    assert isinstance(consumer, LocalConsumer)


def test_trace_project_falls_back_to_the_project_trace_list(tmp_path, monkeypatch):
    _project(tmp_path, monkeypatch, 'trace = ["local"]\n')
    (consumer,) = Operon(doubling, params={"val": None}, trace="project")._trace_consumers
    assert isinstance(consumer, LocalConsumer)


def test_trace_project_with_sinks_off_traces_nothing(tmp_path, monkeypatch):
    _project(tmp_path, monkeypatch, "\n[tracing]\nsinks = []\n")
    assert Operon(doubling, params={"val": None}, trace="project")._trace_consumers == []


def test_trace_project_with_nothing_configured_records_locally(tmp_path, monkeypatch):
    """Like a job: a run that asked to be traced is never untraced by omission."""
    _project(tmp_path, monkeypatch)
    (consumer,) = Operon(doubling, params={"val": None}, trace="project")._trace_consumers
    assert isinstance(consumer, LocalConsumer)


def test_trace_project_names_a_missing_sink(tmp_path, monkeypatch):
    _project(tmp_path, monkeypatch, '\n[tracing]\nsinks = ["trace_langfuse:nowhere"]\n')
    with pytest.raises(Exception, match="trace_langfuse:nowhere"):
        Operon(doubling, params={"val": None}, trace="project")


def test_trace_project_outside_a_project_says_so(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    with pytest.raises(ValueError, match=r"trace=\"project\".*operonx\.toml"):
        Operon(doubling, params={"val": None}, trace="project")


async def test_an_engine_run_inside_an_op_opens_no_trace_of_its_own():
    """A graph an op runs is part of that op's run, not a run of its own:
    its consumers are not called, so a service that runs a helper graph
    per call does not file a second trace per call."""
    inner_cap, outer_cap = Capture(), Capture()
    inner = Operon(doubling, params={"val": None}, trace=[inner_cap])

    @op
    async def helper(x: int = 0) -> dict:
        handle = inner.start(inputs={"val": x})
        out = await handle.result()
        return {"y": out["result"], "nested_nodes": len(handle.trace.nodes)}

    @graph
    def outer(x):
        h = helper(x=x)
        START >> h >> END

    out = await Operon(outer, params={"x": None}, trace=[outer_cap]).run(inputs={"x": 3})
    assert out["y"] == 6
    assert out["nested_nodes"] == 1  # the nested run still records into its own handle
    assert len(outer_cap.traces) == 1
    assert inner_cap.traces == []

    # Outside a run the same engine traces as configured.
    await inner.run(inputs={"val": 1})
    assert len(inner_cap.traces) == 1


def test_a_service_with_no_sinks_stays_untraced_in_a_project(tmp_path, monkeypatch):
    from operonx.app.serve.app import compile_graph

    _project(tmp_path, monkeypatch)
    assert compile_graph(f"{__name__}:doubling")._trace_consumers == []
