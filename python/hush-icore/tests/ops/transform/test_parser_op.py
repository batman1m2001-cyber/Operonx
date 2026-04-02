"""Tests for ParserOp - text parsing and extraction node."""

import pytest

from hush.core.ops.base import END, PARENT, START
from hush.core.ops.graph.graph_op import GraphOp
from hush.core.ops.transform.parser_op import ParserOp
from hush.core.states import MemoryState, StateSchema

# ============================================================
# Test 1: JSON Parser
# ============================================================


class TestJSONParser:
    """Test JSON format parsing."""

    @pytest.mark.asyncio
    async def test_json_parser_in_graph(self):
        """Test JSON parser within a graph context."""
        with GraphOp(name="json_workflow") as graph:
            json_parser = ParserOp(
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

        result = {}
        async for _, result in json_parser.run(state):
            pass
        assert result["name"] == "John"
        assert result["age"] == 30
        assert result["status"] == "active"

    @pytest.mark.asyncio
    async def test_json_parser_updates_state(self):
        """Test that parser updates state correctly."""
        with GraphOp(name="json_workflow") as graph:
            json_parser = ParserOp(
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

        async for _ in json_parser.run(state):
            pass
        assert state["json_workflow.json_parser", "name"] == "John"

    def test_json_parser_quick_call(self):
        """Test JSON parser with direct __call__."""
        parser = ParserOp(name="quick_json", format="json", extract=["name", "age"])
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

        with GraphOp(name="xml_workflow") as graph:
            xml_parser = ParserOp(
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

        result = {}
        async for _, result in xml_parser.run(state):
            pass
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
        parser = ParserOp(name="test_parser", format="json", extract=["name"])
        assert "text" in parser.inputs

    def test_parser_outputs_match_schema(self):
        """Test that outputs match extract."""
        parser = ParserOp(name="test_parser", format="json", extract=["name", "age", "status"])
        assert "name" in parser.outputs
        assert "age" in parser.outputs
        assert "status" in parser.outputs

    def test_parser_nested_schema_outputs(self):
        """Test outputs with nested schema (dot notation)."""
        parser = ParserOp(name="test_parser", format="json", extract=["user.name", "user.email"])
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
            ParserOp(name="bad_parser", format="json")

    @pytest.mark.asyncio
    async def test_invalid_json_returns_none(self):
        """Test that invalid JSON returns empty dict and captures error."""
        with GraphOp(name="invalid_json_graph") as graph:
            parser = ParserOp(
                name="test_parser", format="json", extract=["name"], inputs={"text": PARENT["text"]}
            )
            START >> parser >> END

        graph.build()
        schema = StateSchema(graph)
        state = MemoryState(schema, inputs={"text": "not valid json"})
        async for _ in parser.run(state):
            pass

        # Error should be captured in state
        error = state["invalid_json_graph.test_parser", "error"]
        assert error is not None
        assert "JSON" in error or "json" in error.lower()


# ============================================================
# Test 4: Type Conversion
# ============================================================


class TestTypeConversion:
    """Test automatic type conversion based on type hints."""

    def test_boolean_string_to_bool_xml(self):
        """Test XML boolean strings ("true"/"false") are converted to bool."""
        parser = ParserOp(
            name="bool_parser",
            format="xml",
            extract=["verified: bool", "active: bool", "reason: str"],
        )

        # XML always returns text as strings
        xml_text = "<verified>true</verified><active>false</active><reason>Test</reason>"
        result = parser(text=xml_text)

        # Should be converted to actual booleans
        assert result["verified"] is True
        assert isinstance(result["verified"], bool)
        assert result["active"] is False
        assert isinstance(result["active"], bool)
        assert result["reason"] == "Test"
        assert isinstance(result["reason"], str)

    def test_boolean_variations_xml(self):
        """Test various boolean string representations."""
        parser = ParserOp(name="bool_parser", format="xml", extract=["flag: bool"])

        # Test "true" variations
        for true_val in ["true", "True", "TRUE", "1", "yes", "Yes"]:
            result = parser(text=f"<flag>{true_val}</flag>")
            assert result["flag"] is True, f"Failed for '{true_val}'"

        # Test "false" variations (empty elements return None, not empty string)
        for false_val in ["false", "False", "FALSE", "0", "no", "No"]:
            result = parser(text=f"<flag>{false_val}</flag>")
            assert result["flag"] is False, f"Failed for '{false_val}'"

    def test_boolean_with_json_already_bool(self):
        """Test JSON booleans (already bool type) pass through correctly."""
        parser = ParserOp(name="json_parser", format="json", extract=["enabled: bool"])

        # JSON parses booleans natively, should preserve them
        result = parser(text='{"enabled": true}')
        assert result["enabled"] is True
        assert isinstance(result["enabled"], bool)

        result = parser(text='{"enabled": false}')
        assert result["enabled"] is False

    def test_int_conversion(self):
        """Test integer type conversion."""
        parser = ParserOp(name="int_parser", format="xml", extract=["count: int", "score: int"])

        result = parser(text="<count>42</count><score>100</score>")
        assert result["count"] == 42
        assert isinstance(result["count"], int)
        assert result["score"] == 100

    def test_float_conversion(self):
        """Test float type conversion."""
        parser = ParserOp(
            name="float_parser", format="xml", extract=["price: float", "rate: float"]
        )

        result = parser(text="<price>19.99</price><rate>0.05</rate>")
        assert result["price"] == 19.99
        assert isinstance(result["price"], float)
        assert result["rate"] == 0.05

    def test_none_value_handling(self):
        """Test that None values are handled correctly."""
        parser = ParserOp(name="none_parser", format="xml", extract=["missing: bool"])

        # Missing field returns None
        result = parser(text="<other>value</other>")
        assert result["missing"] is None

    @pytest.mark.asyncio
    async def test_boolean_in_branch_condition(self):
        """Test boolean extraction works with BranchOp (the original issue)."""
        from hush.core.ops.flow.branch_op import if_
        from hush.core.ops.transform.func_op import FuncOp

        with GraphOp(name="branch_test") as graph:
            # Simulate LLM extraction returning XML with boolean
            extractor = ParserOp(
                name="extractor",
                format="xml",
                extract=["verified: bool"],
                inputs={"text": PARENT["text"]},
            )

            # Use plain boolean ref (no == True comparison)
            router = if_(extractor["verified"], "success").else_("failure")

            success_node = FuncOp(
                name="success",
                code_fn=lambda: {"result": "Verified!"},
                inputs={},
                outputs={"*": PARENT},
            )
            failure_node = FuncOp(
                name="failure",
                code_fn=lambda: {"result": "Not verified!"},
                inputs={},
                outputs={"*": PARENT},
            )

            START >> extractor >> router
            router >> [success_node, failure_node]
            [success_node, failure_node] >> END

        graph.build()
        schema = StateSchema(graph)

        # Test with verified=true
        state = MemoryState(schema, inputs={"text": "<verified>true</verified>"})
        result = {}
        async for _, result in graph.run(state):
            pass
        assert result["result"] == "Verified!"

        # Test with verified=false
        state = MemoryState(schema, inputs={"text": "<verified>false</verified>"})
        result = {}
        async for _, result in graph.run(state):
            pass
        assert result["result"] == "Not verified!"
