"""DX_PLAN X5: a health route on every listener, --port for one listener,
and what --reload watches."""

from __future__ import annotations

import pytest
from starlette.testclient import TestClient

from operonx import END, START, graph, op
from operonx.app import Application, ManifestError, Service, http, webhook
from operonx.app.serve import egress, ingress
from operonx.app.serve.app import serve_manifest
from operonx.cli.serve import _snapshot

pytestmark = pytest.mark.unit


@op
def echo(item: dict = None) -> dict:
    return {"out": item}


@graph
def g():
    src = ingress()
    e = echo(item=src["item"])
    out = egress(item=e["out"])
    START >> src >> e >> out >> END


def test_every_listener_answers_healthz():
    services = [
        Service("a", http("POST", "/a", port=8841), graph=g),
        Service("b", webhook("/b", port=8841), graph=g),
    ]
    with TestClient(Application("t", services=services).asgi()) as client:
        reply = client.get("/healthz")
    assert reply.status_code == 200
    assert reply.json() == {"ok": True, "services": ["a", "b"]}


def test_a_service_on_healthz_keeps_it():
    service = Service("health", http("GET", "/healthz", port=8842), graph=g)
    with TestClient(Application("t", services=[service]).asgi()) as client:
        reply = client.get("/healthz")
    assert reply.json() != {"ok": True, "services": ["health"]}


def test_port_binds_one_listener_only():
    app = Application(
        "t",
        services=[
            Service("a", http("POST", "/a", port=8843), graph=g),
            Service("b", http("POST", "/b", port=8844), graph=g),
        ],
    )
    with pytest.raises(ManifestError, match="--port binds one listener"):
        serve_manifest(app.manifest, port=9000)


def test_reload_watches_project_files_not_environments(tmp_path):
    (tmp_path / "app.py").write_text("x = 1\n")
    (tmp_path / ".venv" / "lib").mkdir(parents=True)
    (tmp_path / ".venv" / "lib" / "dep.py").write_text("y = 1\n")
    seen = _snapshot(tmp_path)
    assert [p.rsplit("/", 1)[1] for p in seen] == ["app.py"]
