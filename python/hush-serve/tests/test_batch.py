"""Tests for batch handler: POST /path/batch -> [results]."""

from fastapi.testclient import TestClient

from hush.serve import HushApp


class TestBatchHandler:
    def test_batch_double(self, double_graph):
        app = HushApp()
        app.endpoint("/double", graph=double_graph)
        client = TestClient(app.fastapi)
        resp = client.post("/double/batch", json={"items": [{"x": 1}, {"x": 2}, {"x": 3}]})
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 3
        results = [r["result"] for r in data["results"]]
        assert sorted(results) == [2, 4, 6]

    def test_batch_single_item(self, double_graph):
        app = HushApp()
        app.endpoint("/double", graph=double_graph)
        client = TestClient(app.fastapi)
        resp = client.post("/double/batch", json={"items": [{"x": 10}]})
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 1
        assert data["results"][0]["result"] == 20

    def test_batch_empty(self, double_graph):
        app = HushApp()
        app.endpoint("/double", graph=double_graph)
        client = TestClient(app.fastapi)
        resp = client.post("/double/batch", json={"items": []})
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 0
        assert data["results"] == []

    def test_batch_disabled(self, double_graph):
        app = HushApp()
        app.endpoint("/double", graph=double_graph, batch=False)
        client = TestClient(app.fastapi)
        resp = client.post("/double/batch", json={"items": [{"x": 1}]})
        # Should return 404/405 since batch route is not registered
        assert resp.status_code in (404, 405)

    def test_batch_with_failures(self, failing_graph):
        """Hush engine catches op errors internally and returns empty results."""
        app = HushApp()
        app.endpoint("/fail", graph=failing_graph)
        client = TestClient(app.fastapi, raise_server_exceptions=False)
        resp = client.post("/fail/batch", json={"items": [{"x": 1}, {"x": 2}]})
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 2

    def test_batch_add_graph(self, add_graph):
        app = HushApp()
        app.endpoint("/add", graph=add_graph)
        client = TestClient(app.fastapi)
        items = [{"a": 1, "b": 2}, {"a": 10, "b": 20}, {"a": 100, "b": 200}]
        resp = client.post("/add/batch", json={"items": items})
        assert resp.status_code == 200
        data = resp.json()
        results = [r["result"] for r in data["results"]]
        assert sorted(results) == [3, 30, 300]
