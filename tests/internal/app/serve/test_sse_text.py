"""COST_PLAN round 2, C39: an ``http(..., sse=True)`` door streams a
model's text as it comes, whatever the caller's ``Accept``: a string item
is its text, anything else JSON, and ``event: done`` ends the stream. A
doorless graph that sends with ``egress`` answers only with what it sent.
"""

import json

import pytest

from operonx.app import Application, Service, http
from operonx.app.doors import sends
from operonx.app.serve import egress
from operonx.app.serve.app import SSE_DONE, sse_text_frame
from operonx.core import END, START, graph
from operonx.core.ops import op

starlette = pytest.importorskip("starlette")
from starlette.testclient import TestClient  # noqa: E402

pytestmark = pytest.mark.unit


@op(bound="io", transient=True)
async def pieces(topic: str):
    for piece in ("SUMMARY:", " two", "", " lines\nhere"):
        yield {"piece": f"{piece}" if topic == "text" else {"n": len(piece)}}


@graph
def summary(topic):
    p = pieces(topic=topic)
    out = egress(item=p["piece"])
    START >> p >> out >> END


@op
def plain(topic: str) -> dict:
    return {"topic": topic}


@graph
def quiet(topic):
    q = plain(topic=topic)
    START >> q >> END


def _client(sse=True, flow=summary):
    door = http("GET", "/summary/{topic}", port=8902, sse=sse)
    return TestClient(Application("t", services=[Service("s", door, graph=flow)]).asgi())


def _events(body: str):
    """(event name, data) per event, the way an EventSource reads them."""
    out = []
    for block in body.split("\n\n"):
        lines = [line for line in block.split("\n") if line and not line.startswith("id:")]
        if not lines:
            continue
        name = next((line[7:] for line in lines if line.startswith("event: ")), "message")
        data = "\n".join(line[6:] for line in lines if line.startswith("data: "))
        out.append((name, data))
    return out


def test_an_sse_door_streams_text_without_an_accept_header():
    with _client() as client:
        reply = client.get("/summary/text")
    assert reply.status_code == 200
    assert reply.headers["content-type"].startswith("text/event-stream")
    assert _events(reply.text) == [
        ("message", "SUMMARY:"),
        ("message", " two"),  # the leading space is the model's
        ("message", " lines\nhere"),  # a line per data:, joined back
        ("done", ""),
    ]  # the empty piece made no event; nothing after the items but done


def test_an_sse_door_sends_anything_but_a_string_as_json():
    with _client() as client:
        events = _events(client.get("/summary/json").text)
    assert [json.loads(d) for n, d in events if n == "message"] == [
        {"n": 8},
        {"n": 4},
        {"n": 0},
        {"n": 11},
    ]
    assert events[-1] == ("done", "")


def test_without_sse_the_stream_is_json_and_has_no_done_event():
    with _client(sse=False) as client:
        body = client.get("/summary/text", headers={"accept": "text/event-stream"}).text
    events = _events(body)
    assert [json.loads(d) for _, d in events] == ["SUMMARY:", " two", "", " lines\nhere"]
    assert "event: done" not in body


def test_a_graph_that_sends_does_not_also_reply_with_its_outputs():
    with _client(sse=False) as client:
        body = client.get("/summary/text", headers={"accept": "text/event-stream"}).text
    assert '"sent"' not in body and "outputs" not in body


def test_a_quiet_graph_on_an_sse_door_answers_its_outputs_then_done():
    with _client(flow=quiet) as client:
        events = _events(client.get("/summary/x").text)
    assert events == [("message", json.dumps({"topic": "x"})), ("done", "")]


def test_sends_finds_an_egress():
    assert sends(summary) and not sends(quiet)


def test_the_text_frame():
    assert sse_text_frame("a\nb", 3) == "id: 3\ndata: a\ndata: b\n\n"
    assert sse_text_frame("") == ""
    assert sse_text_frame({"k": 1}) == 'data: {"k": 1}\n\n'
    assert SSE_DONE == "event: done\ndata: \n\n"
