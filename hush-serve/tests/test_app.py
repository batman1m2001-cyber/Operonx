"""Tests for HushApp class."""

import pytest
from fastapi.testclient import TestClient
from hush.core import END, START, graph, op

from hush.serve import HushApp


class TestHushAppConstruction:
    def test_default_config(self):
        app = HushApp()
        assert app._config.title == "Hush API"
        assert app._config.mode == "python"

    def test_custom_config(self):
        app = HushApp(title="My API", mode="python", version="1.0.0")
        assert app._config.title == "My API"
        assert app._config.version == "1.0.0"

    def test_no_endpoints_initially(self):
        app = HushApp()
        assert len(app._endpoints) == 0


class TestEndpointRegistration:
    def test_method_style(self, double_graph):
        app = HushApp()
        app.endpoint("/double", graph=double_graph)
        assert len(app._endpoints) == 1
        assert app._endpoints[0].config.path == "/double"

    def test_multiple_endpoints(self, double_graph, greet_graph):
        app = HushApp()
        app.endpoint("/double", graph=double_graph)
        app.endpoint("/greet", graph=greet_graph)
        assert len(app._endpoints) == 2

    def test_endpoint_config_override(self, double_graph):
        app = HushApp(mode="python")
        app.endpoint("/double", graph=double_graph, batch=False, jobs=True)
        ep = app._endpoints[0]
        assert ep.config.batch is False
        assert ep.config.jobs is True

    def test_decorator_style(self):
        @op
        def my_op(x: int):
            return {"result": x + 1}

        app = HushApp()

        @app.endpoint("/inc")
        @graph
        def incrementer(x):
            step = my_op(x=x)
            START >> step >> END

        assert len(app._endpoints) == 1
        assert app._endpoints[0].config.path == "/inc"

    def test_decorator_rejects_non_graph(self):
        app = HushApp()
        with pytest.raises(TypeError, match="Expected a GraphOp"):

            @app.endpoint("/bad")
            def not_a_graph(x):
                return x


class TestFastAPIGeneration:
    def test_health_endpoint(self, double_graph):
        app = HushApp()
        app.endpoint("/double", graph=double_graph)
        client = TestClient(app.fastapi)
        resp = client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert "/double" in data["endpoints"]

    def test_endpoints_listing(self, double_graph, greet_graph):
        app = HushApp()
        app.endpoint("/double", graph=double_graph)
        app.endpoint("/greet", graph=greet_graph)
        client = TestClient(app.fastapi)
        resp = client.get("/endpoints")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["endpoints"]) == 2
        paths = [e["path"] for e in data["endpoints"]]
        assert "/double" in paths
        assert "/greet" in paths

    def test_openapi_docs(self, double_graph):
        app = HushApp()
        app.endpoint("/double", graph=double_graph)
        client = TestClient(app.fastapi)
        resp = client.get("/openapi.json")
        assert resp.status_code == 200
        spec = resp.json()
        assert "/double" in spec["paths"]

    def test_cors_headers(self, double_graph):
        app = HushApp(cors=True)
        app.endpoint("/double", graph=double_graph)
        client = TestClient(app.fastapi)
        resp = client.options(
            "/double",
            headers={
                "Origin": "http://example.com",
                "Access-Control-Request-Method": "POST",
            },
        )
        assert "access-control-allow-origin" in resp.headers


class TestRustBackend:
    def test_rust_bridge_serialize(self, double_graph):
        from hush.serve._rust_bridge import serialize_for_rust

        app = HushApp()
        app.endpoint("/double", graph=double_graph)
        config = serialize_for_rust(app)
        assert len(config.endpoints) == 1
        assert config.endpoints[0]["path"] == "/double"
        assert "graph" in config.endpoints[0]

    def test_rust_bridge_config_json(self, double_graph):
        import json

        from hush.serve._rust_bridge import serialize_for_rust

        app = HushApp()
        app.endpoint("/double", graph=double_graph)
        config = serialize_for_rust(app)
        config.host = "127.0.0.1"
        config.port = 9000
        data = json.loads(config.to_json())
        assert data["host"] == "127.0.0.1"
        assert data["port"] == 9000
        assert len(data["endpoints"]) == 1
