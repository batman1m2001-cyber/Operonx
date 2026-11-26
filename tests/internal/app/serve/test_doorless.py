"""Doorless services (DOORLESS_SERVICES_PLAN, gate G1): a graph without
``ingress``/``egress`` is served by its signature. Its parameters are what
the caller sends; its outputs are the reply.

A graph with doors is untouched — every door test elsewhere in this folder
is the proof of that.
"""

from __future__ import annotations

import json
import time

import pytest
from starlette.testclient import TestClient

from operonx import END, START, Operon, graph, op
from operonx.app import Application, Job, Service, http, schedule, webhook, websocket
from operonx.app.doors import BindError, bind_item, has_doors, serve_inputs
from operonx.app.serve import RunRequest, current_session, egress, ingress

pytestmark = pytest.mark.unit

SEEN: list = []


@pytest.fixture(autouse=True)
def _clear():
    SEEN.clear()
    yield
    SEEN.clear()


@op
def answer(question: str, k: int) -> dict:
    SEEN.append((question, k))
    return {"answer": f"{question}?", "k": k}


@graph
def chat_flow(question, k=3):
    a = answer(question=question, k=k)
    START >> a >> END


@op
def boom(question: str) -> dict:
    SEEN.append(question)
    raise RuntimeError("secret detail sk-live-1")


@graph
def failing_flow(question):
    b = boom(question=question)
    START >> b >> END


class NotAllowed(PermissionError):
    http_status = 403


@op
def guarded(question: str) -> dict:
    SEEN.append(question)
    if question == "secret":
        raise NotAllowed("only the owner reads this")
    if question == "odd":
        raise ValueError("a bug, not a refusal")
    return {"answer": question}


@graph
def guarded_flow(question):
    g = guarded(question=question)
    START >> g >> END


@op
def stamp() -> dict:
    SEEN.append("tick")
    return {"swept": True}


@graph
def sweep():
    s = stamp()
    START >> s >> END


@op
def stamp_tick(tick: int) -> dict:
    SEEN.append(tick)
    return {"tick": tick}


@graph
def sweep_with_tick(tick):
    s = stamp_tick(tick=tick)
    START >> s >> END


@op
def shout(item: dict = None) -> dict:
    return {"said": item}


@graph
def door_flow():
    src = ingress()
    s = shout(item=src["item"])
    out = egress(item=s["said"])
    START >> src >> s >> out >> END


def _app(*services):
    return Application("t", services=list(services)).asgi()


def _chat(**kw):
    return Service("chat", http("POST", "/chat", port=8901), graph=chat_flow, **kw)


def _wait(predicate, timeout=5.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if predicate():
            return True
        time.sleep(0.02)
    return False


# ── http ─────────────────────────────────────────────────────────────────


def test_a_doorless_graph_answers_with_its_outputs():
    with TestClient(_app(_chat())) as client:
        reply = client.post("/chat", json={"question": "hi", "k": 5})
    assert reply.status_code == 200
    assert reply.json() == {"answer": "hi?", "k": 5}
    assert reply.headers["x-operonx-trace-id"]


def test_a_parameter_left_out_takes_the_graphs_default():
    with TestClient(_app(_chat())) as client:
        assert client.post("/chat", json={"question": "hi"}).json() == {"answer": "hi?", "k": 3}


def test_the_query_and_the_body_fill_parameters_together():
    with TestClient(_app(_chat())) as client:
        reply = client.post("/chat?k=7", json={"question": "hi"})
    assert reply.json() == {"answer": "hi?", "k": "7"}  # a query value is text, as today


def test_a_bare_value_goes_to_the_one_required_parameter():
    with TestClient(_app(_chat())) as client:
        assert client.post("/chat", json="hi").json() == {"answer": "hi?", "k": 3}


def test_a_get_with_only_a_query_runs():
    service = Service("chat", http("GET", "/chat", port=8902), graph=chat_flow)
    with TestClient(_app(service)) as client:
        assert client.get("/chat?question=hi").json() == {"answer": "hi?", "k": 3}


def test_reserved_query_names_never_reach_the_graph():
    with TestClient(_app(_chat())) as client:
        reply = client.post("/chat?trace_id=abc", json={"question": "hi"})
    assert reply.status_code == 200
    assert reply.headers["x-operonx-trace-id"] == "abc"


@pytest.mark.parametrize(
    "url, body, field",
    [
        ("/chat", {"question": "hi", "topic": "x"}, "topic"),  # not a parameter
        ("/chat", {"k": 1}, "question"),  # required, given by nobody
        ("/chat?question=a", {"question": "b"}, "question"),  # given twice
    ],
)
def test_a_request_that_does_not_fit_is_a_400_and_mints_no_run(url, body, field):
    with TestClient(_app(_chat())) as client:
        reply = client.post(url, json=body)
    assert reply.status_code == 400
    assert reply.json()["field"] == field and reply.json()["endpoint"] == "chat"
    assert "input=" not in reply.json()["error"]  # a job's advice, not a caller's
    assert "x-operonx-trace-id" not in reply.headers  # no run, no trace
    assert SEEN == []


@pytest.mark.parametrize("url", ["/chat?topic=x", "/chat?question="])
def test_the_query_ignores_what_the_graph_does_not_take_and_empty_values(url):
    """A query key the graph does not take is ignored, and ``""`` is absent:
    a caller adding a key must not break the door (callbot plan O1a). The
    body keeps its rules: an unknown field is still refused (above)."""
    with TestClient(_app(_chat())) as client:
        reply = client.post(url, json={"question": "hi"})
    assert reply.status_code == 200
    assert SEEN == [("hi", 3)]


def test_a_failing_run_is_a_500_with_its_trace_id_and_no_detail():
    service = Service("bad", http("POST", "/bad", port=8903), graph=failing_flow)
    with TestClient(_app(service)) as client:
        reply = client.post("/bad", json={"question": "hi"})
    assert reply.status_code == 500
    assert reply.json()["trace_id"] == reply.headers["x-operonx-trace-id"]
    assert "sk-live-1" not in reply.text
    assert SEEN == ["hi"]


def test_a_run_that_refuses_with_an_http_status_answers_it():
    """An op raising an exception with a 4xx ``http_status`` is the
    project refusing the caller: that status and the exception's text, not
    the 500 of a run that failed — and the run is still recorded."""
    service = Service("g", http("POST", "/g", port=8904), graph=guarded_flow)
    with TestClient(_app(service)) as client:
        refused = client.post("/g", json={"question": "secret"})
        streamed = client.post(
            "/g", json={"question": "secret"}, headers={"accept": "text/event-stream"}
        )
        bug = client.post("/g", json={"question": "odd"})
        fine = client.post("/g", json={"question": "hi"})
    assert refused.status_code == 403 and streamed.status_code == 403
    assert refused.json()["error"] == "only the owner reads this"
    assert refused.json()["trace_id"] == refused.headers["x-operonx-trace-id"]
    assert "Traceback" not in refused.text
    assert bug.status_code == 500 and "a bug" not in bug.text  # no status: still a 500, no detail
    assert fine.status_code == 200 and fine.json() == {"answer": "hi"}


def test_a_stream_reader_gets_exactly_one_event_the_outputs():
    with TestClient(_app(_chat())) as client:
        reply = client.post(
            "/chat", json={"question": "hi"}, headers={"accept": "text/event-stream"}
        )
    assert reply.status_code == 200
    events = [line for line in reply.text.splitlines() if line.startswith("data: ")]
    assert [json.loads(e[6:]) for e in events] == [{"answer": "hi?", "k": 3}]


def test_a_stream_reader_of_a_request_that_does_not_fit_gets_a_400():
    with TestClient(_app(_chat())) as client:
        reply = client.post("/chat", json={"nope": 1}, headers={"accept": "text/event-stream"})
    assert reply.status_code == 400 and reply.json()["field"] == "nope"


def test_on_session_and_the_body_together_fill_the_parameters():
    def hook(session):
        return RunRequest(inputs={"k": 9})

    with TestClient(_app(_chat(on_session=hook))) as client:
        assert client.post("/chat", json={"question": "hi"}).json() == {"answer": "hi?", "k": 9}
        assert client.post("/chat", json={"question": "hi", "k": 1}).status_code == 400


def test_on_close_runs_after_a_refusal():
    closed = []
    service = _chat(on_close=lambda session, handle: closed.append(handle))
    with TestClient(_app(service)) as client:
        assert client.post("/chat", json={"nope": 1}).status_code == 400
    assert closed == [None]


def test_a_graph_with_doors_is_served_as_before():
    service = Service("door", http("POST", "/door", port=8904), graph=door_flow)
    with TestClient(_app(service)) as client:
        assert client.post("/door", json={"a": 1}).json() == {"a": 1}


def test_a_doorless_graph_on_a_stream_listener_warns_and_runs_as_before(caplog):
    service = Service("ws", websocket("/ws", port=8905), graph=sweep, max_inflight=4)
    with TestClient(_app(service)) as client:
        with client.websocket_connect("/ws"):
            pass
    assert _wait(lambda: SEEN == ["tick"])
    assert "has no ingress op" in caplog.text


def test_describe_says_which_shape_a_service_has():
    from operonx.app.declare import describe_service

    assert describe_service(_chat())["doors"] is False
    door = Service("door", http("POST", "/door", port=8906), graph=door_flow)
    assert describe_service(door)["doors"] is True


@op
def read_mail(payload: dict) -> dict:
    SEEN.append(payload)
    return {"id": payload["ID"]}


@graph
def on_mail(payload):
    r = read_mail(payload=payload)
    START >> r >> END


def test_input_hands_the_whole_body_to_one_parameter():
    service = Service("mail", http("POST", "/mail", port=8911), graph=on_mail, input="payload")
    with TestClient(_app(service)) as client:
        body = {"ID": "m1", "From": "a@b.c", "Subject": "hi"}  # fields the graph does not declare
        assert client.post("/mail", json=body).json() == {"id": "m1"}
        assert client.post("/mail?payload=x", json=body).json()["field"] == "payload"
    assert SEEN == [body]


@op
def edit_ticket(tid: str, patch: dict) -> dict:
    SEEN.append((tid, patch))
    return {"tid": tid, "keys": sorted(patch)}


@graph
def ticket_edit(tid, patch):
    e = edit_ticket(tid=tid, patch=patch)
    START >> e >> END


def test_input_keeps_the_paths_parameters():
    """``input=`` hands the body over whole; the path's parameters still fill
    their own (they used to ride in the body, and the graph's ``tid`` was missing)."""
    service = Service("edit", http("POST", "/tickets/{tid}", port=8913), graph=ticket_edit, input="patch")
    with TestClient(_app(service)) as client:
        ok = client.post("/tickets/t1", json={"title": "x", "tid": "the body's own"})
        both = client.post("/tickets/t1?tid=t2", json={})
    assert ok.status_code == 200 and ok.json() == {"tid": "t1", "keys": ["tid", "title"]}
    assert both.status_code == 400 and both.json()["field"] == "tid"
    assert SEEN == [("t1", {"title": "x", "tid": "the body's own"})]


def test_a_refusal_is_one_log_line_not_a_traceback(caplog):
    """A 4xx ``http_status`` is the caller's fault: one log line, no traceback;
    an exception without one is still an error, with its traceback."""
    service = Service("g", http("POST", "/g", port=8914), graph=guarded_flow)
    from operonx.core.loggings import LOGGER

    with caplog.at_level("INFO", logger=LOGGER.name), TestClient(_app(service)) as client:
        client.post("/g", json={"question": "secret"})
        refused = caplog.text
        caplog.clear()
        client.post("/g", json={"question": "odd"})
        bug = caplog.text
    assert "refused the request (403): only the owner reads this" in refused and "Traceback" not in refused
    assert "Error in op" in bug and "Traceback" in bug


@op
def download(kind: str) -> dict:
    if kind == "file":
        return {"$file": __file__, "$type": "text/x-python", "$name": "tests é.py"}
    if kind == "bytes":
        return {"$body": b"\x00\x01", "$name": "a.bin"}
    if kind == "text":
        return {"$body": "--- a\n+++ b\n", "$type": "text/plain"}
    if kind == "moved":
        return {"$status": 303, "$headers": {"location": "/elsewhere"}}
    return {"$file": "/no/such/file"}


@graph
def downloads(kind):
    d = download(kind=kind)
    START >> d >> END


def test_a_reply_can_be_a_file_bytes_text_or_a_redirect():
    service = Service("dl", http("GET", "/dl/{kind}", port=8915), graph=downloads)
    with TestClient(_app(service)) as client:
        file = client.get("/dl/file")
        data = client.get("/dl/bytes")
        text = client.get("/dl/text")
        moved = client.get("/dl/moved", follow_redirects=False)
        gone = client.get("/dl/gone")
    with open(__file__, "rb") as f:
        assert file.status_code == 200 and file.content == f.read()
    assert file.headers["content-type"].startswith("text/x-python")
    assert "attachment" in file.headers["content-disposition"]
    assert data.content == b"\x00\x01" and data.headers["content-type"] == "application/octet-stream"
    assert data.headers["content-disposition"] == "attachment; filename*=utf-8''a.bin"
    assert text.text == "--- a\n+++ b\n" and text.headers["content-type"].startswith("text/plain")
    assert moved.status_code == 303 and moved.headers["location"] == "/elsewhere"
    assert "x-operonx-trace-id" in moved.headers
    assert gone.status_code == 500 and "is not a file" in gone.json()["error"]


@op
def save_upload(name: str, data: bytes) -> dict:
    SEEN.append((name, data))
    return {"name": name, "bytes": len(data)}


@graph
def upload(name, data):
    s = save_upload(name=name, data=data)
    START >> s >> END


def test_a_bytes_door_hands_the_body_over_as_it_came():
    service = Service(
        "up", http("POST", "/up", port=8916, codec="bytes"), graph=upload, input="data"
    )
    with TestClient(_app(service)) as client:
        ok = client.post("/up?name=a.png", content=b"\x89PNG\xff")
    assert ok.status_code == 200 and ok.json() == {"name": "a.png", "bytes": 5}
    assert SEEN == [("a.png", b"\x89PNG\xff")]


def test_input_on_a_webhook_skips_the_field_check_before_the_202():
    service = Service("mail", webhook("/mail", port=8912), graph=on_mail, input="payload")
    with TestClient(_app(service)) as client:
        assert client.post("/mail", json={"ID": "m2", "Extra": 1}).status_code == 202
        assert _wait(lambda: SEEN == [{"ID": "m2", "Extra": 1}])


# ── webhook ──────────────────────────────────────────────────────────────


def test_a_webhook_refuses_a_body_that_does_not_fit_before_its_202():
    service = Service("hook", webhook("/hook", port=8907), graph=chat_flow)
    with TestClient(_app(service)) as client:
        bad = client.post("/hook", json={"nope": 1})
        missing = client.post("/hook", json={"k": 1})
        good = client.post("/hook", json={"question": "hi"})
        assert bad.status_code == 400 and bad.json()["field"] == "nope"
        assert missing.status_code == 400 and missing.json()["field"] == "question"
        assert good.status_code == 202
        assert _wait(lambda: SEEN == [("hi", 3)])


def test_a_doorless_webhooks_outputs_reach_its_callback(tmp_path):
    from tests.internal.app._receiver import Receiver

    receiver = Receiver()
    try:
        service = Service(
            "hook",
            webhook("/hook", port=8908),
            graph=chat_flow,
            queue=str(tmp_path / "q.db"),
            callback_hosts=["127.0.0.1"],
            poll=0.05,
        )
        with TestClient(_app(service)) as client:
            reply = client.post(f"/hook?callback={receiver.url}", json={"question": "hi"})
            assert reply.status_code == 202
            assert _wait(lambda: receiver.got, timeout=10)
        got = receiver.got[0]
        assert got["status"] == "done"
        assert got["output"] == {"answer": "hi?", "k": 3}
    finally:
        receiver.close()


# ── schedule ─────────────────────────────────────────────────────────────


def test_a_doorless_graph_with_no_parameters_runs_on_each_tick():
    service = Service("sweep", schedule(every=0.05, port=8909), graph=sweep)
    with TestClient(_app(service)):
        assert _wait(lambda: len(SEEN) >= 3)
    assert SEEN[:3] == ["tick"] * 3


def test_a_graph_with_a_tick_parameter_gets_the_tick():
    service = Service("sweep", schedule(every=0.05, port=8910), graph=sweep_with_tick)
    with TestClient(_app(service)):
        assert _wait(lambda: len(SEEN) >= 2)
    assert SEEN[:2] == [1, 2]


# ── the shared rules ─────────────────────────────────────────────────────


@op(door="ingress")
async def receive_audio():
    """A project's own door op, as the callbot's: reads its session."""
    async for item in current_session().recv():
        yield {"item": item}


@op
def keep(item=None) -> dict:
    SEEN.append(item)
    return {"kept": item}


@graph
def own_door_flow(item=None):
    r = receive_audio()
    k = keep(item=r["item"])
    START >> r >> k >> END


def test_has_doors_counts_a_projects_own_ingress_op():
    assert has_doors(door_flow) and has_doors(own_door_flow)
    assert not has_doors(chat_flow)
    assert not has_doors(Operon(sweep))


def test_a_job_feeds_its_item_through_a_projects_own_door(tmp_path):
    """Job.has_doors looked only for the library ingress; this graph's own
    door op got the item bound to its `item` parameter instead."""
    run = Job("j", graph=own_door_flow, items=[{"a": 1}], record_dir=tmp_path).run_sync()
    assert run.status == "ok"
    assert SEEN == [{"a": 1}]


def test_serve_inputs_rules():
    params = ["question", "k"]
    assert serve_inputs(params, {}, {"question": "q"}, defaults={"k": 3}) == {
        "question": "q",
        "k": 3,
    }
    assert serve_inputs(params, {"trace_id": "x", "k": 1}, "q", reserved=("trace_id",)) == {
        "question": "q",
        "k": 1,
    }
    assert serve_inputs([], {}, {"tick": 1, "at": "now"}, tick=True) == {}
    with pytest.raises(BindError, match="both"):
        serve_inputs(params, {"k": 1}, {"k": 2, "question": "q"})
    with pytest.raises(BindError, match="missing"):
        serve_inputs(params, {}, None)


def test_bind_item_keeps_the_job_rules():
    assert bind_item(["a"], 5) == {"a": 5}
    assert bind_item(["a", "b"], 5, fixed={"b": 1}) == {"b": 1, "a": 5}
    assert bind_item(["a", "b"], {"x": 1}, input="a") == {"a": {"x": 1}}
    with pytest.raises(BindError):
        bind_item(["a", "b"], 5)
    with pytest.raises(BindError):
        bind_item(["a"], {"z": 1})
