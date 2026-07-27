"""Tests for unified exception classes in operonx.core.exceptions."""

import pytest

from operonx.core.exceptions import (
    BranchError,
    CodeError,
    EmbeddingError,
    OpError,
    ParserError,
    PromptError,
    RerankError,
    _truncate,
)

# ============================================================
# Test _truncate helper
# ============================================================


class TestTruncateHelper:
    """Test the _truncate utility function."""

    def test_short_text_unchanged(self):
        """Text shorter than max_length is not modified."""
        text = "Hello World"
        result = _truncate(text, max_length=200)
        assert result == "Hello World"

    def test_exact_length_unchanged(self):
        """Text exactly at max_length is not modified."""
        text = "x" * 200
        result = _truncate(text, max_length=200)
        assert result == text
        assert len(result) == 200

    def test_long_text_truncated(self):
        """Text longer than max_length is truncated with ellipsis."""
        text = "x" * 300
        result = _truncate(text, max_length=200)
        assert result == "x" * 200 + "..."
        assert len(result) == 203  # 200 + "..."

    def test_custom_max_length(self):
        """Custom max_length is respected."""
        text = "Hello World"
        result = _truncate(text, max_length=5)
        assert result == "Hello..."


# ============================================================
# Test OpError (base class)
# ============================================================


class TestOpError:
    """Test base OpError class."""

    def test_basic_error(self):
        """Test basic error creation."""
        error = OpError(message="Something went wrong", op_type="test")
        assert "[TEST] Something went wrong" in str(error)

    def test_with_original_error(self):
        """Test error with original exception."""
        original = ValueError("Original error")
        error = OpError(message="Wrapped error", op_type="test", original_error=original)
        msg = str(error)
        assert "[TEST] Wrapped error" in msg
        assert "Error: Original error" in msg

    def test_with_context(self):
        """Test error with context dictionary."""
        error = OpError(
            message="Error with context", op_type="test", context={"key1": "value1", "key2": 42}
        )
        msg = str(error)
        assert "key1: 'value1'" in msg
        assert "key2: 42" in msg

    def test_attributes_stored(self):
        """Test that attributes are stored correctly."""
        original = ValueError("test")
        error = OpError(
            message="Test", op_type="custom", original_error=original, context={"foo": "bar"}
        )
        assert error.op_type == "custom"
        assert error.original_error is original
        assert error.context == {"foo": "bar"}


# ============================================================
# Test ParserError
# ============================================================


class TestParserError:
    """Test ParserError for parsing failures."""

    def test_basic_parser_error(self):
        """Test basic parser error creation."""
        error = ParserError(
            message="Invalid JSON syntax", input_text='{"broken": ', format_type="json"
        )
        msg = str(error)
        assert "[PARSER]" in msg
        assert "Invalid JSON syntax" in msg
        assert "format:" in msg and "json" in msg

    def test_with_original_error(self):
        """Test parser error with original exception."""
        import json

        try:
            json.loads('{"broken": ')
        except json.JSONDecodeError as e:
            error = ParserError(
                message="Failed to parse JSON",
                input_text='{"broken": ',
                format_type="json",
                original_error=e,
            )
        msg = str(error)
        assert "JSONDecodeError" in msg or "Expecting" in msg

    def test_long_input_truncated(self):
        """Test that long input text is truncated in error message."""
        long_text = "x" * 500
        error = ParserError(message="Parse failed", input_text=long_text, format_type="json")
        msg = str(error)
        # Input should be truncated (default 200 chars in repr)
        assert "..." in msg

    def test_attributes_stored(self):
        """Test that attributes are stored."""
        error = ParserError(message="Test", input_text="input", format_type="xml")
        assert error.input_text == "input"
        assert error.format_type == "xml"


# ============================================================
# Test CodeError
# ============================================================


class TestCodeError:
    """Test CodeError for user function failures."""

    def test_basic_code_error(self):
        """Test basic code error creation."""
        error = CodeError(
            message="Function 'calc' raised an exception",
            function_name="calc",
            source="def calc(x): return x / 0",
            inputs={"x": 10},
        )
        msg = str(error)
        assert "[CODE]" in msg
        assert "function_name:" in msg and "calc" in msg
        assert "source:" in msg

    def test_with_division_error(self):
        """Test code error with ZeroDivisionError."""
        try:
            1 / 0
        except ZeroDivisionError as e:
            error = CodeError(
                message="Division by zero",
                function_name="divide",
                source="def divide(a, b): return a / b",
                inputs={"a": 1, "b": 0},
                original_error=e,
            )
        msg = str(error)
        assert "ZeroDivisionError" in msg or "division by zero" in msg

    def test_long_source_truncated(self):
        """Test that long source code is truncated."""
        long_source = "def func():\n" + "    x = 1\n" * 100
        error = CodeError(message="Error", function_name="func", source=long_source, inputs={})
        msg = str(error)
        # Source should be truncated at 300 chars
        assert "..." in msg

    def test_long_input_values_truncated(self):
        """Test that long input values are truncated."""
        error = CodeError(
            message="Error",
            function_name="func",
            source="def func(x): pass",
            inputs={"long_value": "x" * 200},
        )
        msg = str(error)
        # Each input value truncated at 100 chars
        assert "..." in msg

    def test_attributes_stored(self):
        """Test that attributes are stored."""
        error = CodeError(
            message="Test",
            function_name="my_func",
            source="def my_func(): pass",
            inputs={"a": 1, "b": 2},
        )
        assert error.function_name == "my_func"
        assert error.source == "def my_func(): pass"
        assert error.inputs == {"a": 1, "b": 2}


# ============================================================
# Test BranchError
# ============================================================


class TestBranchError:
    """Test BranchError for branch condition failures."""

    def test_basic_branch_error(self):
        """Test basic branch error creation."""
        error = BranchError(
            message="Condition evaluation failed",
            condition="score >= 90",
            inputs={"score": "invalid"},
            candidates=["excellent", "good", "fail"],
        )
        msg = str(error)
        assert "[BRANCH]" in msg
        assert "condition:" in msg and "score >= 90" in msg
        assert "candidates:" in msg

    def test_with_type_error(self):
        """Test branch error with TypeError."""
        try:
            "invalid" >= 90
        except TypeError as e:
            error = BranchError(
                message="Type mismatch in condition",
                condition="score >= 90",
                inputs={"score": "invalid"},
                candidates=["pass", "fail"],
                original_error=e,
            )
        msg = str(error)
        assert "TypeError" in msg or "not supported" in msg

    def test_attributes_stored(self):
        """Test that attributes are stored."""
        error = BranchError(
            message="Test", condition="x > 0", inputs={"x": -1}, candidates=["positive", "negative"]
        )
        assert error.condition == "x > 0"
        assert error.inputs == {"x": -1}
        assert error.candidates == ["positive", "negative"]


# ============================================================
# Test PromptError
# ============================================================


class TestPromptError:
    """Test PromptError for PromptOp template failures."""

    def test_missing_variable_error(self):
        """Test prompt error with missing template variable."""
        error = PromptError(
            message="Missing template variable(s)",
            prompt="Hello {name}, your order {order_id} is ready",
            missing_vars=["order_id"],
        )
        msg = str(error)
        assert "[PROMPT]" in msg
        assert "prompt_type:" in msg and "str" in msg
        assert "missing_vars:" in msg
        assert "order_id" in msg

    def test_multiple_missing_vars(self):
        """Test prompt error with multiple missing variables."""
        error = PromptError(
            message="Missing template variable(s)",
            prompt="Hello {name}, order {order_id} at {location}",
            missing_vars=["order_id", "location"],
        )
        msg = str(error)
        assert "order_id" in msg
        assert "location" in msg

    def test_invalid_template_type(self):
        """Test prompt error for invalid template type."""
        error = PromptError(
            message="Invalid template type",
            prompt=12345,  # Should be str/dict/list
            original_error=TypeError("Expected str, dict, or list"),
        )
        msg = str(error)
        assert "[PROMPT]" in msg
        assert "prompt_type:" in msg and "int" in msg

    def test_dict_template(self):
        """Test prompt error with dict template."""
        error = PromptError(
            message="Missing variable in system prompt",
            prompt={"system": "You are {role}", "user": "{query}"},
            missing_vars=["role"],
        )
        msg = str(error)
        assert "prompt_type:" in msg and "dict" in msg

    def test_list_template(self):
        """Test prompt error with list template."""
        error = PromptError(
            message="Missing variable",
            prompt=[{"role": "user", "content": "Hello {name}"}],
            missing_vars=["name"],
        )
        msg = str(error)
        assert "prompt_type:" in msg and "list" in msg

    def test_long_template_truncated(self):
        """Test that long template is truncated."""
        long_template = "Hello {name}, " + "x" * 500
        error = PromptError(message="Error", prompt=long_template, missing_vars=["name"])
        msg = str(error)
        assert "..." in msg

    def test_no_missing_vars(self):
        """Test that missing_vars is optional."""
        error = PromptError(message="Invalid template", prompt=None)
        msg = str(error)
        assert "missing_vars:" not in msg

    def test_attributes_stored(self):
        """Test that attributes are stored."""
        error = PromptError(message="Test", prompt="Hello {name}", missing_vars=["name"])
        assert error.prompt == "Hello {name}"
        assert error.missing_vars == ["name"]


# ============================================================
# Test EmbeddingError
# ============================================================


class TestEmbeddingError:
    """Test EmbeddingError for EmbeddingOp failures."""

    def test_basic_embedding_error(self):
        """Test basic embedding error creation."""
        error = EmbeddingError(
            message="Embedding backend failed", resource="bge-m3", text_count=100
        )
        msg = str(error)
        assert "[EMBEDDING]" in msg
        assert "resource:" in msg and "bge-m3" in msg
        assert "text_count:" in msg and "100" in msg

    def test_with_connection_error(self):
        """Test embedding error with connection error."""
        error = EmbeddingError(
            message="Failed to connect to embedding service",
            resource="text-embedding-ada-002",
            text_count=50,
            original_error=ConnectionError("Connection refused"),
        )
        msg = str(error)
        assert "Connection" in msg

    def test_attributes_stored(self):
        """Test that attributes are stored."""
        error = EmbeddingError(message="Test", resource="model-key", text_count=25)
        assert error.resource == "model-key"
        assert error.text_count == 25


# ============================================================
# Test RerankError
# ============================================================


class TestRerankError:
    """Test RerankError for RerankOp failures."""

    def test_basic_rerank_error(self):
        """Test basic rerank error creation."""
        error = RerankError(
            message="Invalid document type",
            resource="bge-m3",
            query="search query",
            document_count=50,
        )
        msg = str(error)
        assert "[RERANK]" in msg
        assert "resource:" in msg and "bge-m3" in msg
        assert "query:" in msg
        assert "document_count:" in msg and "50" in msg

    def test_with_type_error(self):
        """Test rerank error with type error."""
        error = RerankError(
            message="Invalid document type",
            resource="bge-m3",
            query="test query",
            document_count=10,
            original_error=TypeError("Expected List[str] or List[Dict]"),
        )
        msg = str(error)
        assert "TypeError" in msg or "List[str]" in msg

    def test_long_query_truncated(self):
        """Test that long query is truncated."""
        long_query = "search " * 100
        error = RerankError(message="Error", resource="model", query=long_query, document_count=10)
        msg = str(error)
        # Query truncated at 100 chars
        assert "..." in msg

    def test_attributes_stored(self):
        """Test that attributes are stored."""
        error = RerankError(
            message="Test", resource="rerank-model", query="my query", document_count=30
        )
        assert error.resource == "rerank-model"
        assert error.query == "my query"
        assert error.document_count == 30


# ============================================================
# Test Error Inheritance
# ============================================================


class TestErrorInheritance:
    """Test that all errors properly inherit from OpError and Exception."""

    @pytest.mark.parametrize(
        "error_class,args",
        [
            (ParserError, {"message": "test", "input_text": "x", "format_type": "json"}),
            (CodeError, {"message": "test", "function_name": "f", "source": "x", "inputs": {}}),
            (BranchError, {"message": "test", "condition": "x", "inputs": {}, "candidates": []}),
            (PromptError, {"message": "test", "prompt": "x"}),
            (EmbeddingError, {"message": "test", "resource": "k", "text_count": 1}),
            (
                RerankError,
                {"message": "test", "resource": "k", "query": "q", "document_count": 1},
            ),
        ],
    )
    def test_inherits_from_node_error(self, error_class, args):
        """Test that all error classes inherit from OpError."""
        error = error_class(**args)
        assert isinstance(error, OpError)
        assert isinstance(error, Exception)

    @pytest.mark.parametrize(
        "error_class,args",
        [
            (ParserError, {"message": "test", "input_text": "x", "format_type": "json"}),
            (CodeError, {"message": "test", "function_name": "f", "source": "x", "inputs": {}}),
            (BranchError, {"message": "test", "condition": "x", "inputs": {}, "candidates": []}),
            (PromptError, {"message": "test", "prompt": "x"}),
            (EmbeddingError, {"message": "test", "resource": "k", "text_count": 1}),
            (
                RerankError,
                {"message": "test", "resource": "k", "query": "q", "document_count": 1},
            ),
        ],
    )
    def test_can_be_raised_and_caught(self, error_class, args):
        """Test that errors can be raised and caught properly."""
        with pytest.raises(error_class):
            raise error_class(**args)

        # Also verify catching as OpError works
        with pytest.raises(OpError):
            raise error_class(**args)


# ============================================================
# Test Error Message Format
# ============================================================


class TestErrorMessageFormat:
    """Test that error messages follow consistent format."""

    def test_message_format_structure(self):
        """Test that message follows [TYPE] message\\n  key: value format."""
        error = CodeError(
            message="Function failed",
            function_name="my_func",
            source="def my_func(): pass",
            inputs={"x": 1},
            original_error=ValueError("test"),
        )
        lines = str(error).split("\n")

        # First line: [TYPE] message
        assert lines[0].startswith("[CODE]")
        assert "Function failed" in lines[0]

        # Subsequent lines: "  key: value" (indented)
        for line in lines[1:]:
            assert line.startswith("  "), f"Line should be indented: {line}"

    def test_all_node_types_uppercase(self):
        """Test that op_type is always displayed in uppercase."""
        errors = [
            ParserError("msg", "input", "json"),
            CodeError("msg", "fn", "src", {}),
            BranchError("msg", "cond", {}, []),
            PromptError("msg", "template"),
            EmbeddingError("msg", "key", 1),
            RerankError("msg", "key", "query", 1),
        ]

        for error in errors:
            msg = str(error)
            # Extract the bracket content
            bracket_content = msg[msg.index("[") + 1 : msg.index("]")]
            assert bracket_content.isupper(), f"Node type should be uppercase: {bracket_content}"
