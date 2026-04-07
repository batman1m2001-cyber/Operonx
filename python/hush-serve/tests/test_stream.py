"""Tests for SSE stream handler: POST /path/stream -> text/event-stream."""

import json

import pytest
from fastapi.testclient import TestClient
from hush.core import END, PARENT, START, GraphOp, op

from hush.serve import HushApp


class TestStreamHandler:
    def test_stream_returns_sse(self, double_graph):
        """Non-streaming graph: stream endpoint enabled, returns result as SSE."""
        app = HushApp()
        app.endpoint("/double", graph=double_graph, stream=True)
        client = TestClient(app.fastapi)
        resp = client.post("/double/stream", json={"x": 5})
        assert resp.status_code == 200
        assert "text/event-stream" in resp.headers.get("content-type", "")

    def test_stream_contains_done_event(self, double_graph):
        app = HushApp()
        app.endpoint("/double", graph=double_graph, stream=True)
        client = TestClient(app.fastapi)
        resp = client.post("/double/stream", json={"x": 5})
        body = resp.text
        assert "event: done" in body

    def test_stream_contains_result(self, double_graph):
        app = HushApp()
        app.endpoint("/double", graph=double_graph, stream=True)
        client = TestClient(app.fastapi)
        resp = client.post("/double/stream", json={"x": 5})
        body = resp.text
        # Parse the SSE event data from the done event
        for line in body.split("\n"):
            if line.startswith("data:") and "result" in line:
                data = json.loads(line[len("data:") :].strip())
                assert data["result"] == 10
                break

    def test_no_stream_endpoint_when_disabled(self, double_graph):
        app = HushApp()
        app.endpoint("/double", graph=double_graph, stream=False)
        client = TestClient(app.fastapi)
        resp = client.post("/double/stream", json={"x": 5})
        assert resp.status_code in (404, 405)


class TestStreamWithGenerator:
    """Test SSE streaming with generator ops — verifies token + done events."""

    def _make_gen_graph(self):
        @op
        def source(items: list):
            for item in items:
                yield {"value": item}

        @op
        def double_val(x: int):
            return {"result": x * 2}

        with GraphOp(name="gen_stream") as g:
            s = source(items=PARENT["items"])
            d = double_val(x=s["value"])
            START >> s >> d >> END
        return g

    def test_generator_stream_has_token_events(self):
        """Generator graph emits SSE token events before done."""
        graph = self._make_gen_graph()
        app = HushApp()
        app.endpoint("/gen", graph=graph, stream=True)
        client = TestClient(app.fastapi)
        resp = client.post("/gen/stream", json={"items": [1, 2, 3]})
        body = resp.text

        assert resp.status_code == 200
        assert "event: token" in body
        assert "event: done" in body

    def test_generator_stream_token_count(self):
        """Generator yielding 3 items produces 3 token events."""
        graph = self._make_gen_graph()
        app = HushApp()
        app.endpoint("/gen", graph=graph, stream=True)
        client = TestClient(app.fastapi)
        resp = client.post("/gen/stream", json={"items": [1, 2, 3]})
        body = resp.text

        token_count = body.count("event: token")
        done_count = body.count("event: done")
        assert token_count == 3
        assert done_count == 1

    def test_generator_stream_token_data(self):
        """Token events contain the output data from downstream ops."""
        graph = self._make_gen_graph()
        app = HushApp()
        app.endpoint("/gen", graph=graph, stream=True)
        client = TestClient(app.fastapi)
        resp = client.post("/gen/stream", json={"items": [10, 20]})
        body = resp.text

        # Parse token event data — tokens contain downstream op outputs
        token_results = []
        lines = body.split("\n")
        for i, line in enumerate(lines):
            if line.startswith("event: token"):
                for j in range(i + 1, len(lines)):
                    if lines[j].startswith("data:"):
                        data = json.loads(lines[j][len("data:") :].strip())
                        if "result" in data:
                            token_results.append(data["result"])
                        break

        assert sorted(token_results) == [20, 40]

    def test_generator_stream_done_has_results(self):
        """Done event contains accumulated results."""
        graph = self._make_gen_graph()
        app = HushApp()
        app.endpoint("/gen", graph=graph, stream=True)
        client = TestClient(app.fastapi)
        resp = client.post("/gen/stream", json={"items": [1, 2, 3]})
        body = resp.text

        # Parse done event
        lines = body.split("\n")
        for i, line in enumerate(lines):
            if line.startswith("event: done"):
                for j in range(i + 1, len(lines)):
                    if lines[j].startswith("data:"):
                        data = json.loads(lines[j][len("data:") :].strip())
                        assert sorted(data["result"]) == [2, 4, 6]
                        return
        pytest.fail("No done event found")

    def test_empty_generator_stream(self):
        """Empty generator: no token events, only done."""
        graph = self._make_gen_graph()
        app = HushApp()
        app.endpoint("/gen", graph=graph, stream=True)
        client = TestClient(app.fastapi)
        resp = client.post("/gen/stream", json={"items": []})
        body = resp.text

        assert "event: token" not in body
        assert "event: done" in body


class TestWebSocketWithGenerator:
    """Test WebSocket streaming with generator ops."""

    def _make_gen_graph(self):
        @op
        def source(items: list):
            for item in items:
                yield {"value": item}

        @op
        def double_val(x: int):
            return {"result": x * 2}

        with GraphOp(name="ws_gen") as g:
            s = source(items=PARENT["items"])
            d = double_val(x=s["value"])
            START >> s >> d >> END
        return g

    def test_ws_generator_sends_token_and_result(self):
        """WebSocket receives token events then result."""
        graph = self._make_gen_graph()
        app = HushApp()
        app.endpoint("/gen", graph=graph, websocket=True)
        client = TestClient(app.fastapi)

        with client.websocket_connect("/gen/ws") as ws:
            ws.send_json({"inputs": {"items": [1, 2]}})

            messages = []
            while True:
                msg = ws.receive_json()
                messages.append(msg)
                if msg["type"] == "result":
                    break

            token_msgs = [m for m in messages if m["type"] == "token"]
            result_msgs = [m for m in messages if m["type"] == "result"]

            assert len(token_msgs) >= 2
            assert len(result_msgs) == 1

            # Token data contains downstream op outputs
            token_results = [m["data"]["result"] for m in token_msgs if "result" in m["data"]]
            assert sorted(token_results) == [2, 4]

            # Result has accumulated output
            assert sorted(result_msgs[0]["data"]["result"]) == [2, 4]

    def test_ws_batch_graph_result_only(self, double_graph):
        """Non-generator graph: WebSocket sends result with final data."""
        app = HushApp()
        app.endpoint("/double", graph=double_graph, websocket=True)
        client = TestClient(app.fastapi)

        with client.websocket_connect("/double/ws") as ws:
            ws.send_json({"inputs": {"x": 5}})
            # Read all messages until result
            messages = []
            while True:
                msg = ws.receive_json()
                messages.append(msg)
                if msg["type"] == "result":
                    break
            result_msg = messages[-1]
            assert result_msg["type"] == "result"
            assert result_msg["data"]["result"] == 10
