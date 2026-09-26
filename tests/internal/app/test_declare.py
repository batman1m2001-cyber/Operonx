"""The application declared in Python.

`Service(...)` builds the same `ServeSpec` a `[[serve]]` block parses to,
with the graph, the hooks and the bound objects in hand instead of
`module:attr` strings; `Application(name, services=, jobs=)` is the
composition root over them; `operonx.toml` can point at that object with
`[project] app` so the CLIs and the studio find it the usual way. The
graph's signature is the door's contract; a door op declares itself;
a listener's `workers=` and a service's `on_startup=` shape the process.
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


def warm_greet():
    STARTED.append("greet")


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
                on_session=pick,
                on_startup=[warm_greet],
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
    assert spec.on_startup == (warm_greet,) and spec.workers == 1
    assert app.manifest.on_startup == (warm,)
    assert [j.name for j in app.jobs] == ["score_two"]


def test_describe_names_objects_the_way_a_manifest_would():
    d = build_app_object().describe()
    greet = next(s for s in d["services"] if s["name"] == "greet")
    assert greet["graph"] == f"{__name__}:styled"
    assert greet["on_session"] == f"{__name__}:pick"
    assert greet["variants"] == ["loud", "quiet"]
    assert greet["on_startup"] == [f"{__name__}:warm_greet"] and greet["workers"] == 1
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
        assert STARTED == ["warm", "greet"]
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


# -- the door's contract is the graph's signature -------------------------


def test_what_moved_out_of_the_service_says_where_it_went():
    with pytest.raises(ManifestError, match="inputs= is gone — the graph's own runtime parameters"):
        Service("flat", http("POST", "/flat"), graph=flat, inputs=["item", "tag"])
    with pytest.raises(ManifestError, match='ingress/egress= is gone — .*@op\\(door="ingress"\\)'):
        Service("flat", http("POST", "/flat"), graph=flat, ingress=["src"], egress=["out"])


def test_a_door_op_says_what_it_is():
    @op(door="egress")
    def played(x=None):
        return {}

    assert played().door == "egress"
    assert ingress().door == "ingress" and egress().door == "egress"
    assert shout().door is None
    with pytest.raises(ValueError, match="door"):
        op(door="side")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "built, refused",
    [
        ({"item": "a", "tag": "!"}, False),  # exactly the graph's parameters
        ({"item": "a"}, True),  # forgot `tag`
        ({"item": "a", "tag": "!", "x": 1}, True),  # `x` is not a parameter
    ],
)
async def test_the_hook_must_build_the_graphs_parameters(built, refused):
    from operonx.app.serve import MemoryTransport, ServeRunner
    from operonx.app.serve.app import engines_for

    hook = lambda s: RunRequest(inputs=dict(built))  # noqa: E731
    spec = Service("flat", websocket("/flat"), max_inflight=4, graph=flat, on_session=hook)
    engine = engines_for(spec)["flat"]
    assert set(engine.inputs_expected) == {"item", "tag"}
    transport = MemoryTransport()
    runner = ServeRunner(engine, spec, transport=transport)
    runner._on_session = hook
    assert (runner._request_for(object()) is None) is refused


# -- the process: workers and startup hooks --------------------------------


def test_a_listener_declares_its_workers_and_services_on_it_agree():
    call = Service(
        "call",
        websocket("/ws", port=8140, workers=4),
        max_inflight=8,
        graph=scored,
        variants={"a": dict(threshold=1)},
    )
    assert call.workers == 4
    with pytest.raises(ManifestError, match="workers=0"):
        websocket("/ws", workers=0)
    with pytest.raises(
        ManifestError, match="share 0.0.0.0:8140 but declare workers=4 and workers=1"
    ):
        Application("x", services=[call, Service("admin", asgi("/", port=8140), app=object())])


def test_the_plan_is_one_entry_per_listener():
    from operonx.app.serve.app import plan

    app = build_app_object(
        services=[
            Service(
                "call",
                websocket("/ws", port=8141, workers=3),
                max_inflight=8,
                graph=scored,
                variants={"a": dict(threshold=1)},
            ),
            Service(
                "greet",
                http("POST", "/greet", port=8142),
                graph=scored,
                variants={"a": dict(threshold=1)},
            ),
            Service(
                "echo",
                http("POST", "/echo", port=8142),
                graph=scored,
                variants={"a": dict(threshold=1)},
            ),
        ]
    )
    assert [
        (addr[1], [s.name for s in group], workers) for addr, group, workers in plan(app.manifest)
    ] == [
        (8141, ["call"], 3),
        (8142, ["greet", "echo"], 1),
    ]
    assert [a[1] for a, _, _ in plan(app.manifest, only=["echo"])] == [8142]
    with pytest.raises(ManifestError, match="no serve entry named: nope"):
        plan(app.manifest, only=["nope"])


def test_a_services_startup_hooks_run_only_where_it_is_served():
    pytest.importorskip("starlette")
    from starlette.testclient import TestClient

    STARTED.clear()
    app = build_app_object(
        services=[
            Service(
                "greet",
                http("POST", "/greet", port=8150),
                graph=styled,
                variants={"loud": dict(style=LOUD, suffix="!")},
                on_session=pick,
                on_startup=[warm_greet],
            ),
            Service("admin", asgi("/", port=8151), app=_tiny_asgi),
        ]
    )
    with TestClient(app.asgi(port=8151)):
        assert STARTED == ["warm"]  # the application's hook, not greet's
    STARTED.clear()
    with TestClient(app.asgi(port=8150, startup=False)):
        assert STARTED == []  # startup=False: none at all


async def _tiny_asgi(scope, receive, send):  # an admin app that answers nothing
    if scope["type"] == "lifespan":
        while True:
            message = await receive()
            if message["type"] == "lifespan.startup":
                await send({"type": "lifespan.startup.complete"})
            elif message["type"] == "lifespan.shutdown":
                await send({"type": "lifespan.shutdown.complete"})
                return


def test_workers_need_a_manifest_each_worker_can_load():
    from operonx.app.serve.app import serve_manifest

    app = build_app_object(
        services=[
            Service(
                "call",
                websocket("/ws", port=8160, workers=2),
                max_inflight=8,
                graph=scored,
                variants={"a": dict(threshold=1)},
            ),
        ],
        root="/nonexistent-project",
    )
    with pytest.raises(ManifestError, match="workers > 1 needs an operonx.toml"):
        serve_manifest(app.manifest)


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


def test_a_worker_loads_its_listener_from_the_project(pointed, monkeypatch):
    """What each process of a pooled listener runs: the application found
    again from the project root, that listener's app built from it."""
    pytest.importorskip("starlette")
    from starlette.testclient import TestClient

    from operonx.app.serve.app import worker_app

    _, root = pointed
    monkeypatch.setenv("OPERONX_SERVE_ROOT", str(root))
    monkeypatch.setenv("OPERONX_SERVE_LISTENER", "0.0.0.0:8131")
    monkeypatch.setenv("OPERONX_SERVE_ONLY", "echo")
    with TestClient(worker_app()) as client:
        assert client.post("/echo", json="hi").json() == "hi"


def test_serve_runs_a_pooled_listener_as_worker_processes(tmp_path):
    """The real thing: `APP.serve()` in a subprocess, a listener with
    workers=2, answering over HTTP, and gone when the process is."""
    pytest.importorskip("uvicorn")
    import os
    import socket
    import subprocess
    import time
    import urllib.request

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    name = f"pooled_{uuid.uuid4().hex[:6]}"
    (tmp_path / f"{name}.py").write_text(
        textwrap.dedent(f"""
        import os
        from operonx.app import Application, Service, http
        from operonx.app.serve import egress, ingress
        from operonx.core import END, START, graph, op

        @op(bound="sync")
        def pid(item=None):
            return {{"out": os.getpid()}}

        @graph
        def whoami():
            src = ingress()
            p = pid(item=src["item"])
            out = egress(item=p["out"])
            START >> src >> p >> out >> END

        APP = Application("pooled", services=[
            Service("who", http("POST", "/who", port={port}, host="127.0.0.1", workers=2), graph=whoami),
        ])
        """),
        encoding="utf-8",
    )
    (tmp_path / "operonx.toml").write_text(
        f'[project]\nname = "pooled"\nsrc = ["."]\napp = "{name}:APP"\n', encoding="utf-8"
    )
    proc = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "from operonx.app import Application; Application.find('.').serve()",
        ],
        cwd=tmp_path,
        env={**os.environ, "PYTHONPATH": str(tmp_path)},
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        pids = set()
        deadline = time.time() + 30
        while time.time() < deadline and not pids:
            try:
                req = urllib.request.Request(
                    f"http://127.0.0.1:{port}/who",
                    data=b'"x"',
                    method="POST",
                    headers={"content-type": "application/json"},
                )
                pids.add(int(urllib.request.urlopen(req, timeout=2).read()))
            except OSError:
                time.sleep(0.3)
        assert pids, "the pooled listener never answered"
        assert proc.pid not in pids  # served by a worker, not the main process
    finally:
        proc.terminate()
        proc.wait(timeout=15)
    time.sleep(1)
    with socket.socket() as s:
        assert s.connect_ex(("127.0.0.1", port)) != 0  # the workers went with it


def test_the_toml_block_says_the_same():
    from operonx.app.manifest import Manifest

    base = {"name": "c", "kind": "websocket", "path": "/ws", "graph": "a:b", "max_inflight": 4}
    spec = Manifest.from_dict(
        {"serve": [{**base, "workers": 3, "on_startup": ["app.startup:warm"]}]}
    ).serve("c")
    assert spec.workers == 3 and spec.on_startup == ("app.startup:warm",)
    with pytest.raises(ManifestError, match="`inputs` is gone"):
        Manifest.from_dict({"serve": [{**base, "inputs": ["x"]}]})
    with pytest.raises(ManifestError, match='`ingress` is gone — .*@op\\(door="ingress"\\)'):
        Manifest.from_dict({"serve": [{**base, "ingress": ["src"]}]})
    with pytest.raises(ManifestError, match="workers 0"):
        Manifest.from_dict({"serve": [{**base, "workers": 0}]})
    with pytest.raises(ManifestError, match="not a `module:attr`"):
        Manifest.from_dict({"serve": [{**base, "on_startup": ["warm"]}]})
