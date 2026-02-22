"""Tests for RustFuncRegistry — Rust-native op execution bridge.

Tests that:
- Registry has built-in ops (rust_double, rust_add, rust_hash_chain)
- Ops execute correctly via registry
- @op(rust=...) tags functions for Rust dispatch
- Rust ops work inside GraphOp workflows (both modes)
- Fallback to Python when Rust op not found
"""

import pytest

from hush.core import END, PARENT, START, GraphOp, Hush, op
from rush_core import RustFuncRegistry


# =============================================================================
# Registry basics
# =============================================================================


class TestRegistryBasics:
    def test_has_builtin_ops(self):
        assert RustFuncRegistry.has("rust_double")
        assert RustFuncRegistry.has("rust_add")
        assert RustFuncRegistry.has("rust_hash_chain")

    def test_has_returns_false_for_unknown(self):
        assert RustFuncRegistry.has("nonexistent") is False

    def test_list_ops(self):
        ops = RustFuncRegistry.list_ops()
        assert "rust_double" in ops
        assert "rust_add" in ops
        assert "rust_hash_chain" in ops


# =============================================================================
# Direct execution
# =============================================================================


class TestDirectExecution:
    def test_double(self):
        result = RustFuncRegistry.execute("rust_double", {"x": 21})
        assert result["result"] == 42

    def test_add(self):
        result = RustFuncRegistry.execute("rust_add", {"a": 10, "b": 32})
        assert result["result"] == 42

    def test_hash_chain(self):
        result = RustFuncRegistry.execute(
            "rust_hash_chain", {"data": "hello", "iterations": 10}
        )
        assert isinstance(result["hash"], str)
        assert len(result["hash"]) == 16  # 16 hex chars

    def test_hash_chain_deterministic(self):
        r1 = RustFuncRegistry.execute(
            "rust_hash_chain", {"data": "hello", "iterations": 5}
        )
        r2 = RustFuncRegistry.execute(
            "rust_hash_chain", {"data": "hello", "iterations": 5}
        )
        assert r1["hash"] == r2["hash"]

    def test_unknown_op_returns_none(self):
        result = RustFuncRegistry.execute("nonexistent", {"x": 1})
        assert result is None

    def test_missing_input_raises(self):
        with pytest.raises(KeyError):
            RustFuncRegistry.execute("rust_double", {})


# =============================================================================
# @op(rust=...) decorator
# =============================================================================


class TestRustOpDecorator:
    def test_decorator_tags_function(self):
        @op(rust="rust_double")
        def my_func(x: int):
            return {"result": x * 2}

        # Call the wrapper to get a FuncOp, check the inner function
        func_op = my_func(x=5)
        assert func_op.core._rust_op_name == "rust_double"

    def test_decorator_preserves_function(self):
        @op(rust="rust_double")
        def my_func(x: int):
            return {"result": x * 2}

        # Wrapper still produces working FuncOps
        func_op = my_func(x=5)
        assert func_op.core(x=5) == {"result": 10}


# =============================================================================
# Integration: Rust ops in workflows
# =============================================================================


class TestRustOpInWorkflow:
    async def test_rust_op_single(self):
        """Single Rust op in a graph."""

        @op(rust="rust_double")
        def double(x: int):
            return {"result": x * 2}

        with GraphOp(name="test") as graph:
            step = double(x=PARENT["x"])
            START >> step >> END

        result = await Hush(graph, mode="rust").run(inputs={"x": 21})
        assert result["result"] == 42

    async def test_rust_op_chain(self):
        """Chain of Rust ops."""

        @op(rust="rust_double")
        def double(x: int):
            return {"result": x * 2}

        @op(rust="rust_add")
        def add(a: int, b: int):
            return {"result": a + b}

        with GraphOp(name="chain") as graph:
            d = double(x=PARENT["x"])
            a = add(a=d["result"], b=PARENT["y"])
            START >> d >> a >> END

        result = await Hush(graph, mode="rust").run(inputs={"x": 5, "y": 3})
        assert result["result"] == 13  # 5*2=10, 10+3=13

    async def test_mixed_rust_and_python_ops(self):
        """Graph with both Rust and Python ops."""

        @op(rust="rust_double")
        def rust_double(x: int):
            return {"result": x * 2}

        @op
        def python_add(a: int, b: int):
            return {"result": a + b}

        with GraphOp(name="mixed") as graph:
            d = rust_double(x=PARENT["x"])
            a = python_add(a=d["result"], b=PARENT["y"])
            START >> d >> a >> END

        result = await Hush(graph, mode="rust").run(inputs={"x": 5, "y": 3})
        assert result["result"] == 13

    async def test_fallback_to_python(self):
        """Unregistered rust= falls back to Python."""

        @op(rust="nonexistent_op")
        def fallback(x: int):
            return {"result": x * 99}

        with GraphOp(name="fallback") as graph:
            step = fallback(x=PARENT["x"])
            START >> step >> END

        result = await Hush(graph, mode="rust").run(inputs={"x": 2})
        assert result["result"] == 198  # Python fallback: 2*99

    @pytest.mark.parametrize("mode", ["python", "rust"])
    async def test_rust_op_both_modes(self, mode):
        """Rust op produces same result in both modes."""

        @op(rust="rust_double")
        def double(x: int):
            return {"result": x * 2}

        with GraphOp(name="both") as graph:
            step = double(x=PARENT["x"])
            START >> step >> END

        result = await Hush(graph, mode=mode).run(inputs={"x": 7})
        assert result["result"] == 14
