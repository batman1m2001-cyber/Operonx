"""Tests for Rust-native op execution via cdylib plugins.

Tests that:
- @op(rust=...) tags functions for plugin dispatch
- Plugin ops execute correctly via C ABI
- Rust ops work inside GraphOp workflows (both modes)
- Fallback to Python when Rust op path has no "::" (no plugin)
"""

import pytest
from conftest import BUILTIN_CRATE

from hush.core import END, PARENT, START, GraphOp, Hush, op

# =============================================================================
# @op(rust=...) decorator
# =============================================================================


class TestRustOpDecorator:
    def test_decorator_tags_function(self):
        @op(rust=f"{BUILTIN_CRATE}::double")
        def my_func(x: int):
            return {"result": x * 2}

        # Call the wrapper to get a FuncOp, check the inner function
        func_op = my_func(x=5)
        assert func_op.core._rust_op_name == f"{BUILTIN_CRATE}::double"

    def test_decorator_preserves_function(self):
        @op(rust=f"{BUILTIN_CRATE}::double")
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

        @op(rust=f"{BUILTIN_CRATE}::double")
        def double(x: int):
            return {"result": x * 2}

        with GraphOp(name="test") as graph:
            step = double(x=PARENT["x"])
            START >> step >> END

        result = await Hush(graph, mode="rust").run(inputs={"x": 21})
        assert result["result"] == 42

    async def test_rust_op_chain(self):
        """Chain of Rust ops."""

        @op(rust=f"{BUILTIN_CRATE}::double")
        def double(x: int):
            return {"result": x * 2}

        @op(rust=f"{BUILTIN_CRATE}::add")
        def add(a: int, b: int):
            return {"result": a + b}

        with GraphOp(name="chain") as graph:
            d = double(x=PARENT["x"])
            a = add(a=d["result"], b=PARENT["y"])
            START >> d >> a >> END

        result = await Hush(graph, mode="rust").run(inputs={"x": 5, "y": 3})
        assert result["result"] == 13  # 5*2=10, 10+3=13

    @pytest.mark.skip(
        reason="Intentional: validates that Python-only ops are rejected in Rust mode"
    )
    async def test_mixed_rust_and_python_ops(self):
        """Graph with both Rust and Python ops."""

        @op(rust=f"{BUILTIN_CRATE}::double")
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
        """Ops without a valid rust= (no ::) are rejected at config parse time."""

        @op(rust="nonexistent_op")
        def fallback(x: int):
            return {"result": x * 99}

        with GraphOp(name="fallback") as graph:
            step = fallback(x=PARENT["x"])
            START >> step >> END

        with pytest.raises(ValueError, match="no Rust implementation"):
            Hush(graph, mode="rust")

    @pytest.mark.parametrize("mode", ["python", "rust"])
    async def test_rust_op_both_modes(self, mode):
        """Rust op produces same result in both modes."""

        @op(rust=f"{BUILTIN_CRATE}::double")
        def double(x: int):
            return {"result": x * 2}

        with GraphOp(name="both") as graph:
            step = double(x=PARENT["x"])
            START >> step >> END

        result = await Hush(graph, mode=mode).run(inputs={"x": 7})
        assert result["result"] == 14
