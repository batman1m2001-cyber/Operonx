"""One door, several compiled graphs: `[serve.variants]`.

A door whose graph is a factory over some part that differs per caller —
a callbot with one turn graph per agent — declares the variants in the
manifest, gets one engine per variant at boot, and `on_session` names
one per session with `RunRequest.variant`. The gate refuses a session
that names none or an unknown one, so a bad name is a refusal at the
door and never a run that fails on its first item.
"""

from __future__ import annotations

import sys
import textwrap
import uuid

import pytest

from operonx.app import Application, ManifestError
from operonx.app.manifest import Manifest, ServeSpec, _toml
from operonx.app.serve import MemoryTransport, RunRequest, ServeRunner, egress, ingress
from operonx.app.serve.app import compile_graph
from operonx.core import END, START, Operon, graph
from operonx.core.ops import op

# -- the manifest --------------------------------------------------------

MANIFEST = """
[project]
name = "demo"
src  = ["src"]

[[serve]]
name = "call"
kind = "http"
path = "/call"
port = 8080
graph = "demo.call:build"
[serve.variants]
loud  = { style = "demo.styles:LOUD",  suffix = "!" }
quiet = { style = "demo.styles:QUIET", suffix = "." }
"""


def test_variants_parse_as_a_table_of_bindings():
    m = Manifest.from_dict(_toml.loads(MANIFEST))
    assert m.src == ("src",)
    call = m.serve("call")
    assert call.variants == {
        "loud": {"style": "demo.styles:LOUD", "suffix": "!"},
        "quiet": {"style": "demo.styles:QUIET", "suffix": "."},
    }
    assert Manifest.from_dict({"project": {"name": "x"}}).src == (".",)


def test_variants_must_be_tables_and_never_on_an_asgi_mount():
    with pytest.raises(ManifestError, match="must be a table of factory parameters"):
        Manifest.from_dict(
            {"serve": [{"name": "c", "kind": "http", "graph": "a:b", "variants": {"x": 1}}]}
        )
    with pytest.raises(ManifestError, match="non-empty table"):
        Manifest.from_dict(
            {"serve": [{"name": "c", "kind": "http", "graph": "a:b", "variants": {}}]}
        )
    with pytest.raises(ManifestError, match="asgi.*cannot have variants"):
        Manifest.from_dict(
            {"serve": [{"name": "c", "kind": "asgi", "app": "a:b", "variants": {"x": {}}}]}
        )


# -- the factory ---------------------------------------------------------

LOUD = str.upper
QUIET = str.lower


def build(style, suffix: str):
    """A factory: bound once per variant, returns the graph."""

    @op(bound="sync")
    def shout(item: str = "") -> dict:
        return {"reply": style(item) + suffix}

    @graph
    def pipeline():
        src = ingress()
        loud = shout(item=src["item"])
        out = egress(item=loud["reply"])
        START >> src >> loud >> out >> END

    return pipeline


@graph
def plain():
    src = ingress()
    out = egress(item=src["item"])
    START >> src >> out >> END


@graph
def styled(style, suffix):
    """A `@graph` whose build-time parts are its own parameters: a static
    value lands in the body as-is, a `None` stays a runtime input."""
    src = ingress()
    loud = shout(item=src["item"], style=style, suffix=suffix)
    out = egress(item=loud["reply"])
    START >> src >> loud >> out >> END


@op(bound="sync")
def shout(item: str = "", style=None, suffix: str = "") -> dict:
    return {"reply": style(item) + suffix}


ME = __name__


def test_compile_graph_binds_the_factory_and_loads_entry_point_values():
    engine = compile_graph(f"{ME}:build", bind={"style": f"{ME}:LOUD", "suffix": "!"})
    assert isinstance(engine, Operon)
    # a value that does not read as module:attr is a literal
    engine = compile_graph(f"{ME}:build", bind={"style": f"{ME}:QUIET", "suffix": "a:b:c"})
    assert isinstance(engine, Operon)


def test_a_graph_binds_its_own_parameters():
    engine = compile_graph(f"{ME}:styled", bind={"style": f"{ME}:LOUD", "suffix": "!"})
    assert isinstance(engine, Operon)


def test_binding_a_parameter_the_graph_does_not_have_says_so():
    with pytest.raises(TypeError, match="has no parameter \\['style'\\]"):
        compile_graph(f"{ME}:plain", bind={"style": f"{ME}:LOUD"})
    with pytest.raises(TypeError, match="could not take \\['nope'\\]"):
        compile_graph(f"{ME}:build", bind={"nope": 1})


# -- the gate ------------------------------------------------------------


def _runner(transport, on_session):
    spec = ServeSpec(
        name="call",
        kind="memory",
        graph="x:y",
        max_inflight=8,
        variants={"loud": {}, "quiet": {}},
    )
    runner = ServeRunner(
        None,
        spec,
        transport=transport,
        variants={
            "loud": compile_graph(f"{ME}:build", bind={"style": f"{ME}:LOUD", "suffix": "!"}),
            "quiet": compile_graph(f"{ME}:build", bind={"style": f"{ME}:QUIET", "suffix": "."}),
        },
    )
    runner._on_session = on_session
    return runner


@pytest.mark.asyncio
async def test_the_session_picks_the_variant():
    transport = MemoryTransport()
    runner = _runner(transport, lambda s: RunRequest(variant=s.meta["v"]))
    a = transport.open(meta={"v": "loud"})
    b = transport.open(meta={"v": "quiet"})
    for s in (a, b):
        await s.feed("Hi")
        s.end_input()
    transport.stop()
    await runner.run()
    assert a.sent == ["HI!"] and b.sent == ["hi."]


@pytest.mark.asyncio
async def test_no_variant_and_an_unknown_variant_are_refused_at_the_door():
    transport = MemoryTransport()
    runner = _runner(transport, lambda s: RunRequest(variant=s.meta.get("v")))
    none = transport.open(meta={})
    wrong = transport.open(meta={"v": "shouty"})
    transport.stop()
    await runner.run()
    assert none.closed and none.sent == []
    assert wrong.closed and wrong.sent == []


# -- the application, end to end ----------------------------------------

PROJECT = """
from operonx.core import END, START, graph, op
from operonx.app.serve import egress, ingress
from styles import LOUD

def build(style, suffix: str):
    @op(bound="sync")
    def shout(item: str = "") -> dict:
        return {"reply": style(item) + suffix}

    @graph
    def pipeline():
        src = ingress()
        loud = shout(item=src["item"])
        out = egress(item=loud["reply"])
        START >> src >> loud >> out >> END

    return pipeline

def open_call(session):
    from operonx.app.serve import RunRequest
    return RunRequest(variant=session.meta["query"].get("v"))
"""

STYLES = """
LOUD = str.upper
QUIET = str.lower
"""


@pytest.fixture
def project(tmp_path, monkeypatch):
    name = f"door_{uuid.uuid4().hex[:6]}"
    src = tmp_path / "src"
    src.mkdir()
    (src / f"{name}.py").write_text(textwrap.dedent(PROJECT), encoding="utf-8")
    (src / "styles.py").write_text(textwrap.dedent(STYLES), encoding="utf-8")
    (tmp_path / "operonx.toml").write_text(
        textwrap.dedent(f"""
        [project]
        name = "door"
        src  = ["src"]

        [[serve]]
        name  = "call"
        kind  = "http"
        path  = "/call"
        port  = 8124
        graph = "{name}:build"
        on_session = "{name}:open_call"
        [serve.variants]
        loud  = {{ style = "styles:LOUD",  suffix = "!" }}
        quiet = {{ style = "styles:QUIET", suffix = "." }}
    """),
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    yield name, tmp_path
    sys.modules.pop(name, None)
    sys.modules.pop("styles", None)
    sys.path[:] = [p for p in sys.path if not p.startswith(str(tmp_path))]


def test_src_roots_go_on_sys_path_and_variants_are_graphs(project):
    name, root = project
    app = Application.find(root)
    d = app.describe()
    assert d["services"][0]["variants"] == ["loud", "quiet"]
    assert [(g["name"], g["bind"]) for g in d["graphs"]] == [
        ("build[loud]", {"style": "styles:LOUD", "suffix": "!"}),
        ("build[quiet]", {"style": "styles:QUIET", "suffix": "."}),
    ]
    assert name not in sys.modules  # describe() imported nothing
    app.bootstrap()
    assert str(root / "src") in sys.path
    engine = next(g for g in app.graphs if g.name == "build[quiet]").compile()
    assert isinstance(engine, Operon)


def test_one_door_serves_every_variant(project):
    pytest.importorskip("starlette")
    from starlette.testclient import TestClient

    _, root = project
    app = Application.find(root)
    with TestClient(app.asgi(port=8124)) as client:
        assert client.post("/call?v=loud", json="hey").json() == "HEY!"
        assert client.post("/call?v=quiet", json="HEY").json() == "hey."
        # refused at the door: no run, so no reply — the http door says 500
        assert client.post("/call?v=nope", json="x").status_code == 500
        assert client.post("/call", json="x").status_code == 500
