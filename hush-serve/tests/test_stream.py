"""Tests for SSE stream handler: POST /path/stream -> text/event-stream."""

from fastapi.testclient import TestClient

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
        import json

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
