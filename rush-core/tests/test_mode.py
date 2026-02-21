"""Tests for execution mode detection and switching."""

from rush_core import ExecutionMode, is_rust_available


class TestExecutionMode:
    def test_enum_values(self):
        assert ExecutionMode.PYTHON == "python"
        assert ExecutionMode.RUST == "rust"

    def test_string_comparison(self):
        assert ExecutionMode.PYTHON == "python"
        assert ExecutionMode.RUST == "rust"
        assert ExecutionMode("python") == ExecutionMode.PYTHON
        assert ExecutionMode("rust") == ExecutionMode.RUST


class TestRustAvailability:
    def test_is_rust_available_returns_bool(self):
        result = is_rust_available()
        assert isinstance(result, bool)

    def test_is_rust_available_true_when_built(self):
        """When the native module is built and installed, this should be True."""
        # This test only passes after `maturin develop`
        assert is_rust_available() is True

    def test_imports_work(self):
        """Verify that Rush can be imported."""
        from rush_core import Rush  # noqa: F401
