"""`[tracing]` in operonx.toml: the one place that says which sinks are on.

The gates:

* precedence, most specific first — ``[tracing.services.<n>]`` /
  ``[tracing.jobs.<n>]``, then the service's or job's own ``trace=``, then
  ``[tracing] sinks``, then ``Application(trace=...)``, then the built-in
  default (a job records locally; a service traces nothing);
* ``"local"`` is the built-in local consumer, any other sink a resource key;
* an explicit ``sinks = []`` means "not traced" at that level;
* mistakes fail at load, naming the key; a sink missing from
  ``resources.yaml`` fails when the app starts, not as a run with no trace;
* every sink of one run receives the same trace id, including one a caller
  passed as ``?trace_id=``;
* ``--list`` and ``describe()`` show the sinks each service and job will use.
"""

from __future__ import annotations

import sys
import textwrap
import time
import uuid
from collections import defaultdict
from typing import ClassVar

import pytest

from operonx.app import Application, ManifestError
from operonx.app.manifest import Manifest, _toml
from operonx.core.registry import REGISTRY, ResourceHub
from operonx.core.utils.yaml_model import YamlModel
from operonx.telemetry.consumer import Consumer
from operonx.telemetry.consumers.local import LocalConsumer

pytestmark = pytest.mark.unit


# -- a sink that remembers what it was handed ------------------------------------

CAPTURED: dict = defaultdict(list)


class CaptureConfig(YamlModel):
    _category: ClassVar[str] = "trace_capture"
    tag: str = ""


class Capture(Consumer):
    def __init__(self, tag: str):
        super().__init__()
        self.tag = tag

    def consume(self, trace):
        CAPTURED[self.tag].append(trace.trace_id)


REGISTRY.register(CaptureConfig, lambda c: Capture(c.tag))


@pytest.fixture(autouse=True)
def _clean():
    CAPTURED.clear()
    yield
    CAPTURED.clear()
    ResourceHub.reset_instance()


def _manifest(text: str) -> Manifest:
    return Manifest.from_dict(_toml.loads(textwrap.dedent(text)))


TOML_APP = """
[project]
name = "callbot"

[tracing]
sinks = ["local", "trace_langfuse:edupia", "trace_clickhouse:default"]

[tracing.services.call]
sinks = ["local", "trace_langfuse:edupia"]

[tracing.jobs.backfill_call_logs]
sinks = []

[[serve]]
name  = "call"
kind  = "http"
path  = "/call"
graph = "pipeline:call"

[[serve]]
name  = "admin"
kind  = "http"
path  = "/admin"
graph = "pipeline:admin"
trace = ["trace_local:default"]

[[job]]
name  = "backfill_call_logs"
graph = "pipeline:backfill"

[[job]]
name  = "score_calls"
graph = "pipeline:score"
"""


# -- parsing and the precedence, in a toml-only application ----------------------


def test_tracing_is_parsed_and_settles_every_service_and_job():
    m = _manifest(TOML_APP)
    assert m.tracing.sinks == ("local", "trace_langfuse:edupia", "trace_clickhouse:default")
    assert m.tracing.services == {"call": ("local", "trace_langfuse:edupia")}
    assert m.tracing.jobs == {"backfill_call_logs": ()}
    # the override beats the project-wide list
    assert m.serve("call").options["trace"] == ["local", "trace_langfuse:edupia"]
    # the service's own `trace =` beats the project-wide list
    assert m.serve("admin").options["trace"] == ["trace_local:default"]

    d = Application(m).describe()
    by = {s["name"]: (s["sinks"], s["sinks_from"]) for s in d["services"]}
    assert by == {
        "call": (["local", "trace_langfuse:edupia"], "[tracing.services.call]"),
        "admin": (["trace_local:default"], "service"),
    }
    jobs = {j["name"]: (j["sinks"], j["sinks_from"]) for j in d["jobs"]}
    assert jobs == {
        "backfill_call_logs": ([], "[tracing.jobs.backfill_call_logs]"),
        "score_calls": (
            ["local", "trace_langfuse:edupia", "trace_clickhouse:default"],
            "[tracing]",
        ),
    }


def test_built_jobs_follow_the_same_precedence(tmp_path):
    from operonx.app.declare import build_job, settle_jobs

    m = _manifest(TOML_APP)
    jobs = {s.name: build_job(s, tmp_path) for s in m.jobs}
    settle_jobs(jobs, m, tmp_path)
    assert jobs["backfill_call_logs"].trace == []
    assert jobs["score_calls"].trace == [
        "local",
        "trace_langfuse:edupia",
        "trace_clickhouse:default",
    ]


def test_an_explicit_empty_trace_on_a_job_block_is_kept():
    """`[[job]] trace = []` is a decision, the way `Job(trace=[])` is. It
    used to read as "nothing declared" and inherit."""
    m = _manifest("""
    [project]
    name = "x"
    [tracing]
    sinks = ["local"]
    [[job]]
    name  = "quiet"
    graph = "m:f"
    trace = []
    """)
    assert m.job("quiet").trace == ()
    (j,) = Application(m).describe()["jobs"]
    assert (j["sinks"], j["sinks_from"]) == ([], "job")


def test_with_no_tracing_anywhere_a_service_is_untraced_and_a_job_records_locally():
    m = _manifest("""
    [project]
    name = "x"
    [[serve]]
    name = "a"
    kind = "http"
    path = "/a"
    graph = "m:f"
    [[job]]
    name  = "j"
    graph = "m:f"
    """)
    assert "trace" not in m.serve("a").options
    d = Application(m).describe()
    assert (d["services"][0]["sinks"], d["services"][0]["sinks_from"]) == ([], "default")
    assert (d["jobs"][0]["sinks"], d["jobs"][0]["sinks_from"]) == (["local"], "default")


# -- precedence with an application declared in Python ----------------------------

MODULE = """
from operonx.app import Application, Service, http
from operonx.app.jobs import Job, ListSink, Runbook
from operonx.core import END, START, graph, op


@op
def one() -> dict:
    return {{"n": 1}}


@graph
def flow():
    o = one()
    START >> o >> END


member = Job("member", graph=flow, source=[], sink=ListSink())
APP = Application(
    "demo",
    services=[
        Service("call", http("POST", "/call", port=8471), graph=flow{svc}),
        Service("plain", http("POST", "/plain", port=8471), graph=flow),
    ],
    jobs=[
        Job("backfill", graph=flow, source=[], sink=ListSink(){job}),
        Runbook("nightly", member),
    ],
    resources=None,
    src=["."]{app},
)
"""


def _project(tmp_path, monkeypatch, *, tracing="", svc=None, job=None, app=None):
    name = f"tr_{uuid.uuid4().hex[:8]}"
    (tmp_path / f"{name}.py").write_text(
        MODULE.format(
            svc="" if svc is None else f", trace={svc!r}",
            job="" if job is None else f", trace={job!r}",
            app="" if app is None else f", trace={app!r}",
        ),
        encoding="utf-8",
    )
    (tmp_path / "operonx.toml").write_text(
        f'[project]\nname = "demo"\nsrc = ["."]\napp = "{name}:APP"\n\n' + textwrap.dedent(tracing),
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.syspath_prepend(str(tmp_path))
    return name


@pytest.fixture
def unimport():
    before = set(sys.modules)
    yield
    for mod in set(sys.modules) - before:
        if mod.startswith("tr_"):
            sys.modules.pop(mod, None)


# one list per level: override, own, [tracing] sinks, application
OV, S, T, A = ["trace_t:o"], ["trace_t:s"], ["trace_t:t"], ["trace_t:a"]


def _tracing(override=None, sinks=None) -> str:
    out = ""
    if sinks is not None:
        out += f"[tracing]\nsinks = {sinks!r}\n"
    if override is not None:
        out += f"[tracing.services.call]\nsinks = {override!r}\n"
        out += f"[tracing.jobs.backfill]\nsinks = {override!r}\n"
    return out.replace("'", '"')


# (override, own, [tracing] sinks, Application(trace)) -> (sinks, from)
MATRIX = [
    pytest.param(OV, S, T, A, OV, "[tracing.{kind}s.{name}]", id="override-beats-all"),
    pytest.param(OV, None, None, A, OV, "[tracing.{kind}s.{name}]", id="override-beats-app"),
    pytest.param(None, S, T, A, S, "{kind}", id="own-beats-tracing-and-app"),
    pytest.param(None, None, T, A, T, "[tracing]", id="tracing-beats-app"),
    pytest.param(None, None, None, A, A, "application", id="app-alone"),
    pytest.param([], S, T, A, [], "[tracing.{kind}s.{name}]", id="override-empty-silences"),
    pytest.param(None, [], T, A, [], "{kind}", id="own-empty-silences"),
    pytest.param(None, None, [], A, [], "[tracing]", id="tracing-empty-silences"),
]


@pytest.mark.parametrize("override,own,sinks,app_trace,want,src", MATRIX)
def test_precedence_for_a_service(
    tmp_path, monkeypatch, unimport, override, own, sinks, app_trace, want, src
):
    _project(
        tmp_path,
        monkeypatch,
        tracing=_tracing(override, sinks),
        svc=own,
        app=app_trace,
    )
    app = Application.find(tmp_path)
    assert app.service("call").options["trace"] == want
    d = {s["name"]: s for s in app.describe()["services"]}
    assert d["call"]["sinks"] == want
    assert d["call"]["sinks_from"] == src.format(kind="service", name="call")


@pytest.mark.parametrize("override,own,sinks,app_trace,want,src", MATRIX)
def test_precedence_for_a_job(
    tmp_path, monkeypatch, unimport, override, own, sinks, app_trace, want, src
):
    _project(
        tmp_path,
        monkeypatch,
        tracing=_tracing(override, sinks),
        job=own,
        app=app_trace,
    )
    app = Application.find(tmp_path)
    d = {j["name"]: j for j in app.describe()["jobs"]}
    assert d["backfill"]["sinks"] == want
    assert d["backfill"]["sinks_from"] == src.format(kind="job", name="backfill")
    assert app.job("backfill").trace == want


def test_the_default_is_untraced_for_a_service_and_local_for_a_job(tmp_path, monkeypatch, unimport):
    _project(tmp_path, monkeypatch)
    app = Application.find(tmp_path)
    assert "trace" not in app.service("call").options
    (local,) = app.job("backfill").trace
    assert isinstance(local, LocalConsumer)
    d = app.describe()
    assert {s["name"]: s["sinks"] for s in d["services"]} == {"call": [], "plain": []}
    assert {j["name"]: j["sinks"] for j in d["jobs"]}["backfill"] == ["local"]


def test_tracing_reaches_the_services_and_jobs_that_declare_nothing(
    tmp_path, monkeypatch, unimport
):
    _project(tmp_path, monkeypatch, tracing='[tracing]\nsinks = ["local"]\n', svc=S, app=A)
    app = Application.find(tmp_path)
    assert app.service("call").options["trace"] == S  # its own
    assert app.service("plain").options["trace"] == ["local"]  # [tracing] beats the app's
    assert app.job("backfill").trace == ["local"]


def test_a_runbook_override_reaches_its_member_jobs(tmp_path, monkeypatch, unimport):
    _project(
        tmp_path,
        monkeypatch,
        tracing='[tracing]\nsinks = ["local"]\n[tracing.jobs.nightly]\nsinks = []\n',
    )
    app = Application.find(tmp_path)
    (member,) = app.job("nightly").jobs
    assert member.trace == []
    assert {j["name"]: j["sinks"] for j in app.describe()["jobs"]}["nightly"] == []


def test_a_runbook_member_can_be_named_on_its_own(tmp_path, monkeypatch, unimport):
    _project(tmp_path, monkeypatch, tracing='[tracing.jobs.member]\nsinks = ["trace_t:m"]\n')
    app = Application.find(tmp_path)
    (member,) = app.job("nightly").jobs
    assert member.trace == ["trace_t:m"]


def test_tracing_and_project_trace_together_is_an_error():
    with pytest.raises(ManifestError, match=r"\[project\] trace.*\[tracing\] sinks"):
        _manifest("""
        [project]
        name  = "x"
        trace = ["trace_local:default"]
        [tracing]
        sinks = ["local"]
        """)


# -- the "local" alias --------------------------------------------------------------


def test_local_is_the_builtin_local_consumer_on_an_engine():
    from operonx.app.serve.app import compile_graph
    from operonx.core import END, START, graph, op

    @op
    def one() -> dict:
        return {"n": 1}

    @graph
    def flow():
        o = one()
        START >> o >> END

    engine = compile_graph(flow, trace=["local"])
    (consumer,) = engine._trace_consumers
    assert isinstance(consumer, LocalConsumer)
    assert compile_graph(flow, trace=[])._trace_consumers == []


def test_a_job_traced_to_local_records_under_the_project(tmp_path, monkeypatch, unimport):
    monkeypatch.delenv("OPERONX_RUNS_DIR", raising=False)
    _project(tmp_path, monkeypatch, tracing='[tracing]\nsinks = ["local"]\n')
    app = Application.find(tmp_path)
    job = app.job("backfill")
    job.source = [{"id": 1}]
    job.record_dir = tmp_path / "records"
    run = app.run_sync("backfill")
    assert run.status == "ok"
    runs = tmp_path / ".operonx" / "runs" / "jobs" / "backfill" / run.run_id
    assert sorted(p.name for p in runs.iterdir()) == [i.trace_id for i in run.items]


# -- validation at load ---------------------------------------------------------------

BASE = """
[project]
name = "x"
[[serve]]
name  = "call"
kind  = "http"
path  = "/call"
graph = "m:f"
[[serve]]
name = "admin"
kind = "asgi"
path = "/admin"
app  = "m:app"
[[job]]
name  = "backfill"
graph = "m:f"
"""


@pytest.mark.parametrize(
    "tracing,match",
    [
        pytest.param("[tracing]\nsink = []\n", r"\[tracing\] has unknown key 'sink'", id="typo"),
        pytest.param(
            "[tracing]\nsinks = 'local'\n",
            r"\[tracing\] sinks must be a list",
            id="not-a-list",
        ),
        pytest.param(
            '[tracing]\nsinks = ["langfuse"]\n',
            r"\[tracing\] sinks: 'langfuse' is neither \"local\" nor `category:name`",
            id="bad-sink",
        ),
        pytest.param(
            "[tracing]\nsinks = [1]\n",
            r"\[tracing\] sinks: 1 is neither",
            id="not-a-string",
        ),
        pytest.param(
            '[tracing]\nsinks = ["local", "local"]\n',
            r"\[tracing\] sinks lists 'local' twice",
            id="twice",
        ),
        pytest.param(
            '[tracing.services.nope]\nsinks = ["local"]\n',
            r"\[tracing\.services\.nope\] names no service \(have: call, admin\)",
            id="unknown-service",
        ),
        pytest.param(
            '[tracing.services.admin]\nsinks = ["local"]\n',
            r"\[tracing\.services\.admin\].*asgi",
            id="asgi-service",
        ),
        pytest.param(
            "[tracing.jobs.nope]\nsinks = []\n",
            r"\[tracing\.jobs\.nope\] names no job \(have: backfill\)",
            id="unknown-job",
        ),
        pytest.param(
            '[tracing.services.call]\nsink = ["local"]\n',
            r"\[tracing\.services\.call\] has unknown key 'sink'",
            id="override-typo",
        ),
        pytest.param(
            "[tracing.services.call]\n",
            r"\[tracing\.services\.call\] has no `sinks`",
            id="override-empty-table",
        ),
        pytest.param(
            '[tracing.services.call]\nsinks = "local"\n',
            r"\[tracing\.services\.call\] sinks must be a list",
            id="override-not-a-list",
        ),
        pytest.param(
            '[tracing]\nservices = ["call"]\n',
            r"\[tracing\.services\] must be a table",
            id="services-not-a-table",
        ),
    ],
)
def test_a_mistake_in_tracing_fails_at_load_naming_the_key(tmp_path, tracing, match):
    path = tmp_path / "operonx.toml"
    path.write_text(BASE + tracing, encoding="utf-8")
    with pytest.raises(ManifestError, match=match) as err:
        Manifest.from_file(path)
    assert str(path) in str(err.value)


def test_an_unknown_name_in_a_python_application_fails_at_load(tmp_path, monkeypatch, unimport):
    _project(tmp_path, monkeypatch, tracing="[tracing.services.nope]\nsinks = []\n")
    with pytest.raises(ManifestError, match=r"\[tracing\.services\.nope\] names no service"):
        Application.find(tmp_path)
    _project(tmp_path, monkeypatch, tracing="[tracing.jobs.nope]\nsinks = []\n")
    with pytest.raises(ManifestError, match=r"\[tracing\.jobs\.nope\] names no job"):
        Application.find(tmp_path)


# -- one trace id per run, across every sink -----------------------------------------

HOOKS = """
from operonx.app import Application, Service, http, webhook
from operonx.app.serve import egress, ingress
from operonx.core import END, START, graph, op


@op
def echo(item: dict = None) -> dict:
    return {"out": item}


@graph
def flow():
    src = ingress()
    e = echo(item=src["item"])
    out = egress(item=e["out"])
    START >> src >> e >> out >> END


APP = Application(
    "hooks",
    services=[
        Service("hook", webhook("/hook", port=8472), graph=flow),
        Service("ask", http("POST", "/ask", port=8472), graph=flow),
    ],
    resources=None,
    src=["."],
)
"""


def _hooks(tmp_path, monkeypatch, sinks, resources):
    name = f"tr_{uuid.uuid4().hex[:8]}"
    (tmp_path / f"{name}.py").write_text(HOOKS, encoding="utf-8")
    (tmp_path / "resources.yaml").write_text(textwrap.dedent(resources), encoding="utf-8")
    (tmp_path / "operonx.toml").write_text(
        f'[project]\nname = "hooks"\nsrc = ["."]\napp = "{name}:APP"\n'
        '[resources]\noverlay = "resources.yaml"\n'
        f"[tracing]\nsinks = {sinks}\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.syspath_prepend(str(tmp_path))


def _wait(predicate, timeout=5.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if predicate():
            return True
        time.sleep(0.02)
    return False


def test_every_sink_receives_the_same_trace_id_including_a_callers(tmp_path, monkeypatch, unimport):
    from starlette.testclient import TestClient

    monkeypatch.delenv("OPERONX_RUNS_DIR", raising=False)
    _hooks(
        tmp_path,
        monkeypatch,
        '["local", "trace_capture:a", "trace_capture:b"]',
        """
        trace_capture:
          a: {tag: a}
          b: {tag: b}
        """,
    )
    app = Application.find(tmp_path)
    with TestClient(app.asgi()) as client:
        assert client.post("/hook?trace_id=msg-7", json={"n": 1}).json()["run_id"] == "msg-7"
        assert _wait(lambda: CAPTURED["a"] and CAPTURED["b"])
        assert client.post("/ask?trace_id=req-9", json={"n": 2}).status_code == 200
        assert _wait(lambda: len(CAPTURED["a"]) == 2 and len(CAPTURED["b"]) == 2)
        client.post("/ask", json={"n": 3})  # no id given: one is minted, and shared
        assert _wait(lambda: len(CAPTURED["a"]) == 3 and len(CAPTURED["b"]) == 3)
    assert CAPTURED["a"] == CAPTURED["b"]
    assert CAPTURED["a"][:2] == ["msg-7", "req-9"]
    local = {p.name for p in (tmp_path / ".operonx" / "runs").rglob("*") if p.is_dir()}
    assert set(CAPTURED["a"]) <= local  # and the local sink filed each under the same id


def test_a_sink_missing_from_resources_fails_when_the_app_starts(tmp_path, monkeypatch, unimport):
    _hooks(
        tmp_path,
        monkeypatch,
        '["local", "trace_capture:a", "trace_capture:gone"]',
        """
        trace_capture:
          a: {tag: a}
        """,
    )
    app = Application.find(tmp_path)  # loading is fine: resources are not read yet
    with pytest.raises(
        ManifestError,
        match=r"'trace_capture:gone' \(from \[tracing\]\) is not in .*resources\.yaml",
    ) as err:
        app.asgi()
    assert "service 'hook'" in str(err.value) and "trace_capture:a" in str(err.value)
    assert not CAPTURED


def test_a_sink_missing_from_resources_fails_before_a_job_runs(tmp_path, monkeypatch, unimport):
    _project(tmp_path, monkeypatch, tracing='[tracing]\nsinks = ["trace_capture:gone"]\n')
    (tmp_path / "resources.yaml").write_text("trace_capture:\n  a: {tag: a}\n", encoding="utf-8")
    ResourceHub.set_instance(ResourceHub.from_yaml(str(tmp_path / "resources.yaml")))
    app = Application.find(tmp_path)
    with pytest.raises(ManifestError, match=r"'trace_capture:gone' \(from \[tracing\]\)"):
        app.run_sync("backfill")


# -- what the operator reads ------------------------------------------------------------


def test_serve_list_shows_each_services_sinks(tmp_path, capsys):
    from operonx.cli.serve import main

    path = tmp_path / "operonx.toml"
    path.write_text(TOML_APP, encoding="utf-8")
    assert main(["-f", str(path), "--list"]) == 0
    out = capsys.readouterr().out
    assert "sinks: local, trace_langfuse:edupia  ([tracing.services.call])" in out
    assert "sinks: trace_local:default  (service)" in out


def test_run_list_shows_each_jobs_sinks(tmp_path, capsys):
    from operonx.cli.run import main

    path = tmp_path / "operonx.toml"
    path.write_text(TOML_APP, encoding="utf-8")
    assert main(["-f", str(path), "--list"]) == 0
    out = capsys.readouterr().out
    assert "sinks: none  ([tracing.jobs.backfill_call_logs])" in out
    assert "sinks: local, trace_langfuse:edupia, trace_clickhouse:default  ([tracing])" in out


def test_describe_names_a_consumer_object_by_its_type(tmp_path):
    from operonx.app import Service, http
    from operonx.app.declare import describe_service

    spec = Service(
        "x", http("POST", "/x"), graph="m:f", trace=[LocalConsumer(), Capture("t"), "trace_t:z"]
    )
    d = describe_service(spec)
    assert d["sinks"] == ["local", "Capture", "trace_t:z"] and d["sinks_from"] == "service"


def test_a_manifest_without_tracing_has_none():
    assert _manifest(BASE).tracing is None


def test_the_start_check_does_not_cache_a_sink_before_its_category_registers(tmp_path):
    """`hub.has()` parses and caches a config; done before the project has
    imported the module registering a custom sink category, it cached the
    raw dict and the engine's `get()` then refused the key."""
    from operonx.app import Service, http
    from operonx.app.serve.app import compile_graph
    from operonx.app.tracing import check_sinks
    from operonx.core import END, START, graph, op

    cfg = tmp_path / "resources.yaml"
    cfg.write_text("trace_late:\n  x: {tag: late}\n", encoding="utf-8")
    ResourceHub.set_instance(ResourceHub.from_yaml(str(cfg)))

    @op
    def one() -> dict:
        return {"n": 1}

    @graph
    def flow():
        o = one()
        START >> o >> END

    check_sinks("t", [Service("s", http("POST", "/s"), graph=flow, trace=["trace_late:x"])])

    class LateConfig(YamlModel):
        _category: ClassVar[str] = "trace_late"
        tag: str = ""

    REGISTRY.register(LateConfig, lambda c: Capture(c.tag))  # the project's import, later
    (consumer,) = compile_graph(flow, trace=["trace_late:x"])._trace_consumers
    assert isinstance(consumer, Capture) and consumer.tag == "late"


def test_a_hand_built_spec_keeps_the_trace_in_its_options():
    from operonx.app.manifest import ServeSpec
    from operonx.app.tracing import Tracing, settle_serves

    spec = ServeSpec(name="h", kind="http", graph="m:f", options={"trace": ["trace_t:h"]})
    (settled,) = settle_serves([spec], ["trace_t:a"], Tracing(sinks=("local",)))
    assert settled.options["trace"] == ["trace_t:h"] and settled.trace_from == "service"
    (again,) = settle_serves([settled], None, Tracing(services={"h": ()}))
    assert again.options["trace"] == [] and again.trace_from == "[tracing.services.h]"
    (back,) = settle_serves([again], None, None)  # what it declared is not lost
    assert back.options["trace"] == ["trace_t:h"]
