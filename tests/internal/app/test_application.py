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
from operonx.app.jobs import RUN_OK, Job, Runbook

PIPELINE = """
from operonx.core import END, START, graph, op
from operonx.app.jobs import Job, Runbook
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


nightly = Runbook("nightly", Job("inner", graph=score_flow, source=[{"call_id": "z", "text": "a b"}], key="call_id"))
not_a_runbook = 42
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
        source:calls:
          kind: jsonl
          path: {tmp_path / "data" / "calls.jsonl"}
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

        [[job]]
        name   = "score_calls"
        graph  = "{name}:score_flow"
        source = "source:calls"
        sink   = "out/scores.jsonl"
        key    = "call_id"
        schedule = "0 2 * * *"

        [[job]]
        name    = "nightly"
        runbook = "{name}:nightly"
        record_dir = "runs"
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
    assert graphs["score_flow"].used_by == ("serve:score", "job:score_calls")
    assert graphs["other"].used_by == ("serve:echo",)
    assert isinstance(graphs["other"], GraphRef)

    jobs = {j.name: j for j in app.jobs}
    assert isinstance(jobs["score_calls"], Job) and isinstance(jobs["nightly"], Runbook)
    assert jobs["score_calls"].sink == root / "out" / "scores.jsonl"
    assert jobs["nightly"].record_dir == root / "runs"
    with pytest.raises(ManifestError, match="no job named"):
        app.job("nope")
    assert "Application('demo'" in repr(app) and "jobs=2" in repr(app)


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
        "app": None,
        "description": "",
    }
    assert [(j["name"], j["kind"]) for j in d["jobs"]] == [
        ("score_calls", "job"),
        ("nightly", "runbook"),
    ]
    assert d["jobs"][0]["schedule"] == "0 2 * * *" and d["jobs"][1]["runbook"] == f"{name}:nightly"
    assert d["jobs"][0]["session"] == "per_item" and d["jobs"][1]["session"] is None
    assert name not in sys.modules  # describe() did not import the project


def test_bootstrap_installs_the_hub_and_the_project_once(project):
    name, root = project
    from operonx.core.registry import ResourceHub

    app = Application.find(root)
    app.bootstrap()
    assert ResourceHub.instance().has("source:calls")
    assert str(root) in sys.path
    app.bootstrap()  # idempotent


async def test_run_a_job_and_a_runbook(project):
    name, root = project
    app = Application.find(root)
    run = await app.run("score_calls")
    assert run.status == RUN_OK and run.counts["ok"] == 2
    assert (root / "out" / "scores.jsonl").exists()
    rb = app.run_sync if False else None  # run_sync needs its own loop; covered below
    del rb
    nightly = await app.run("nightly")
    assert nightly.status == RUN_OK and [j.name for j in nightly.jobs] == ["inner"]
    assert (root / "runs" / "nightly").is_dir()


def test_run_sync_from_a_script(project):
    _, root = project
    assert Application.find(root).run_sync("score_calls").status == RUN_OK


def test_a_runbook_entry_that_is_not_a_runbook_is_a_manifest_error(project):
    name, root = project
    toml = (
        (root / "operonx.toml")
        .read_text(encoding="utf-8")
        .replace(f"{name}:nightly", f"{name}:not_a_runbook")
    )
    (root / "operonx.toml").write_text(toml, encoding="utf-8")
    with pytest.raises(ManifestError, match="not a Runbook"):
        Application.find(root).jobs


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


def test_the_graph_ref_compiles_like_a_served_graph(project):
    _, root = project
    app = Application.find(root)
    app.bootstrap()
    from operonx.core import Operon

    engine = next(g for g in app.graphs if g.name == "other").compile()
    assert isinstance(engine, Operon)
    assert engine.name == "engine"  # what a served graph is named too: the
    # root graph takes its compiling variable


def test_the_old_import_paths_still_work_and_warn():
    for old in ("operonx.core.serve", "operonx.core.jobs", "operonx.core.manifest"):
        sys.modules.pop(old, None)
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        import importlib

        core_serve = importlib.import_module("operonx.core.serve")
        from operonx.core.jobs import Job as OldJob
        from operonx.core.manifest import Manifest as OldManifest
        from operonx.core.serve.protocol import RunRequest as OldRunRequest
    from operonx.app.jobs import Job
    from operonx.app.manifest import Manifest
    from operonx.app.serve.protocol import RunRequest

    assert OldJob is Job and OldManifest is Manifest and OldRunRequest is RunRequest
    assert (
        core_serve.current_session is importlib.import_module("operonx.app.serve").current_session
    )
    assert {
        str(x.message).split("`")[1] for x in w if issubclass(x.category, DeprecationWarning)
    } >= {"operonx.core.serve", "operonx.core.jobs", "operonx.core.manifest"}


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
