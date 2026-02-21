"""Tests for Phase 4 built-in Rust ops (string, json, math).

Tests that:
- All built-in ops are registered
- String ops: concat, split, template
- JSON ops: parse, extract, merge
- Math ops: sum, mean, max, min
- Built-in ops work inside GraphOp workflows
"""

import pytest

from hush.core import END, PARENT, START, GraphOp, Hush, op
from rush_core import RustFuncRegistry, rust_op


# =============================================================================
# Registry — all Phase 4 ops registered
# =============================================================================


class TestPhase4Registry:
    @pytest.mark.parametrize(
        "name",
        [
            "rust_string_concat",
            "rust_string_split",
            "rust_string_template",
            "rust_json_parse",
            "rust_json_extract",
            "rust_json_merge",
            "rust_math_sum",
            "rust_math_mean",
            "rust_math_max",
            "rust_math_min",
        ],
    )
    def test_builtin_registered(self, name):
        assert RustFuncRegistry.has(name)


# =============================================================================
# String ops
# =============================================================================


class TestStringOps:
    def test_concat(self):
        result = RustFuncRegistry.execute(
            "rust_string_concat", {"parts": ["hello", " ", "world"]}
        )
        assert result["result"] == "hello world"

    def test_concat_empty(self):
        result = RustFuncRegistry.execute("rust_string_concat", {"parts": []})
        assert result["result"] == ""

    def test_split(self):
        result = RustFuncRegistry.execute(
            "rust_string_split", {"text": "a,b,c", "delimiter": ","}
        )
        assert result["parts"] == ["a", "b", "c"]

    def test_split_no_match(self):
        result = RustFuncRegistry.execute(
            "rust_string_split", {"text": "hello", "delimiter": ","}
        )
        assert result["parts"] == ["hello"]

    def test_template(self):
        result = RustFuncRegistry.execute(
            "rust_string_template",
            {"template": "Hello {name}, you are {age}!", "vars": {"name": "Alice", "age": "30"}},
        )
        assert result["result"] == "Hello Alice, you are 30!"

    def test_template_no_vars(self):
        result = RustFuncRegistry.execute(
            "rust_string_template", {"template": "no placeholders", "vars": {}}
        )
        assert result["result"] == "no placeholders"


# =============================================================================
# JSON ops
# =============================================================================


class TestJsonOps:
    def test_parse(self):
        result = RustFuncRegistry.execute(
            "rust_json_parse", {"text": '{"key": "value", "num": 42}'}
        )
        assert result["data"] == {"key": "value", "num": 42}

    def test_parse_list(self):
        result = RustFuncRegistry.execute("rust_json_parse", {"text": "[1, 2, 3]"})
        assert result["data"] == [1, 2, 3]

    def test_extract_simple(self):
        result = RustFuncRegistry.execute(
            "rust_json_extract", {"data": {"a": {"b": 1}}, "path": "a.b"}
        )
        assert result["value"] == 1

    def test_extract_top_level(self):
        result = RustFuncRegistry.execute(
            "rust_json_extract", {"data": {"key": "val"}, "path": "key"}
        )
        assert result["value"] == "val"

    def test_merge(self):
        result = RustFuncRegistry.execute(
            "rust_json_merge", {"a": {"x": 1, "y": 2}, "b": {"y": 3, "z": 4}}
        )
        assert result["result"] == {"x": 1, "y": 3, "z": 4}

    def test_merge_empty(self):
        result = RustFuncRegistry.execute(
            "rust_json_merge", {"a": {"x": 1}, "b": {}}
        )
        assert result["result"] == {"x": 1}


# =============================================================================
# Math ops
# =============================================================================


class TestMathOps:
    def test_sum(self):
        result = RustFuncRegistry.execute("rust_math_sum", {"values": [1, 2, 3, 4]})
        assert result["result"] == 10.0

    def test_sum_empty(self):
        result = RustFuncRegistry.execute("rust_math_sum", {"values": []})
        assert result["result"] == 0.0

    def test_mean(self):
        result = RustFuncRegistry.execute("rust_math_mean", {"values": [2, 4, 6]})
        assert result["result"] == 4.0

    def test_mean_empty(self):
        result = RustFuncRegistry.execute("rust_math_mean", {"values": []})
        assert result["result"] == 0.0

    def test_max(self):
        result = RustFuncRegistry.execute("rust_math_max", {"values": [3, 1, 4, 1, 5]})
        assert result["result"] == 5.0

    def test_min(self):
        result = RustFuncRegistry.execute("rust_math_min", {"values": [3, 1, 4, 1, 5]})
        assert result["result"] == 1.0

    def test_floats(self):
        result = RustFuncRegistry.execute("rust_math_sum", {"values": [1.5, 2.5, 3.0]})
        assert result["result"] == 7.0


# =============================================================================
# Integration: built-in ops in workflows
# =============================================================================


class TestBuiltinOpsInWorkflow:
    async def test_string_concat_in_graph(self):
        @rust_op("rust_string_concat")
        @op
        def concat(parts: list):
            return {"result": "".join(parts)}

        with GraphOp(name="test") as graph:
            step = concat(parts=PARENT["parts"])
            START >> step >> END

        result = await Hush(graph, mode="rust").run(inputs={"parts": ["a", "b", "c"]})
        assert result["result"] == "abc"

    async def test_math_chain_in_graph(self):
        @rust_op("rust_math_sum")
        @op
        def total(values: list):
            return {"result": sum(values)}

        @rust_op("rust_double")
        @op
        def double(x: int):
            return {"result": x * 2}

        with GraphOp(name="chain") as graph:
            s = total(values=PARENT["nums"])
            d = double(x=s["result"])
            START >> s >> d >> END

        result = await Hush(graph, mode="rust").run(inputs={"nums": [1, 2, 3]})
        assert result["result"] == 12.0  # sum=6, double=12

    @pytest.mark.parametrize("mode", ["python", "rust"])
    async def test_builtin_both_modes(self, mode):
        @rust_op("rust_string_template")
        @op
        def greet(template: str, vars: dict):
            result = template
            for k, v in vars.items():
                result = result.replace(f"{{{k}}}", str(v))
            return {"result": result}

        with GraphOp(name="both") as graph:
            step = greet(template=PARENT["tpl"], vars=PARENT["vars"])
            START >> step >> END

        result = await Hush(graph, mode=mode).run(
            inputs={"tpl": "Hi {name}!", "vars": {"name": "Bob"}}
        )
        assert result["result"] == "Hi Bob!"
