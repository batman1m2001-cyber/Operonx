"""An http door with path parameters and its own status code.

``http("GET", "/summaries/{date}")`` hands ``date`` to the run as an input,
beside the body's fields. An op that outputs ``"$status": 404`` (or
``201``) sets the reply's status code; the key is not in the body. One
that outputs ``"$body"`` answers that value alone (a JSON array). Both
keep a route that needs them a ``Service`` (lint C14), traced like any run,
instead of a hand-written Starlette route.
"""

from __future__ import annotations

import pytest
from starlette.testclient import TestClient

from operonx import END, START, graph, op
from operonx.app import Application, Service, http

pytestmark = pytest.mark.unit

STORE = {"2026-01-01": "a quiet day"}


@op
def find_summary(date: str) -> dict:
    if date in STORE:
        return {"date": date, "summary": STORE[date]}
    return {"error": f"no summary for {date}", "$status": 404}


@graph
def summary_flow(date):
    found = find_summary(date=date)
    START >> found >> END


@op
def create_ticket(text: str) -> dict:
    return {"ticket": {"text": text}, "$status": 201}


@graph
def create_flow(text):
    made = create_ticket(text=text)
    START >> made >> END


@op
def list_all() -> dict:
    return {"tickets": []}


@graph
def list_flow():
    tickets = list_all()
    START >> tickets >> END


@op
def list_texts() -> dict:
    return {"$body": [{"text": "printer"}, {"text": "login"}]}


@graph
def texts_flow():
    texts = list_texts()
    START >> texts >> END


@op
def nothing_here(id: str) -> dict:
    return {"$body": None, "$status": 404}


@graph
def nothing_flow(id):
    found = nothing_here(id=id)
    START >> found >> END


@op
def bad_status(text: str) -> dict:
    return {"$status": "teapot"}


@graph
def bad_flow(text):
    bad = bad_status(text=text)
    START >> bad >> END


def _client():
    port = 8962
    return TestClient(
        Application(
            "t",
            services=[
                Service("summary", http("GET", "/summaries/{date}", port=port), graph=summary_flow),
                Service("create", http("POST", "/tickets", port=port), graph=create_flow),
                Service("list", http("GET", "/tickets", port=port), graph=list_flow),
                Service("bad", http("POST", "/bad", port=port), graph=bad_flow),
                Service("texts", http("GET", "/texts", port=port), graph=texts_flow),
                Service("nothing", http("GET", "/nothing/{id}", port=port), graph=nothing_flow),
            ],
        ).asgi()
    )


def test_a_path_parameter_is_an_input_of_the_run():
    with _client() as client:
        resp = client.get("/summaries/2026-01-01")
    assert resp.status_code == 200
    assert resp.json() == {"date": "2026-01-01", "summary": "a quiet day"}


def test_an_op_sets_the_status_code_and_the_key_is_not_in_the_body():
    with _client() as client:
        missing = client.get("/summaries/1999-01-01")
        created = client.post("/tickets", json={"text": "printer"})
    assert missing.status_code == 404
    assert missing.json() == {"error": "no summary for 1999-01-01"}
    assert created.status_code == 201
    assert created.json() == {"ticket": {"text": "printer"}}


def test_a_get_with_no_body_and_no_parameters_still_runs():
    with _client() as client:
        resp = client.get("/tickets")
    assert resp.status_code == 200 and resp.json() == {"tickets": []}


def test_an_op_answers_a_bare_array_with_body():
    with _client() as client:
        listed = client.get("/texts")
        missing = client.get("/nothing/7")
    assert listed.status_code == 200
    assert listed.json() == [{"text": "printer"}, {"text": "login"}]
    assert missing.status_code == 404 and missing.json() is None


def test_a_status_that_is_not_a_code_is_a_500_not_a_reply():
    with _client() as client:
        resp = client.post("/bad", json={"text": "x"})
    assert resp.status_code == 500
    assert "$status" in resp.json()["error"]


def test_a_name_in_both_the_path_and_the_body_is_refused():
    with _client() as client:
        resp = client.request("GET", "/summaries/2026-01-01", json={"date": "2026-01-02"})
    assert resp.status_code == 400 and resp.json()["field"] == "date"
