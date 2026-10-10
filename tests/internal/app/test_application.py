"""`Application`: the loaded manifest, and the three things production does.

Size is an acceptance test here too: the object replaced three copies of
"parse the manifest, bootstrap, resolve entry points", and it must stay
smaller than what it replaced.
"""

from __future__ import annotations

import json
import sys
import textwrap
import uuid
import warnings
from pathlib import Path

import pytest

from operonx.app import Application, GraphRef, ManifestError
from operonx.app.jobs import RUN_OK, Job

PIPELINE = """
from operonx.core import END, START, graph, op
from operonx.app.jobs import Job
from operonx.app.serve import egress, ingress


@op(bound="sync")
def score(call: dict = None) -> dict:
    return {"result": {"call_id": call["call_id"], "words": len(call["text"].split())}}


@graph
def score_flow():
    src = ingress()
    scored = score(call=src["item"])
    out = egress(item=scored["result"])
    START >> src >> scored >> out >> END


@graph
def other_flow():
    src = ingress()
    out = egress(item=src["item"])
    START >> src >> out >> END


inner = Job("inner", graph=score_flow, items=[{"call_id": "z", "text": "a b"}], key="call_id")
"""

CALLS = [{"call_id": "c1", "text": "one two three"}, {"call_id": "c2", "text": "four"}]


@pytest.fixture
def project(tmp_path, monkeypatch):
    name = f"pipe_{uuid.uuid4().hex[:6]}"
    (tmp_path / f"{name}.py").write_text(textwrap.dedent(PIPELINE), encoding="utf-8")
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "calls.jsonl").write_text(
        "".join(json.dumps(c) + "\n" for c in CALLS), encoding="utf-8"
    )
    (tmp_path / "resources.yaml").write_text(
        textwrap.dedent(f"""
        trace_local:dev:
          root: {tmp_path / "traces"}
    """),
        encoding="utf-8",
    )
    (tmp_path / "operonx.toml").write_text(
        textwrap.dedent(f"""
        [project]
        name = "demo"

        [resources]
        overlay = "resources.yaml"

        [[graph]]
        name  = "other"
        entry = "{name}:other_flow"

        [[serve]]
        name  = "score"
        kind  = "http"
        path  = "/score"
        port  = 8123
        graph = "{name}:score_flow"

        [[serve]]
        name  = "echo"
        kind  = "http"
        path  = "/echo"
        port  = 8123
        graph = "{name}:other_flow"
    """),
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    yield name, tmp_path
    sys.modules.pop(name, None)
    from operonx.core.registry import ResourceHub

    ResourceHub.reset_instance()


def test_load_find_and_the_three_lists(project):
    name, root = project
    app = Application.find(root)
    assert app.name == "demo" and app.root == root
    assert Application.load(root / "operonx.toml").name == "demo"

    assert [s.name for s in app.services] == ["score", "echo"]
    assert app.service("score").graph == f"{name}:score_flow"

    # Graphs: once each, named by [[graph]] when there is one, else by the
    # entry's attribute; with who uses them.
    graphs = {g.name: g for g in app.graphs}
    assert set(graphs) == {"other", "score_flow"}
    assert graphs["score_flow"].used_by == ("serve:score",)
    assert graphs["other"].used_by == ("serve:echo",)
    assert isinstance(graphs["other"], GraphRef)

    assert app.jobs == []  # jobs are declared in Python only
    with pytest.raises(ManifestError, match="no job named"):
        app.job("nope")
    assert "Application('demo'" in repr(app) and "jobs=0" in repr(app)


def test_describe_is_plain_data_and_imports_nothing(project):
    name, root = project
    d = Application.find(root).describe()
    assert d["name"] == "demo" and d["root"] == str(root)
    assert [g["name"] for g in d["graphs"]] == ["other", "score_flow"]
    assert d["services"][0] == {
        "name": "score",
        "kind": "http",
        "path": "/score",
        "port": 8123,
        "session": "per_request",
        "graph": f"{name}:score_flow",
        "job": None,
        "doors": None,  # named as module:attr — describing imports nothing
        "variants": [],
        "host": "0.0.0.0",
        "workers": 1,
        "on_startup": [],
        "on_session": None,
        "on_close": None,
        "app": None,
        "resume": None,
        "description": "",
        "key_ops": [],
        "playground": None,
        "replay": False,
        "input": None,
        "sinks": [],  # nothing configured: a service is not traced
        "sinks_from": "default",
    }
    assert d["jobs"] == []
    assert name not in sys.modules  # describe() did not import the project


def test_bootstrap_installs_the_hub_and_the_project_once(project):
    name, root = project
    from operonx.core.registry import ResourceHub

    app = Application.find(root)
    app.bootstrap()
    assert ResourceHub.instance().has("trace_local:dev")
    assert str(root) in sys.path
    app.bootstrap()  # idempotent


def _declared(name, root, monkeypatch):
    """The same project's jobs, declared in Python: a job over a file with an
    output, and a job of steps."""
    import importlib

    monkeypatch.syspath_prepend(str(root))
    mod = importlib.import_module(name)
    score_calls = Job(
        "score_calls",
        graph=mod.score_flow,
        items=root / "data" / "calls.jsonl",
        output=root / "out" / "scores.jsonl",
        key="call_id",
    )
    nightly = Job("nightly", steps=[mod.inner], record_dir=root / "runs")
    return Application("demo", jobs=[score_calls, nightly], root=root, resources=None)


async def test_run_a_job_and_a_job_of_steps(project, monkeypatch):
    name, root = project
    app = _declared(name, root, monkeypatch)
    d = app.describe()
    assert [(j["name"], j["kind"]) for j in d["jobs"]] == [
        ("score_calls", "job"),
        ("nightly", "steps"),
    ]
    assert d["jobs"][1]["steps"] == ["inner"]
    run = await app.run("score_calls")
    assert run.status == RUN_OK and run.counts["ok"] == 2
    assert run.path.parent == root / ".operonx" / "jobs" / "score_calls"  # the project's folder
    assert run.results == {"c1": {"call_id": "c1", "words": 3}, "c2": {"call_id": "c2", "words": 1}}
    assert (root / "out" / "scores.jsonl").read_text().count("\n") == 2
    nightly = await app.run("nightly")
    assert nightly.status == RUN_OK and [s["name"] for s in nightly.meta["steps"]] == ["inner"]
    assert (root / "runs" / "nightly").is_dir()


def test_run_sync_from_a_script(project, monkeypatch):
    name, root = project
    assert _declared(name, root, monkeypatch).run_sync("score_calls").status == RUN_OK


def test_asgi_gives_one_listeners_app(project):
    pytest.importorskip("starlette")
    from starlette.testclient import TestClient

    _, root = project
    app = Application.find(root)
    with TestClient(app.asgi(port=8123)) as client:
        assert client.post("/score", json={"call_id": "x", "text": "a b c"}).json() == {
            "call_id": "x",
            "words": 3,
        }
        assert client.post("/echo", json="hi").json() == "hi"
    with pytest.raises(ManifestError, match="exactly one listener"):
        app.asgi(port=9999)
    # a listener that leaves the startup hooks to another process
    assert app.asgi(port=8123, startup=False).router.lifespan_context is not None


def test_the_graph_ref_compiles_like_a_served_graph(project):
    _, root = project
    app = Application.find(root)
    app.bootstrap()
    from operonx.core import Operon

    engine = next(g for g in app.graphs if g.name == "other").compile()
    assert isinstance(engine, Operon)
    assert engine.name == "other_flow"  # what a served graph is named too:
    # after its graph function, not the variable that compiled it


def test_the_old_import_paths_still_work_and_warn():
    for old in ("operonx.core.serve", "operonx.core.manifest"):
        sys.modules.pop(old, None)
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        import importlib

        core_serve = importlib.import_module("operonx.core.serve")
        from operonx.core.manifest import Manifest as OldManifest
        from operonx.core.serve.protocol import RunRequest as OldRunRequest
    from operonx.app.manifest import Manifest
    from operonx.app.serve.protocol import RunRequest

    assert OldManifest is Manifest and OldRunRequest is RunRequest
    assert (
        core_serve.current_session is importlib.import_module("operonx.app.serve").current_session
    )
    assert {
        str(x.message).split("`")[1] for x in w if issubclass(x.category, DeprecationWarning)
    } >= {"operonx.core.serve", "operonx.core.manifest"}


def test_application_stays_small():
    """The acceptance test for the object's reason to exist."""
    source = (Path(__file__).resolve().parents[3] / "operonx" / "app" / "application.py").read_text(
        encoding="utf-8"
    )
    code = [
        line
        for line in source.splitlines()
        if line.strip() and not line.strip().startswith(("#", '"""'))
    ]
    assert len(code) < 200, (
        f"application.py has {len(code)} code lines; it is becoming a framework object"
    )
