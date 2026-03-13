"""Tests for auto-schema generation from GraphOp metadata."""

from hush.serve.schema import build_request_model, build_response_model, graph_schema_info


class TestBuildRequestModel:
    def test_simple_int_input(self, double_graph):
        model = build_request_model(double_graph)
        assert model.__name__ == "DoublerRequest"
        fields = model.model_fields
        assert "x" in fields

    def test_multi_input(self, add_graph):
        model = build_request_model(add_graph)
        assert model.__name__ == "AdderRequest"
        fields = model.model_fields
        assert "a" in fields
        assert "b" in fields

    def test_string_input(self, greet_graph):
        model = build_request_model(greet_graph)
        fields = model.model_fields
        assert "name" in fields

    def test_model_validation(self, double_graph):
        model = build_request_model(double_graph)
        instance = model(x=5)
        assert instance.model_dump() == {"x": 5}

    def test_multi_input_validation(self, add_graph):
        model = build_request_model(add_graph)
        instance = model(a=3, b=7)
        assert instance.model_dump() == {"a": 3, "b": 7}


class TestBuildResponseModel:
    def test_simple_output(self, double_graph):
        model = build_response_model(double_graph)
        assert model.__name__ == "DoublerResponse"

    def test_string_output(self, greet_graph):
        model = build_response_model(greet_graph)
        assert model.__name__ == "GreeterResponse"


class TestGraphSchemaInfo:
    def test_schema_info(self, double_graph):
        info = graph_schema_info(double_graph)
        assert info["name"] == "doubler"
        assert "x" in info["inputs"]
        assert "result" in info["outputs"]

    def test_schema_info_multi_input(self, chain_graph):
        info = graph_schema_info(chain_graph)
        assert info["name"] == "chain"
        assert "x" in info["inputs"]
        assert "y" in info["inputs"]
