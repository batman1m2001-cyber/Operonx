"""Tests for sync request handler: POST /path -> JSON result."""

from fastapi.testclient import TestClient

from hush.serve import HushApp


class TestSyncHandler:
    def test_simple_double(self, double_graph):
        app = HushApp()
        app.endpoint("/double", graph=double_graph)
        client = TestClient(app.fastapi)
        resp = client.post("/double", json={"x": 5})
        assert resp.status_code == 200
        data = resp.json()
        assert data["result"] == 10

    def test_multi_input(self, add_graph):
        app = HushApp()
        app.endpoint("/add", graph=add_graph)
        client = TestClient(app.fastapi)
        resp = client.post("/add", json={"a": 3, "b": 7})
        assert resp.status_code == 200
        data = resp.json()
        assert data["result"] == 10

    def test_string_input(self, greet_graph):
        app = HushApp()
        app.endpoint("/greet", graph=greet_graph)
        client = TestClient(app.fastapi)
        resp = client.post("/greet", json={"name": "World"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["greeting"] == "Hello, World!"

    def test_chain_graph(self, chain_graph):
        app = HushApp()
        app.endpoint("/chain", graph=chain_graph)
        client = TestClient(app.fastapi)
        resp = client.post("/chain", json={"x": 5, "y": 3})
        assert resp.status_code == 200
        data = resp.json()
        # double(5) = 10, add(10, 3) = 13
        assert data["result"] == 13

    def test_internal_keys_filtered(self, double_graph):
        """Verify $-prefixed internal keys are not in the response."""
        app = HushApp()
        app.endpoint("/double", graph=double_graph)
        client = TestClient(app.fastapi)
        resp = client.post("/double", json={"x": 5})
        data = resp.json()
        for key in data:
            assert not key.startswith("$")

    def test_request_id_header(self, double_graph):
        app = HushApp()
        app.endpoint("/double", graph=double_graph)
        client = TestClient(app.fastapi)
        resp = client.post("/double", json={"x": 1})
        assert "x-request-id" in resp.headers

    def test_timing_header(self, double_graph):
        app = HushApp()
        app.endpoint("/double", graph=double_graph)
        client = TestClient(app.fastapi)
        resp = client.post("/double", json={"x": 1})
        assert "x-response-time" in resp.headers

    def test_multiple_endpoints(self, double_graph, greet_graph):
        app = HushApp()
        app.endpoint("/double", graph=double_graph)
        app.endpoint("/greet", graph=greet_graph)
        client = TestClient(app.fastapi)

        r1 = client.post("/double", json={"x": 4})
        assert r1.json()["result"] == 8

        r2 = client.post("/greet", json={"name": "Hush"})
        assert r2.json()["greeting"] == "Hello, Hush!"

    def test_failing_graph(self, failing_graph):
        """Hush engine catches op errors internally and returns 200 with empty result."""
        app = HushApp()
        app.endpoint("/fail", graph=failing_graph)
        client = TestClient(app.fastapi, raise_server_exceptions=False)
        resp = client.post("/fail", json={"x": 42})
        # Engine catches the error internally — returns 200 with empty outputs
        assert resp.status_code == 200
