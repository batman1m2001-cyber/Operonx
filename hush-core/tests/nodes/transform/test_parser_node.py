"""Tests for ParserNode - text parsing and extraction node."""

import pytest

from hush.core.nodes.base import END, PARENT, START
from hush.core.nodes.graph.graph_node import GraphNode
from hush.core.nodes.transform.parser_node import ParserNode
from hush.core.states import MemoryState, StateSchema

# ============================================================
# Test 1: JSON Parser
# ============================================================


class TestJSONParser:
    """Test JSON format parsing."""

    @pytest.mark.asyncio
    async def test_json_parser_in_graph(self):
        """Test JSON parser within a graph context."""
        with GraphNode(name="json_workflow") as graph:
            json_parser = ParserNode(
                name="json_parser",
                format="json",
                extract=[
                    "user.name",
                    "user.age",
                    "status",
                ],
                inputs={"text": PARENT["text"]},
            )
            START >> json_parser >> END

        graph.build()
        schema = StateSchema(graph)

        json_text = '{"user": {"name": "John", "age": 30}, "status": "active"}'
        state = MemoryState(schema, inputs={"text": json_text})

        result = await json_parser.run(state)
        assert result["name"] == "John"
        assert result["age"] == 30
        assert result["status"] == "active"

    @pytest.mark.asyncio
    async def test_json_parser_updates_state(self):
        """Test that parser updates state correctly."""
        with GraphNode(name="json_workflow") as graph:
            json_parser = ParserNode(
                name="json_parser",
                format="json",
                extract=["user.name"],
                inputs={"text": PARENT["text"]},
            )
            START >> json_parser >> END

        graph.build()
        schema = StateSchema(graph)

        json_text = '{"user": {"name": "John"}}'
        state = MemoryState(schema, inputs={"text": json_text})

        await json_parser.run(state)
        assert state["json_workflow.json_parser", "name", None] == "John"

    def test_json_parser_quick_call(self):
        """Test JSON parser with direct __call__."""
        parser = ParserNode(name="quick_json", format="json", extract=["name", "age"])
        result = parser(text='{"name": "Bob", "age": 25}')
        assert result["name"] == "Bob"
        assert result["age"] == 25


# ============================================================
# Test 2: XML Parser
# ============================================================


class TestXMLParser:
    """Test XML format parsing."""

    @pytest.mark.asyncio
    async def test_xml_parser_in_graph(self):
        """Test XML parser within a graph context."""
        xml_text = """
        <response>
            <user>
                <name>Alice</name>
                <email>alice@example.com</email>
            </user>
            <code>200</code>
        </response>
        """

        with GraphNode(name="xml_workflow") as graph:
            xml_parser = ParserNode(
                name="xml_parser",
                format="xml",
                extract=[
                    "response.user.name",
                    "response.user.email",
                    "response.code",
                ],
                inputs={"text": PARENT["text"]},
            )
            START >> xml_parser >> END

        graph.build()
        schema = StateSchema(graph)
        state = MemoryState(schema, inputs={"text": xml_text})

        result = await xml_parser.run(state)
        assert result["name"] == "Alice"
        assert result["email"] == "alice@example.com"
        assert result["code"] == "200"


# ============================================================
# Test 5: Schema Extraction
# ============================================================


class TestParserSchemaExtraction:
    """Test automatic schema extraction."""

    def test_parser_has_text_input(self):
        """Test that parser always has 'text' input."""
        parser = ParserNode(name="test_parser", format="json", extract=["name"])
        assert "text" in parser.inputs

    def test_parser_outputs_match_schema(self):
        """Test that outputs match extract."""
        parser = ParserNode(name="test_parser", format="json", extract=["name", "age", "status"])
        assert "name" in parser.outputs
        assert "age" in parser.outputs
        assert "status" in parser.outputs

    def test_parser_nested_schema_outputs(self):
        """Test outputs with nested schema (dot notation)."""
        parser = ParserNode(name="test_parser", format="json", extract=["user.name", "user.email"])
        # Output keys should be the last part of the path
        assert "name" in parser.outputs
        assert "email" in parser.outputs


# ============================================================
# Test 6: Error Handling
# ============================================================


class TestParserErrors:
    """Test parser error handling."""

    def test_missing_extract_raises(self):
        """Test that missing extract raises error."""
        with pytest.raises(TypeError):
            ParserNode(name="bad_parser", format="json")

    @pytest.mark.asyncio
    async def test_invalid_json_returns_none(self):
        """Test that invalid JSON returns empty dict and captures error."""
        with GraphNode(name="invalid_json_graph") as graph:
            parser = ParserNode(
                name="test_parser", format="json", extract=["name"], inputs={"text": PARENT["text"]}
            )
            START >> parser >> END

        graph.build()
        schema = StateSchema(graph)
        state = MemoryState(schema, inputs={"text": "not valid json"})
        await parser.run(state)

        # Error should be captured in state
        error = state["invalid_json_graph.test_parser", "error", None]
        assert error is not None
        assert "JSON" in error or "json" in error.lower()
