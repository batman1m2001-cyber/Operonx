"""``Service(body=fn)``: one route's request contract, its own.

Several routes share one feature graph, each binding its branch with
``variants``. Without ``body=``, each route's contract is the shared
graph's parameters: optional, and the same for every route — so
``/tilt`` would accept ``/send``'s fields and run without its own
``client_id``. With ``body=``, a plain function's signature (names, which
are required, defaults) is the route's contract again, checked before any
run.
"""

from __future__ import annotations

import pytest
from starlette.testclient import TestClient

from operonx import END, START, graph, op
from operonx.app import Application, ManifestError, Service, http, websocket
from operonx.app.play import Bridge
from operonx.app.serve import egress, ingress

pytestmark = pytest.mark.unit

SEEN: list = []


@pytest.fixture(autouse=True)
def _clear():
    SEEN.clear()
    yield
    SEEN.clear()


@op
def act(action, client_id=None, weight=None, note=None) -> dict:
    SEEN.append(action)
    return {"action": action, "client_id": client_id, "weight": weight, "note": note}


@graph
def allocation_flow(action, client_id=None, weight=0.5, note=None):
    a = act(action=action, client_id=client_id, weight=weight, note=note)
    START >> a >> END


def tilt_body(client_id, weight=0.25):
    """``/tilt``'s contract: a client, and a weight it may leave out."""


@op
def send_body(client_id: str, note: str) -> dict:
    """A route's old one-op handler serves as its contract: never called."""
    raise AssertionError("body= is a signature, not a handler")


def _route(name, body=None, port=8961, **kw):
    if body is not None:
        kw["body"] = body
    return Service(
        name,
        http("POST", f"/{name}", port=port),
        graph=allocation_flow,
        variants={name: {"action": name}},
        **kw,
    )


def _client(*services):
    return TestClient(Application("t", services=list(services)).asgi())


# ── a request ────────────────────────────────────────────────────────────


def test_each_route_runs_its_branch_with_its_own_defaults_then_the_graphs():
    with _client(_route("tilt", tilt_body), _route("send", send_body)) as client:
        tilt = client.post("/tilt", json={"client_id": "c1"})
        send = client.post("/send", json={"client_id": "c2", "note": "hi"})
    assert tilt.status_code == 200 and send.status_code == 200
    # weight: the route's default (0.25), not the graph's (0.5); note: the graph's
    assert tilt.json() == {"action": "tilt", "client_id": "c1", "weight": 0.25, "note": None}
    assert send.json() == {"action": "send", "client_id": "c2", "weight": 0.5, "note": "hi"}


def test_without_body_a_shared_graph_lets_a_route_run_without_its_required_field():
    """What body= is for: the same request, refused only with it."""
    with _client(_route("tilt")) as client:
        assert client.post("/tilt", json={"weight": 1}).status_code == 200
    with _client(_route("tilt", tilt_body)) as client:
        reply = client.post("/tilt", json={"weight": 1})
    assert reply.status_code == 400 and reply.json()["field"] == "client_id"


@pytest.mark.parametrize(
    "body, field",
    [
        ({"weight": 1}, "client_id"),  # required by the route, left out
        ({"client_id": "c", "note": "x"}, "note"),  # a sibling route's field
        (
            {"client_id": "c", "action": "send"},
            "action",
        ),  # the branch is the route's, not the caller's
    ],
)
def test_a_request_outside_the_routes_contract_is_a_400_and_mints_no_run(body, field):
    with _client(_route("tilt", tilt_body)) as client:
        reply = client.post("/tilt", json=body)
    assert reply.status_code == 400
    assert reply.json()["field"] == field and reply.json()["endpoint"] == "tilt"
    assert "x-operonx-trace-id" not in reply.headers
    assert SEEN == []


def test_the_query_fills_the_contract_and_ignores_what_it_does_not_take():
    with _client(_route("tilt", tilt_body)) as client:
        reply = client.post("/tilt?weight=0.9&note=x", json={"client_id": "c"})
    assert reply.json() == {"action": "tilt", "client_id": "c", "weight": "0.9", "note": None}


def test_a_manifest_names_body_by_module_attr():
    with _client(_route("tilt", f"{__name__}:tilt_body")) as client:
        assert client.post("/tilt", json={"weight": 1}).json()["field"] == "client_id"
        assert client.post("/tilt", json={"client_id": "c"}).status_code == 200


def test_the_playground_form_shows_the_routes_fields():
    app = Application("t", services=[_route("tilt", tilt_body)])
    door = Bridge(app, lambda _m: None).describe()["doors"][0]
    assert door["inputs"] == ["client_id", "weight"]
    assert door["required"] == ["client_id"] and door["defaults"] == {"weight": 0.25}


# ── at build ─────────────────────────────────────────────────────────────


def _build(service):
    with _client(service):
        pass


def body_binds_action(action, client_id):
    pass


def body_unknown(client_id, colour):
    pass


@graph
def needs_two(action, client_id, account, weight=0.5):
    a = act(action=action, client_id=client_id, weight=weight, note=account)
    START >> a >> END


@pytest.mark.parametrize(
    "service, match",
    [
        (_route("tilt", body_binds_action), "takes 'action', which graph .* leaves no parameter"),
        (_route("tilt", body_unknown), "takes 'colour'"),
        (
            Service(
                "tilt",
                http("POST", "/tilt", port=8962),
                graph=needs_two,
                variants={"tilt": {"action": "tilt"}},
                body=tilt_body,
            ),
            "needs 'account', which body= does not take and which has no default",
        ),
    ],
)
def test_a_body_that_does_not_fit_the_graph_fails_the_build(service, match):
    with pytest.raises(ManifestError, match=match):
        _build(service)


@graph
def door_flow(client_id=None):
    src = ingress()
    out = egress(item=src["item"])
    START >> src >> out >> END


def test_a_graph_with_doors_takes_no_body():
    service = Service("d", http("POST", "/d", port=8963), graph=door_flow, body=tilt_body)
    with pytest.raises(ManifestError, match="for a graph without doors"):
        _build(service)


def test_body_is_refused_where_it_cannot_mean_anything():
    def spread(*fields):
        pass

    with pytest.raises(ManifestError, match=r"takes \*fields"):
        _route("tilt", spread)
    with pytest.raises(ManifestError, match="not a websocket"):
        Service(
            "w", websocket("/w", port=8964), graph=allocation_flow, max_inflight=4, body=tilt_body
        )
    with pytest.raises(ManifestError, match="pick one"):
        _route("tilt", tilt_body, input="note")
