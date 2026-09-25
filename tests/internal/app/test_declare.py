"""The application declared in Python.

`Service(...)` builds the same `ServeSpec` a `[[serve]]` block parses to,
with the graph, the hooks and the bound objects in hand instead of
`module:attr` strings; `Application(name, services=, jobs=)` is the
composition root over them; `operonx.toml` can point at that object with
`[project] app` so the CLIs and the studio find it the usual way.
"""

from __future__ import annotations

import sys
import textwrap
import uuid

import pytest

from operonx.app import Application, ManifestError, Service, asgi, env, http, websocket
from operonx.app.jobs import Job
from operonx.app.serve import RunRequest, egress, ingress
from operonx.core import END, PARENT, START, Operon, graph
from operonx.core.ops import op

# -- a project, declared in Python --------------------------------------


def LOUD(text: str) -> str:  # noqa: N802 — a style, named like one
    return text.upper()


def QUIET(text: str) -> str:  # noqa: N802
    return text.lower()


@op(bound="sync")
def shout(item: str = "", style=None, suffix: str = "") -> dict:
    return {"reply": style(item) + suffix}


@graph
def styled(style, suffix):
    src = ingress()
    loud = shout(item=src["item"], style=style, suffix=suffix)
    out = egress(item=loud["reply"])
    START >> src >> loud >> out >> END


@graph
def scored(threshold):
    """A graph with one runtime input the door builds (`threshold` is bound)."""
    src = ingress()
    out = egress(item=src["item"])
    START >> src >> out >> END


@graph
def flat(item, tag):
    """Two runtime inputs, no doors: what an `inputs=` contract is checked against."""
    both = shout(item=item, style=str.title, suffix=tag)
    both["reply"] >> PARENT["reply"]
    START >> both >> END


STARTED = []


def warm():
    STARTED.append("warm")


def pick(session) -> RunRequest:
    return RunRequest(variant=session.meta["query"].get("v"))


def build_app_object(**overrides):
    kwargs = dict(
        services=[
            Service(
                "greet",
                http("POST", "/greet", port=8130),
                graph=styled,
                variants={
                    "loud": dict(style=LOUD, suffix="!"),
                    "quiet": dict(style=QUIET, suffix="."),
                },
                ingress=["src"],
                egress=["out"],
                on_session=pick,
                description="one door, two styles",
            ),
            Service(
                "echo",
                http("POST", "/echo", port=8130),
                graph=scored,
                variants={"a": dict(threshold=1)},
            ),
        ],
        on_startup=[warm],
        jobs=[Job("score_two", graph=styled, source=[{"item": "x"}], sink=[])],
    )
    kwargs.update(overrides)
    return Application("demo", **kwargs)


def test_a_service_is_the_same_record_a_manifest_block_parses_to():
    app = build_app_object()
    spec = app.service("greet")
    assert spec.kind == "http" and spec.path == "/greet" and spec.port == 8130
    assert spec.graph is styled and spec.on_session is pick
    assert spec.variants["loud"]["style"] is LOUD
    assert spec.ingress == ("src",) and spec.egress == ("out",)
    assert app.manifest.on_startup == (warm,)
    assert [j.name for j in app.jobs] == ["score_two"]


def test_describe_names_objects_the_way_a_manifest_would():
    d = build_app_object().describe()
    greet = next(s for s in d["services"] if s["name"] == "greet")
    assert greet["graph"] == f"{__name__}:styled"
    assert greet["on_session"] == f"{__name__}:pick"
    assert greet["variants"] == ["loud", "quiet"]
    assert greet["ingress"] == ["src"] and greet["egress"] == ["out"]
    assert [g["name"] for g in d["graphs"]] == ["styled[loud]", "styled[quiet]", "scored[a]"]
    assert d["graphs"][0]["bind"] == {"style": f"{__name__}:LOUD", "suffix": "!"}
    assert d["jobs"] == [
        {
            "name": "score_two",
            "kind": "job",
            "graph": "styled",
            "runbook": None,
            "session": "per_item",
            "source": "[{'item': 'x'}]",
            "sink": "[]",
            "schedule": None,
            "description": "",
        }
    ]


def test_graph_refs_compile_from_the_objects():
    app = build_app_object()
    engine = next(g for g in app.graphs if g.name == "styled[quiet]").compile()
    assert isinstance(engine, Operon)


def test_the_declared_app_serves_and_runs_its_startup_hooks():
    pytest.importorskip("starlette")
    from starlette.testclient import TestClient

    STARTED.clear()
    app = build_app_object()
    with TestClient(app.asgi(port=8130)) as client:
        assert STARTED == ["warm"]
        assert client.post("/greet?v=loud", json="hey").json() == "HEY!"
        assert client.post("/greet?v=quiet", json="HEY").json() == "hey."


def test_declaration_errors_name_the_service():
    with pytest.raises(
        ManifestError, match="Service\\('ws'\\) is a websocket listener and must set max_inflight"
    ):
        Service("ws", websocket("/ws"), graph=styled)
    with pytest.raises(ManifestError, match="needs app="):
        Service("admin", asgi("/"))
    with pytest.raises(ManifestError, match="needs graph="):
        Service("x", http("POST", "/x"))
    with pytest.raises(ManifestError, match="both serve /greet"):
        Application(
            "dup",
            services=[
                Service("a", http("POST", "/greet"), graph=styled, variants={"v": {}}),
                Service("b", http("POST", "/greet"), graph=styled, variants={"v": {}}),
            ],
        )


# -- the inputs contract ---------------------------------------------------


def test_declared_inputs_are_checked_against_the_graph_at_compile():
    from operonx.app.serve.app import engines_for

    ok = Service("flat", http("POST", "/flat"), graph=flat, inputs=["item", "tag"])
    assert set(engines_for(ok)) == {"flat"}
    wrong = Service("flat", http("POST", "/flat"), graph=flat, inputs=["item", "colour"])
    with pytest.raises(
        ManifestError, match="not built by the door: \\['tag'\\].*not a parameter: \\['colour'\\]"
    ):
        engines_for(wrong)


@pytest.mark.asyncio
async def test_a_door_that_builds_other_inputs_than_it_declared_is_refused():
    from operonx.app.serve import MemoryTransport, ServeRunner
    from operonx.app.serve.app import engines_for

    spec = Service("flat", websocket("/flat"), max_inflight=4, graph=flat, inputs=["item", "tag"])
    engine = engines_for(spec)["flat"]
    transport = MemoryTransport()
    runner = ServeRunner(engine, spec, transport=transport)
    runner._on_session = lambda s: RunRequest(inputs={"item": "a"})  # forgot `tag`
    session = transport.open()
    transport.stop()
    await runner.run()
    assert session.closed and session.sent == []


# -- operonx.toml points at the object -------------------------------------


@pytest.fixture
def pointed(tmp_path, monkeypatch):
    name = f"decl_{uuid.uuid4().hex[:6]}"
    src = tmp_path / "src"
    src.mkdir()
    (src / f"{name}.py").write_text(
        textwrap.dedent(f"""
        from operonx.app import Application, Service, env, http
        from operonx.app.serve import egress, ingress
        from operonx.core import END, START, graph

        @graph
        def echo():
            src = ingress()
            out = egress(item=src["item"])
            START >> src >> out >> END

        APP = Application("pointed", services=[Service("echo", http("POST", "/echo", port=env("P", 8131)), graph=echo)])
        """),
        encoding="utf-8",
    )
    (tmp_path / "operonx.toml").write_text(
        textwrap.dedent(f"""
        [project]
        name = "pointed"
        src  = ["src"]
        app  = "{name}:APP"
        """),
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    yield name, tmp_path
    sys.modules.pop(name, None)
    sys.path[:] = [p for p in sys.path if not p.startswith(str(tmp_path))]


def test_find_returns_the_object_the_manifest_points_at(pointed):
    name, root = pointed
    app = Application.find(root)
    assert app.name == "pointed" and app.root == root and app.manifest.src == ("src",)
    assert [s.name for s in app.services] == ["echo"] and app.service("echo").port == 8131
    assert app.describe()["services"][0]["graph"] == f"{name}:echo"


def test_a_pointer_to_something_else_is_a_manifest_error(pointed, tmp_path):
    name, root = pointed
    (root / "operonx.toml").write_text(
        f'[project]\nname = "pointed"\nsrc = ["src"]\napp = "{name}:echo"\n', encoding="utf-8"
    )
    with pytest.raises(ManifestError, match="not an Application"):
        Application.find(root)


def test_env_reads_the_variable_in_the_defaults_type(monkeypatch):
    monkeypatch.setenv("OPX_T_PORT", "9001")
    monkeypatch.setenv("OPX_T_FLAG", "yes")
    monkeypatch.delenv("OPX_T_NONE", raising=False)
    assert env("OPX_T_PORT", 8000) == 9001
    assert env("OPX_T_FLAG", False) is True
    assert env("OPX_T_NONE", "x") == "x"
