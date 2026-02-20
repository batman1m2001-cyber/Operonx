"""Tests for Phase 5 inline Rust execution.

Tests that:
- try_execute_inline() executes Rust ops entirely in Rust
- all_rust_executable() correctly detects all-Rust graphs
- run_sync() runs entire graph synchronously in Rust
- Cross-mode equivalence: Rust inline vs Python — identical results
- Mixed graphs (some Rust, some Python) work correctly
- PARENT refs and sibling refs resolve correctly in inline mode
"""

import pytest

from hush.core import END, PARENT, START, GraphOp, Hush, op
from hush.core.ops.transform.func_op import FuncOp
from hush_runtime import RustFuncRegistry, rust_op


# =============================================================================
# try_execute_inline — basic tests
# =============================================================================


class TestTryExecuteInline:
    async def test_inline_double(self):
        """Single Rust op executes inline."""

        @rust_op("rust_double")
        @op
        def double(x: int):
            return {"result": x * 2}

        with GraphOp(name="test") as graph:
            d = double(x=PARENT["x"])
            START >> d >> END

        result = await Hush(graph, mode="rust").run(inputs={"x": 5})
        assert result["result"] == 10

    async def test_inline_add(self):
        """Multi-input Rust op executes inline."""

        @rust_op("rust_add")
        @op
        def add(a: int, b: int):
            return {"result": a + b}

        with GraphOp(name="test") as graph:
            a = add(a=PARENT["a"], b=PARENT["b"])
            START >> a >> END

        result = await Hush(graph, mode="rust").run(inputs={"a": 3, "b": 7})
        assert result["result"] == 10

    async def test_inline_chain(self):
        """Chain of Rust ops executes inline."""

        @rust_op("rust_double")
        @op
        def double(x: int):
            return {"result": x * 2}

        @rust_op("rust_add")
        @op
        def add(a: int, b: int):
            return {"result": a + b}

        with GraphOp(name="chain") as graph:
            d = double(x=PARENT["x"])
            a = add(a=d["result"], b=PARENT["y"])
            START >> d >> a >> END

        result = await Hush(graph, mode="rust").run(inputs={"x": 5, "y": 3})
        assert result["result"] == 13  # (5*2) + 3

    async def test_inline_hash_chain(self):
        """CPU-heavy Rust op executes inline with GIL release."""

        @rust_op("rust_hash_chain")
        @op
        def hash_chain(data: str, iterations: int):
            return {"hash": "placeholder"}

        with GraphOp(name="test") as graph:
            h = hash_chain(data=PARENT["data"], iterations=PARENT["iters"])
            START >> h >> END

        result = await Hush(graph, mode="rust").run(
            inputs={"data": "hello", "iters": 100}
        )
        assert isinstance(result["hash"], str)
        assert len(result["hash"]) == 16  # 64-bit hex hash


# =============================================================================
# all_rust_executable — detection tests
# =============================================================================


class TestAllRustExecutable:
    def test_all_rust_graph(self):
        """Graph with all Rust ops is detected as all-Rust-executable."""

        @rust_op("rust_double")
        @op
        def double(x: int):
            return {"result": x * 2}

        with GraphOp(name="test") as graph:
            d = double(x=PARENT["x"])
            START >> d >> END

        graph.build()

        from hush_runtime import CompiledGraph

        cg = CompiledGraph.from_graph(graph)
        assert cg.all_rust_executable() is True

    def test_mixed_graph_not_all_rust(self):
        """Graph with a Python-only op is NOT all-Rust-executable."""

        @rust_op("rust_double")
        @op
        def double(x: int):
            return {"result": x * 2}

        @op
        def python_only(x: int):
            return {"result": x + 1}

        with GraphOp(name="test") as graph:
            d = double(x=PARENT["x"])
            p = python_only(x=d["result"])
            START >> d >> p >> END

        graph.build()

        from hush_runtime import CompiledGraph

        cg = CompiledGraph.from_graph(graph)
        assert cg.all_rust_executable() is False

    def test_async_op_not_all_rust(self):
        """Graph with an async op is NOT all-Rust-executable."""

        @rust_op("rust_double")
        @op
        async def async_double(x: int):
            return {"result": x * 2}

        with GraphOp(name="test") as graph:
            d = async_double(x=PARENT["x"])
            START >> d >> END

        graph.build()

        from hush_runtime import CompiledGraph

        cg = CompiledGraph.from_graph(graph)
        assert cg.all_rust_executable() is False


# =============================================================================
# run_sync — full Rust scheduling
# =============================================================================


class TestRunSync:
    async def test_run_sync_linear(self):
        """Linear chain runs entirely via run_sync."""

        @rust_op("rust_double")
        @op
        def double(x: int):
            return {"result": x * 2}

        @rust_op("rust_add")
        @op
        def add(a: int, b: int):
            return {"result": a + b}

        with GraphOp(name="sync_test") as graph:
            d = double(x=PARENT["x"])
            a = add(a=d["result"], b=PARENT["y"])
            START >> d >> a >> END

        result = await Hush(graph, mode="rust").run(inputs={"x": 4, "y": 1})
        assert result["result"] == 9  # (4*2) + 1

    async def test_run_sync_parallel(self):
        """Parallel Rust ops run via run_sync (sequential in sync mode)."""

        @rust_op("rust_double")
        @op
        def double(x: int):
            return {"result": x * 2}

        @rust_op("rust_add")
        @op
        def add(a: int, b: int):
            return {"result": a + b}

        with GraphOp(name="parallel") as graph:
            d1 = double(x=PARENT["x"])
            d2 = double(x=PARENT["y"])
            a = add(a=d1["result"], b=d2["result"])
            START >> [d1, d2]
            [d1, d2] >> a >> END

        result = await Hush(graph, mode="rust").run(inputs={"x": 3, "y": 5})
        assert result["result"] == 16  # (3*2) + (5*2)


# =============================================================================
# Cross-mode equivalence
# =============================================================================


class TestCrossModeEquivalence:
    @pytest.mark.parametrize("mode", ["python", "rust"])
    async def test_double_equivalence(self, mode):
        """Rust double op produces identical results in both modes."""

        @rust_op("rust_double")
        @op
        def double(x: int):
            return {"result": x * 2}

        with GraphOp(name="test") as graph:
            d = double(x=PARENT["x"])
            START >> d >> END

        result = await Hush(graph, mode=mode).run(inputs={"x": 7})
        assert result["result"] == 14

    @pytest.mark.parametrize("mode", ["python", "rust"])
    async def test_chain_equivalence(self, mode):
        """Chain of Rust ops produces identical results in both modes."""

        @rust_op("rust_double")
        @op
        def double(x: int):
            return {"result": x * 2}

        @rust_op("rust_add")
        @op
        def add(a: int, b: int):
            return {"result": a + b}

        with GraphOp(name="chain") as graph:
            d = double(x=PARENT["x"])
            a = add(a=d["result"], b=PARENT["y"])
            START >> d >> a >> END

        result = await Hush(graph, mode=mode).run(inputs={"x": 10, "y": 5})
        assert result["result"] == 25  # (10*2) + 5

    @pytest.mark.parametrize("mode", ["python", "rust"])
    async def test_parallel_equivalence(self, mode):
        """Parallel Rust ops produce identical results in both modes."""

        @rust_op("rust_double")
        @op
        def double(x: int):
            return {"result": x * 2}

        @rust_op("rust_add")
        @op
        def add(a: int, b: int):
            return {"result": a + b}

        with GraphOp(name="parallel") as graph:
            d1 = double(x=PARENT["x"])
            d2 = double(x=PARENT["y"])
            a = add(a=d1["result"], b=d2["result"])
            START >> [d1, d2]
            [d1, d2] >> a >> END

        result = await Hush(graph, mode=mode).run(inputs={"x": 3, "y": 5})
        assert result["result"] == 16  # (3*2) + (5*2)


# =============================================================================
# Mixed graph — hybrid execution
# =============================================================================


class TestMixedGraph:
    async def test_mixed_rust_python(self):
        """Graph with both Rust and Python ops works correctly."""

        @rust_op("rust_double")
        @op
        def rust_double(x: int):
            return {"result": x * 2}

        @op
        def python_add_one(x: int):
            return {"result": x + 1}

        with GraphOp(name="mixed") as graph:
            d = rust_double(x=PARENT["x"])
            p = python_add_one(x=d["result"])
            START >> d >> p >> END

        result = await Hush(graph, mode="rust").run(inputs={"x": 5})
        assert result["result"] == 11  # (5*2) + 1

    async def test_mixed_python_then_rust(self):
        """Python op followed by Rust op works correctly."""

        @op
        def python_double(x: int):
            return {"result": x * 2}

        @rust_op("rust_add")
        @op
        def rust_add(a: int, b: int):
            return {"result": a + b}

        with GraphOp(name="mixed") as graph:
            d = python_double(x=PARENT["x"])
            a = rust_add(a=d["result"], b=PARENT["y"])
            START >> d >> a >> END

        result = await Hush(graph, mode="rust").run(inputs={"x": 5, "y": 3})
        assert result["result"] == 13  # (5*2) + 3


# =============================================================================
# Built-in ops in inline mode
# =============================================================================


class TestBuiltinOpsInline:
    async def test_string_concat_inline(self):
        """Built-in string concat executes inline."""

        @rust_op("rust_string_concat")
        @op
        def concat(parts: list):
            return {"result": "".join(parts)}

        with GraphOp(name="test") as graph:
            c = concat(parts=PARENT["parts"])
            START >> c >> END

        result = await Hush(graph, mode="rust").run(
            inputs={"parts": ["hello", " ", "world"]}
        )
        assert result["result"] == "hello world"

    async def test_math_sum_inline(self):
        """Built-in math sum executes inline."""

        @rust_op("rust_math_sum")
        @op
        def total(values: list):
            return {"result": sum(values)}

        with GraphOp(name="test") as graph:
            s = total(values=PARENT["nums"])
            START >> s >> END

        result = await Hush(graph, mode="rust").run(inputs={"nums": [1, 2, 3, 4]})
        assert result["result"] == 10.0

    async def test_builtin_chain_inline(self):
        """Chain of built-in ops with matching types executes inline."""

        @rust_op("rust_math_sum")
        @op
        def total(values: list):
            return {"result": sum(values)}

        @rust_op("rust_math_mean")
        @op
        def mean(values: list):
            return {"result": sum(values) / len(values) if values else 0}

        with GraphOp(name="chain") as graph:
            s = total(values=PARENT["nums"])
            m = mean(values=PARENT["nums2"])
            START >> [s, m]
            [s, m] >> END

        result = await Hush(graph, mode="rust").run(
            inputs={"nums": [1, 2, 3], "nums2": [10, 20]}
        )
        # Both ops write to "result", last one wins based on execution order
        assert result["result"] in [6.0, 15.0]
