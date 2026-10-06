"""``agent_service`` end to end: HTTP (JSON and server-sent events) and a
websocket, each with an approval round trip through the wire.

A refund over 500 parks for a human. The client reads the interruption,
answers it on the door's resume route (or the same socket), and the run
finishes: the refund runs once, after the approval, and never before.
"""

from __future__ import annotations

import json

import pytest
from operonx.app import http, websocket
from operonx.app.serve.app import TRACE_HEADER, build_app
from operonx.telemetry.consumers.langfuse import build_tree
from starlette.testclient import TestClient

from operonx_agents import Agent, InMemorySession, InMemoryStateStore, Model, agent_service, tool
from operonx_agents.serve import decisions_of
from tests.agents import RAN, asks, says
from tests.fakes import ScriptedLLM


@pytest.fixture(autouse=True)
def _ran():
    RAN.clear()
    yield


@tool(idempotent=False, approval=lambda ctx, args: args["amount"] > 500)
async def refund(order_id: str, amount: int) -> str:
    """Refund an order (over 500 needs a human)."""
    RAN.append(f"refund:{order_id}:{amount}")
    return f"refunded {amount} on {order_id}"


def cashier(hub, *script, **kw):
    llm = ScriptedLLM(*script)
    hub(m=llm)
    return Agent(name="cashier", model=Model("m"), tools=[refund], **kw), llm


REFUND_900 = (asks(("refund", {"order_id": "A1", "amount": 900})), says("Refunded 900 on A1."))
SSE = {"accept": "text/event-stream"}


def frames(text: str) -> list:
    out = []
    for block in text.split("\n\n"):
        data = [line[len("data: ") :] for line in block.splitlines() if line.startswith("data: ")]
        if data:
            out.append(json.loads("\n".join(data)))
    return out


def served(*specs):
    return TestClient(build_app(specs))


class TestHttpJson:
    def test_an_approval_round_trip(self, hub):
        agent, llm = cashier(hub, *REFUND_900)
        spec = agent_service(agent, http("POST", "/cashier"), store=InMemoryStateStore())
        with served(spec) as client:
            first = client.post("/cashier", json={"input": "refund 900 on A1"})
            parked = first.json()
            assert first.status_code == 200 and first.headers[TRACE_HEADER]
            assert parked["status"] == "interrupted" and RAN == []
            assert "messages" not in parked and "new_items" not in parked
            (asked,) = parked["interruptions"]
            assert asked["tool"] == "refund" and asked["args"] == {"order_id": "A1", "amount": 900}
            done = client.post(
                "/cashier/resume",
                json={"run_id": parked["run_id"], "approvals": {asked["id"]: "approve"}},
            ).json()
        assert done["status"] == "completed" and done["output"] == "Refunded 900 on A1."
        assert done["run_id"] == parked["run_id"] and RAN == ["refund:A1:900"]
        assert llm.calls == 2  # the parked turn is not asked again

    def test_deny_tells_the_model_why(self, hub):
        agent, llm = cashier(hub, *REFUND_900)
        spec = agent_service(agent, http("POST", "/c"), store=InMemoryStateStore())
        with served(spec) as client:
            parked = client.post("/c", json={"input": "refund"}).json()
            (asked,) = parked["interruptions"]
            done = client.post(
                "/c/resume",
                json={"run_id": parked["run_id"], "approvals": {asked["id"]: {"deny": "fraud"}}},
            ).json()
        assert done["status"] == "completed" and RAN == []
        (said,) = [m for m in llm.requests[-1]["messages"] if m["role"] == "tool"]
        assert "fraud" in said["content"]

    def test_a_bare_string_is_the_input(self, hub):
        agent, _ = cashier(hub, says("Hello."))
        spec = agent_service(agent, http("POST", "/c"), store=InMemoryStateStore())
        with served(spec) as client:
            assert client.post("/c", json="hi").json()["output"] == "Hello."

    @pytest.mark.parametrize(
        "body, says_",
        [
            ({"question": "x"}, 'no "input"'),
            ([1, 2], "got list"),
            ({"run_id": "nope", "approvals": {"x": "approve"}}, "nope"),
            ({"run_id": "r", "approvals": {"x": "yes"}}, 'expected "approve"'),
            ({"run_id": "r"}, 'a resume needs "approvals"'),
            ({"input": "hi", "session_id": "s1"}, "no sessions"),
        ],
    )
    def test_a_body_it_cannot_read_is_invalid_and_runs_nothing(self, hub, body, says_):
        agent, llm = cashier(hub, says("never"))
        spec = agent_service(agent, http("POST", "/c"), store=InMemoryStateStore())
        with served(spec) as client:
            got = client.post("/c", json=body).json()
        assert got["status"] == "invalid" and says_ in got["error"], got
        assert llm.calls == 0

    def test_approvals_for_ids_the_run_does_not_wait_on_are_refused(self, hub):
        agent, _ = cashier(hub, *REFUND_900)
        spec = agent_service(agent, http("POST", "/c"), store=InMemoryStateStore())
        with served(spec) as client:
            parked = client.post("/c", json={"input": "refund"}).json()
            got = client.post(
                "/c/resume", json={"run_id": parked["run_id"], "approvals": {"bogus": "approve"}}
            ).json()
        assert got["status"] == "invalid" and "bogus" in got["error"] and RAN == []

    def test_a_session_id_continues_the_conversation(self, hub):
        agent, llm = cashier(hub, says("Noted: blue."), says("Blue."))
        kept = {}
        spec = agent_service(
            agent,
            http("POST", "/c"),
            store=InMemoryStateStore(),
            sessions=lambda sid: kept.setdefault(sid, InMemorySession()),
        )
        with served(spec) as client:
            client.post("/c", json={"input": "my colour is blue", "session_id": "u1"})
            assert (
                client.post("/c", json={"input": "colour?", "session_id": "u1"}).json()["output"]
                == "Blue."
            )
        seen = [(m["role"], m["content"]) for m in llm.requests[1]["messages"]]
        assert ("user", "my colour is blue") in seen and ("assistant", "Noted: blue.") in seen


class TestHttpEventStream:
    def test_every_event_then_the_result_and_a_streamed_resume(self, hub):
        agent, _ = cashier(hub, *REFUND_900)
        spec = agent_service(agent, http("POST", "/c"), store=InMemoryStateStore())
        with served(spec) as client:
            first = client.post("/c", json={"input": "refund 900"}, headers=SSE)
            assert first.headers["content-type"].startswith("text/event-stream")
            events = frames(first.text)
            kinds = [e["type"] for e in events]
            assert kinds == ["RunStarted", "TurnStarted", "ApprovalRequired", "RunFinished"]
            asked = events[2]
            parked = events[-1]["result"]
            assert (
                parked["status"] == "interrupted"
                and parked["interruptions"][0]["id"] == asked["id"]
            )
            again = client.post(
                "/c/resume",
                json={"run_id": parked["run_id"], "approvals": {asked["id"]: "approve"}},
                headers=SSE,
            )
        events = frames(again.text)
        kinds = [e["type"] for e in events]
        assert kinds[0] == "RunStarted" and events[0]["resumed"] is True
        assert "ToolCallFinished" in kinds and kinds[-1] == "RunFinished"
        text = "".join(e["text"] for e in events if e["type"] == "TextDelta")
        assert text == "Refunded 900 on A1."
        assert events[-1]["result"]["status"] == "completed" and RAN == ["refund:A1:900"]


class TestWebSocket:
    def test_an_approval_round_trip_on_one_connection(self, hub):
        agent, _ = cashier(hub, *REFUND_900)
        spec = agent_service(agent, websocket("/ws"), store=InMemoryStateStore(), max_inflight=8)
        with served(spec) as client, client.websocket_connect("/ws") as ws:
            ws.send_json({"input": "refund 900 on A1"})
            first = _until_finished(ws)
            asked = next(e for e in first if e["type"] == "ApprovalRequired")
            parked = first[-1]["result"]
            assert parked["status"] == "interrupted" and RAN == []
            ws.send_json({"run_id": parked["run_id"], "approvals": {asked["id"]: "approve"}})
            done = _until_finished(ws)
        assert done[0]["type"] == "RunStarted" and done[0]["resumed"] is True
        assert done[-1]["result"]["status"] == "completed" and RAN == ["refund:A1:900"]

    def test_requests_on_one_connection_run_in_turn(self, hub):
        agent, _ = cashier(hub, says("one"), says("two"))
        spec = agent_service(agent, websocket("/ws"), store=InMemoryStateStore(), max_inflight=8)
        with served(spec) as client, client.websocket_connect("/ws") as ws:
            ws.send_json({"input": "first"})
            ws.send_json({"input": "second"})
            a, b = _until_finished(ws), _until_finished(ws)
        # each run's events arrive together, start to finish, in order
        for run in (a, b):
            assert run[0]["type"] == "RunStarted" and run[-1]["type"] == "RunFinished"
            assert sum(e["type"] == "RunStarted" for e in run) == 1
        assert [a[-1]["result"]["output"], b[-1]["result"]["output"]] == ["one", "two"]


def _until_finished(ws) -> list:
    got = []
    while True:
        frame = ws.receive_json()
        got.append(frame)
        if frame.get("type") == "RunFinished":
            return got


class TestTheServiceRun:
    def test_the_trace_is_agent_turn_model_and_tool(self, hub):
        agent, _ = cashier(hub, asks(("refund", {"order_id": "A1", "amount": 5})), says("Done."))
        handles = []
        spec = agent_service(
            agent,
            http("POST", "/c"),
            store=InMemoryStateStore(),
            on_close=lambda session, handle: handles.append(handle),
        )
        with served(spec) as client:
            assert client.post("/c", json={"input": "refund 5"}).json()["status"] == "completed"
        (handle,) = handles
        tree = build_tree(handle.trace)
        rows = {
            n["id"]: (n["name"], n["record"].op_type if n["record"] else None)
            for n in tree.values()
        }
        root = next(
            i for i, n in tree.items() if n["record"] is not None and n["record"].op_type == "agent"
        )
        assert rows[root][0] == "cashier"

        def kids(node):
            return [i for i, n in tree.items() if n["parent"] == node]

        # the reply the op sent hangs under it too: what it dispatched
        assert [rows[k] for k in kids(root)] == [
            ("turn", "turn"),
            ("turn", "turn"),
            ("out", "code"),
        ]
        turns = kids(root)[:2]
        assert [rows[k] for k in kids(turns[0])] == [("model", "llm"), ("refund", "tool")]
        assert [rows[k] for k in kids(turns[1])] == [("model", "llm")]

    def test_it_is_a_service_with_a_resume_route_named_after_the_agent(self, hub):
        agent, _ = cashier(hub, says("x"))
        spec = agent_service(agent, http("POST", "/c"), store=InMemoryStateStore())
        assert spec.name == "cashier" and spec.resume is spec.graph
        assert spec.graph.__name__ == "cashier_service"
        assert spec.resume_spec().path == "/c/resume"
        ws = agent_service(
            agent, websocket("/ws"), store=InMemoryStateStore(), max_inflight=4, name="cashier_ws"
        )
        assert ws.name == "cashier_ws" and ws.resume is None

    def test_a_store_is_required(self, hub):
        agent, _ = cashier(hub, says("x"))
        with pytest.raises(ValueError, match="needs store="):
            agent_service(agent, http("POST", "/c"), store=None)


def test_decisions_are_spelled_out():
    from operonx_agents import Approve, Deny

    assert decisions_of({"a": "approve", "b": "deny", "c": {"deny": "no"}}) == {
        "a": Approve(),
        "b": Deny(),
        "c": Deny("no"),
    }
