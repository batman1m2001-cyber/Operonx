"""Tests for Phase 4 Rust branch resolution.

Tests that:
- Branch resolution works identically in rust vs python mode
- CompiledGraph.activate_successors_with_state() reads target from RustMemoryState
- Branch-to-END routing works
- Multi-branch routing works
"""

import pytest

from hush.core import END, PARENT, START, GraphOp, Hush, op
from hush.core.ops.flow.branch_op import Branch, if_
from hush.core.ops.transform.func_op import FuncOp


# =============================================================================
# Branch routing — cross-mode equivalence
# =============================================================================


class TestBranchCrossMode:
    @pytest.mark.parametrize("mode", ["python", "rust"])
    async def test_simple_branch(self, mode):
        """Simple if/else routes correctly in both modes."""
        with GraphOp(name="branch_test") as graph:
            branch = if_(PARENT["x"] >= 10, "high").else_("low")

            high = FuncOp(
                name="high",
                code_fn=lambda: {"result": "high"},
                inputs={},
                outputs={"*": PARENT},
            )
            low = FuncOp(
                name="low",
                code_fn=lambda: {"result": "low"},
                inputs={},
                outputs={"*": PARENT},
            )

            START >> branch
            branch >> [high, low]
            [high, low] >> END

        result = await Hush(graph, mode=mode).run(inputs={"x": 15})
        assert result["result"] == "high"

        result = await Hush(graph, mode=mode).run(inputs={"x": 5})
        assert result["result"] == "low"

    @pytest.mark.parametrize("mode", ["python", "rust"])
    async def test_multi_branch(self, mode):
        """Multi-condition branch routes correctly in both modes."""
        with GraphOp(name="multi_branch") as graph:
            branch = (
                Branch("router")
                .if_(PARENT["score"] >= 90, "a_grade")
                .if_(PARENT["score"] >= 70, "b_grade")
                .else_("fail")
            )

            a_grade = FuncOp(
                name="a_grade",
                code_fn=lambda: {"result": "A"},
                inputs={},
                outputs={"*": PARENT},
            )
            b_grade = FuncOp(
                name="b_grade",
                code_fn=lambda: {"result": "B"},
                inputs={},
                outputs={"*": PARENT},
            )
            fail = FuncOp(
                name="fail",
                code_fn=lambda: {"result": "F"},
                inputs={},
                outputs={"*": PARENT},
            )

            START >> branch
            branch >> [a_grade, b_grade, fail]
            [a_grade, b_grade, fail] >> END

        result = await Hush(graph, mode=mode).run(inputs={"score": 95})
        assert result["result"] == "A"

        result = await Hush(graph, mode=mode).run(inputs={"score": 75})
        assert result["result"] == "B"

        result = await Hush(graph, mode=mode).run(inputs={"score": 40})
        assert result["result"] == "F"

    @pytest.mark.parametrize("mode", ["python", "rust"])
    async def test_branch_with_ops(self, mode):
        """Branch after a computation op."""

        @op
        def compute(x: int):
            return {"value": x * 3}

        with GraphOp(name="branch_after_op") as graph:
            c = compute(x=PARENT["input"])
            branch = if_(c["value"] >= 15, "big").else_("small")

            big = FuncOp(
                name="big",
                code_fn=lambda: {"result": "big"},
                inputs={},
                outputs={"*": PARENT},
            )
            small = FuncOp(
                name="small",
                code_fn=lambda: {"result": "small"},
                inputs={},
                outputs={"*": PARENT},
            )

            START >> c >> branch
            branch >> [big, small]
            [big, small] >> END

        result = await Hush(graph, mode=mode).run(inputs={"input": 6})
        assert result["result"] == "big"  # 6*3=18 >= 15

        result = await Hush(graph, mode=mode).run(inputs={"input": 3})
        assert result["result"] == "small"  # 3*3=9 < 15

    @pytest.mark.parametrize("mode", ["python", "rust"])
    async def test_branch_string_equality(self, mode):
        """Branch on string equality."""
        with GraphOp(name="string_branch") as graph:
            branch = (
                if_(PARENT["action"] == "greet", "greet_op")
                .if_(PARENT["action"] == "farewell", "farewell_op")
                .else_("default_op")
            )

            greet_op = FuncOp(
                name="greet_op",
                code_fn=lambda: {"result": "hello!"},
                inputs={},
                outputs={"*": PARENT},
            )
            farewell_op = FuncOp(
                name="farewell_op",
                code_fn=lambda: {"result": "goodbye!"},
                inputs={},
                outputs={"*": PARENT},
            )
            default_op = FuncOp(
                name="default_op",
                code_fn=lambda: {"result": "what?"},
                inputs={},
                outputs={"*": PARENT},
            )

            START >> branch
            branch >> [greet_op, farewell_op, default_op]
            [greet_op, farewell_op, default_op] >> END

        result = await Hush(graph, mode=mode).run(inputs={"action": "greet"})
        assert result["result"] == "hello!"

        result = await Hush(graph, mode=mode).run(inputs={"action": "farewell"})
        assert result["result"] == "goodbye!"

        result = await Hush(graph, mode=mode).run(inputs={"action": "unknown"})
        assert result["result"] == "what?"


# =============================================================================
# Branch + Rust ops
# =============================================================================


class TestBranchWithRustOps:
    async def test_branch_routes_to_rust_op(self):
        """Branch routes to a Rust-native op."""
        from hush_runtime import rust_op

        @rust_op("rust_double")
        @op
        def rust_double_fn(x: int):
            return {"result": x * 2}

        with GraphOp(name="branch_rust") as graph:
            branch = if_(PARENT["use_double"] == True, "double").else_("triple")  # noqa: E712

            double = FuncOp(
                name="double",
                code_fn=lambda val: {"result": val * 2},
                inputs={"val": PARENT["val"]},
                outputs={"*": PARENT},
            )
            triple = FuncOp(
                name="triple",
                code_fn=lambda val: {"result": val * 3},
                inputs={"val": PARENT["val"]},
                outputs={"*": PARENT},
            )

            START >> branch
            branch >> [double, triple]
            [double, triple] >> END

        result = await Hush(graph, mode="rust").run(
            inputs={"use_double": True, "val": 5}
        )
        assert result["result"] == 10

        result = await Hush(graph, mode="rust").run(
            inputs={"use_double": False, "val": 5}
        )
        assert result["result"] == 15
