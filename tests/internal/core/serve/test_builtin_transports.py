"""The built-in http and websocket transports, over a real ASGI server.

These come after `test_serve_layer.py`, and that order is deliberate: the
protocol was proven with a third-party transport first, so the built-ins
cannot have quietly defined the interface.
"""

import pytest

from operonx.core import END, START, Operon, graph
from operonx.core.manifest import Manifest, ServeSpec
from operonx.core.ops import op
from operonx.core.serve import egress, ingress
from operonx.core.serve.app import build_app, engine_for

starlette = pytest.importorskip("starlette")
from starlette.testclient import TestClient  # noqa: E402


@op(bound="sync")
def upper(item=None) -> dict:
    return {"loud": str(item).upper()}


@graph
def loud_pipeline():
    src = ingress()
    up = upper(item=src["item"])
    out = egress(item=up["loud"])
    START >> src >> up >> out >> END


@op(bound="sync")
def swallow(item=None) -> dict:
    """Produces nothing a caller can see — the empty-200 failure shape."""
    return {}


@graph
def silent_pipeline():
    src = ingress()
    s = swallow(item=src["item"])
    START >> src >> s >> END


ENGINE = Operon(loud_pipeline)
SILENT = Operon(silent_pipeline)


def _spec(**kw) -> ServeSpec:
    base = dict(name="t", kind="http", graph="x:y", path="/go", method="POST")
    base.update(kw)
    return ServeSpec(**base)


def test_http_request_becomes_a_run_and_a_response():
    app = build_app((_spec(),), engines={"t": ENGINE})
    with TestClient(app) as client:
        assert client.post("/go", json="hello").json() == "HELLO"


def test_a_run_that_produces_nothing_is_a_500_not_an_empty_200():
    """The failure the plan exists to prevent.

    Op exceptions are caught by the scheduler and logged rather than
    raised, so from the transport's side a failed run looks like a run
    that produced nothing. For one caller waiting on one request, that is
    a failure and must not be dressed up as success.
    """
    app = build_app((_spec(name="s", path="/silent"),), engines={"s": SILENT})
    with TestClient(app) as client:
        response = client.post("/silent", json="hello")
    assert response.status_code == 500
    assert "produced no output" in response.json()["error"]


def test_websocket_connection_is_one_long_lived_run():
    spec = _spec(name="w", kind="websocket", path="/ws", session="per_connection",
                 max_inflight=64)
    app = build_app((spec,), engines={"w": ENGINE})
    with TestClient(app) as client:
        with client.websocket_connect("/ws") as ws:
            for word in ("a", "b", "c"):
                ws.send_text(word)
            assert [ws.receive_text() for _ in range(3)] == ["A", "B", "C"]


def test_asgi_kind_mounts_a_foreign_app_untouched():
    """operonx must never implement CRUD — only mount someone who does."""
    spec = ServeSpec(name="admin", kind="asgi", path="/admin",
                     app="tests.internal.core.serve.test_builtin_transports:admin_app")
    with TestClient(build_app((spec,))) as client:
        assert client.get("/admin/healthz").json() == {"ok": True}


from starlette.applications import Starlette  # noqa: E402
from starlette.responses import JSONResponse  # noqa: E402
from starlette.routing import Route  # noqa: E402

admin_app = Starlette(routes=[
    Route("/healthz", lambda request: JSONResponse({"ok": True})),
])


def test_endpoints_on_one_port_share_a_listener():
    m = Manifest.from_dict({"schema": 2, "serve": [
        {"name": "a", "kind": "http", "path": "/a", "port": 8080,
         "graph": "tests.internal.core.serve.test_builtin_transports:loud_pipeline"},
        {"name": "b", "kind": "http", "path": "/b", "port": 8080,
         "graph": "tests.internal.core.serve.test_builtin_transports:loud_pipeline"},
    ]})
    (addr, specs), = m.listeners().items()
    app = build_app(specs)
    with TestClient(app) as client:
        assert client.post("/a", json="x").json() == "X"
        assert client.post("/b", json="y").json() == "Y"


def test_engine_for_declares_every_graph_parameter_as_an_input():
    """A graph compiled without its params keeps the literal defaults.

    That is the failure where a deep op holds None forever, so the
    parameters are declared rather than discovered at the first call.
    """
    spec = _spec(graph="tests.internal.core.serve.test_builtin_transports:loud_pipeline")
    assert isinstance(engine_for(spec), Operon)
