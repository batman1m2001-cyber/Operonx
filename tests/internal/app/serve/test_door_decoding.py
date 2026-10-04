"""Every door decodes its payload the same way (roadmap C11, dogfood F08/F09).

Up to 1.14 an HTTP door decoded a JSON body and a websocket door handed
the graph each text frame as a raw string, so one graph served on both
broke on the websocket and its client heard nothing. A body that was not
JSON ran the graph with the raw string instead of answering 400.

Now a door's ``codec`` decides: ``"json"`` (the default) decodes every
body and text frame, ``"text"`` passes text through. A payload the codec
cannot read is refused before a run is minted (HTTP 400, or an error
frame on the socket). HTTP replies carry the run's ``x-operonx-trace-id``.
"""

import pytest

from operonx.app import Service, http, webhook, websocket
from operonx.app.manifest import ManifestError, ServeSpec
from operonx.app.serve import egress, ingress
from operonx.app.serve.app import build_app
from operonx.core import END, START, Operon, graph
from operonx.core.ops import op

starlette = pytest.importorskip("starlette")
from starlette.testclient import TestClient  # noqa: E402

SEEN: list = []


@op(bound="sync")
def describe(item=None) -> dict:
    SEEN.append(item)
    shown = len(item) if isinstance(item, bytes) else item
    return {"reply": {"type": type(item).__name__, "item": shown}}


@graph
def describe_flow():
    src = ingress()
    d = describe(item=src["item"])
    out = egress(item=d["reply"])
    START >> src >> d >> out >> END


@op(bound="sync")
def boom() -> dict:
    raise RuntimeError("secret detail sk-live-1")


@graph
def failing_flow():
    b = boom()
    START >> b >> END


ENGINE = Operon(describe_flow)
FAILING = Operon(failing_flow)


@pytest.fixture(autouse=True)
def _clear():
    SEEN.clear()


def _http(**options) -> ServeSpec:
    return ServeSpec(name="h", kind="http", graph="x:y", path="/go", options=options)


def _ws(**options) -> ServeSpec:
    return ServeSpec(
        name="w",
        kind="websocket",
        graph="x:y",
        path="/ws",
        session="per_connection",
        max_inflight=8,
        options=options,
    )


# ── websocket ──────────────────────────────────────────────────────────


def test_a_websocket_json_frame_reaches_the_graph_as_a_dict():
    with TestClient(build_app((_ws(),), engines={"w": ENGINE})) as client:
        with client.websocket_connect("/ws") as ws:
            ws.send_json({"topic": "x"})
            assert ws.receive_json() == {"type": "dict", "item": {"topic": "x"}}
    assert SEEN == [{"topic": "x"}]


def test_a_malformed_frame_gets_an_error_frame_and_the_socket_lives_on():
    with TestClient(build_app((_ws(),), engines={"w": ENGINE})) as client:
        with client.websocket_connect("/ws") as ws:
            ws.send_text("{oops")
            error = ws.receive_json()
            assert error["error"].startswith("frame is not JSON")
            ws.send_json([1, 2])
            assert ws.receive_json() == {"type": "list", "item": [1, 2]}
    assert SEEN == [[1, 2]]  # the bad frame never reached the graph


def test_codec_text_passes_text_frames_through():
    with TestClient(build_app((_ws(codec="text"),), engines={"w": ENGINE})) as client:
        with client.websocket_connect("/ws") as ws:
            ws.send_text("{not json, and fine}")
            assert ws.receive_json() == {"type": "str", "item": "{not json, and fine}"}


def test_bytes_frames_stay_bytes():
    with TestClient(build_app((_ws(),), engines={"w": ENGINE})) as client:
        with client.websocket_connect("/ws") as ws:
            ws.send_bytes(b"\x00\x01")
            assert ws.receive_json()["type"] == "bytes"


def test_a_run_that_fails_without_sending_gets_an_error_frame():
    """The client used to hear nothing at all."""
    with TestClient(build_app((_ws(),), engines={"w": FAILING})) as client:
        with client.websocket_connect("/ws") as ws:
            error = ws.receive_json()
    assert error["error"] == "the graph failed before it sent anything"
    assert error["trace_id"]
    assert "sk-live-1" not in str(error)


# ── http ───────────────────────────────────────────────────────────────


def test_a_malformed_json_body_is_a_400_and_starts_no_run():
    app = build_app((_http(),), engines={"h": ENGINE})
    with TestClient(app) as client:
        response = client.post(
            "/go", content=b"{oops", headers={"content-type": "application/json"}
        )
    assert response.status_code == 400
    assert response.json()["error"].startswith("body is not JSON")
    assert "x-operonx-trace-id" not in response.headers  # no run, no trace
    assert SEEN == []


def test_an_empty_body_is_no_payload():
    with TestClient(build_app((_http(),), engines={"h": ENGINE})) as client:
        response = client.post("/go")
    assert response.status_code == 200
    assert response.json() == {"type": "NoneType", "item": None}


def test_codec_text_takes_the_body_as_text():
    with TestClient(build_app((_http(codec="text"),), engines={"h": ENGINE})) as client:
        assert client.post("/go", content=b"{oops").json() == {"type": "str", "item": "{oops"}


def test_http_replies_carry_the_trace_id():
    with TestClient(build_app((_http(),), engines={"h": ENGINE})) as client:
        ok = client.post("/go?trace_id=abc123", json={"a": 1})
    assert ok.status_code == 200
    assert ok.headers["x-operonx-trace-id"] == "abc123"

    with TestClient(build_app((_http(),), engines={"h": FAILING})) as client:
        failed = client.post("/go", json={"a": 1})
    assert failed.status_code == 500
    assert failed.headers["x-operonx-trace-id"]
    # In the body too: a client that logs only the body can still find the run.
    assert failed.json()["trace_id"] == failed.headers["x-operonx-trace-id"]


def test_a_webhook_refuses_a_malformed_body_and_names_its_run():
    spec = ServeSpec(name="hook", kind="webhook", graph="x:y", path="/hook")
    with TestClient(build_app((spec,), engines={"hook": ENGINE})) as client:
        bad = client.post("/hook", content=b"{oops")
        good = client.post("/hook", json={"a": 1})
    assert bad.status_code == 400
    assert good.status_code == 202
    assert good.headers["x-operonx-trace-id"] == good.json()["run_id"]


# ── declaring it ───────────────────────────────────────────────────────


def test_listeners_take_a_codec():
    assert (
        Service("w", websocket("/ws", codec="text"), graph=describe_flow, max_inflight=4).options[
            "codec"
        ]
        == "text"
    )
    assert (
        Service("h", http("POST", "/h"), graph=describe_flow).options.get("codec", "json") == "json"
    )
    assert webhook("/hook", codec="text").options == {"codec": "text"}


def test_an_unknown_codec_is_refused_where_it_is_declared():
    with pytest.raises(ManifestError, match="codec"):
        websocket("/ws", codec="xml")
    with pytest.raises(ManifestError, match="codec"):
        build_app((_http(codec="xml"),), engines={"h": ENGINE})
