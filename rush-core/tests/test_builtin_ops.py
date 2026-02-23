"""Tests for built-in Rust ops (string, json, math) via cdylib plugin.

Tests that:
- String ops: concat, split, template
- JSON ops: parse, extract, merge
- Math ops: sum, mean, max, min
- Built-in ops work inside GraphOp workflows
"""

import pytest

from hush.core import END, PARENT, START, GraphOp, Hush, op

from conftest import BUILTIN_CRATE


# =============================================================================
# String ops in workflows
# =============================================================================


class TestStringOps:
    async def test_concat(self):
        @op(rust=f"{BUILTIN_CRATE}::string_concat")
        def concat(parts: list):
            return {"result": "".join(parts)}

        with GraphOp(name="test") as graph:
            step = concat(parts=PARENT["parts"])
            START >> step >> END

        result = await Hush(graph, mode="rust").run(inputs={"parts": ["hello", " ", "world"]})
        assert result["result"] == "hello world"

    async def test_concat_empty(self):
        @op(rust=f"{BUILTIN_CRATE}::string_concat")
        def concat(parts: list):
            return {"result": "".join(parts)}

        with GraphOp(name="test") as graph:
            step = concat(parts=PARENT["parts"])
            START >> step >> END

        result = await Hush(graph, mode="rust").run(inputs={"parts": []})
        assert result["result"] == ""

    async def test_split(self):
        @op(rust=f"{BUILTIN_CRATE}::string_split")
        def split(text: str, delimiter: str):
            return {"parts": text.split(delimiter)}

        with GraphOp(name="test") as graph:
            step = split(text=PARENT["text"], delimiter=PARENT["delim"])
            START >> step >> END

        result = await Hush(graph, mode="rust").run(inputs={"text": "a,b,c", "delim": ","})
        assert result["parts"] == ["a", "b", "c"]

    async def test_split_no_match(self):
        @op(rust=f"{BUILTIN_CRATE}::string_split")
        def split(text: str, delimiter: str):
            return {"parts": text.split(delimiter)}

        with GraphOp(name="test") as graph:
            step = split(text=PARENT["text"], delimiter=PARENT["delim"])
            START >> step >> END

        result = await Hush(graph, mode="rust").run(inputs={"text": "hello", "delim": ","})
        assert result["parts"] == ["hello"]

    async def test_template(self):
        @op(rust=f"{BUILTIN_CRATE}::string_template")
        def template(template: str, vars: dict):
            result = template
            for k, v in vars.items():
                result = result.replace(f"{{{k}}}", str(v))
            return {"result": result}

        with GraphOp(name="test") as graph:
            step = template(template=PARENT["tpl"], vars=PARENT["vars"])
            START >> step >> END

        result = await Hush(graph, mode="rust").run(
            inputs={"tpl": "Hello {name}, you are {age}!", "vars": {"name": "Alice", "age": "30"}}
        )
        assert result["result"] == "Hello Alice, you are 30!"

    async def test_template_no_vars(self):
        @op(rust=f"{BUILTIN_CRATE}::string_template")
        def template(template: str, vars: dict):
            result = template
            for k, v in vars.items():
                result = result.replace(f"{{{k}}}", str(v))
            return {"result": result}

        with GraphOp(name="test") as graph:
            step = template(template=PARENT["tpl"], vars=PARENT["vars"])
            START >> step >> END

        result = await Hush(graph, mode="rust").run(
            inputs={"tpl": "no placeholders", "vars": {}}
        )
        assert result["result"] == "no placeholders"


# =============================================================================
# JSON ops in workflows
# =============================================================================


class TestJsonOps:
    async def test_parse(self):
        @op(rust=f"{BUILTIN_CRATE}::json_parse")
        def parse(text: str):
            import json
            return {"data": json.loads(text)}

        with GraphOp(name="test") as graph:
            step = parse(text=PARENT["text"])
            START >> step >> END

        result = await Hush(graph, mode="rust").run(
            inputs={"text": '{"key": "value", "num": 42}'}
        )
        assert result["data"] == {"key": "value", "num": 42}

    async def test_parse_list(self):
        @op(rust=f"{BUILTIN_CRATE}::json_parse")
        def parse(text: str):
            import json
            return {"data": json.loads(text)}

        with GraphOp(name="test") as graph:
            step = parse(text=PARENT["text"])
            START >> step >> END

        result = await Hush(graph, mode="rust").run(inputs={"text": "[1, 2, 3]"})
        assert result["data"] == [1, 2, 3]

    async def test_extract(self):
        @op(rust=f"{BUILTIN_CRATE}::json_extract")
        def extract(data: dict, path: str):
            current = data
            for key in path.split("."):
                current = current[key]
            return {"value": current}

        with GraphOp(name="test") as graph:
            step = extract(data=PARENT["data"], path=PARENT["path"])
            START >> step >> END

        result = await Hush(graph, mode="rust").run(
            inputs={"data": {"a": {"b": 1}}, "path": "a.b"}
        )
        assert result["value"] == 1

    async def test_merge(self):
        @op(rust=f"{BUILTIN_CRATE}::json_merge")
        def merge(a: dict, b: dict):
            result = {**a, **b}
            return {"result": result}

        with GraphOp(name="test") as graph:
            step = merge(a=PARENT["a"], b=PARENT["b"])
            START >> step >> END

        result = await Hush(graph, mode="rust").run(
            inputs={"a": {"x": 1, "y": 2}, "b": {"y": 3, "z": 4}}
        )
        assert result["result"] == {"x": 1, "y": 3, "z": 4}

    async def test_merge_empty(self):
        @op(rust=f"{BUILTIN_CRATE}::json_merge")
        def merge(a: dict, b: dict):
            result = {**a, **b}
            return {"result": result}

        with GraphOp(name="test") as graph:
            step = merge(a=PARENT["a"], b=PARENT["b"])
            START >> step >> END

        result = await Hush(graph, mode="rust").run(inputs={"a": {"x": 1}, "b": {}})
        assert result["result"] == {"x": 1}


# =============================================================================
# Math ops in workflows
# =============================================================================


class TestMathOps:
    async def test_sum(self):
        @op(rust=f"{BUILTIN_CRATE}::math_sum")
        def total(values: list):
            return {"result": sum(values)}

        with GraphOp(name="test") as graph:
            step = total(values=PARENT["values"])
            START >> step >> END

        result = await Hush(graph, mode="rust").run(inputs={"values": [1, 2, 3, 4]})
        assert result["result"] == 10.0

    async def test_sum_empty(self):
        @op(rust=f"{BUILTIN_CRATE}::math_sum")
        def total(values: list):
            return {"result": sum(values)}

        with GraphOp(name="test") as graph:
            step = total(values=PARENT["values"])
            START >> step >> END

        result = await Hush(graph, mode="rust").run(inputs={"values": []})
        assert result["result"] == 0.0

    async def test_mean(self):
        @op(rust=f"{BUILTIN_CRATE}::math_mean")
        def mean(values: list):
            return {"result": sum(values) / len(values) if values else 0.0}

        with GraphOp(name="test") as graph:
            step = mean(values=PARENT["values"])
            START >> step >> END

        result = await Hush(graph, mode="rust").run(inputs={"values": [2, 4, 6]})
        assert result["result"] == 4.0

    async def test_mean_empty(self):
        @op(rust=f"{BUILTIN_CRATE}::math_mean")
        def mean(values: list):
            return {"result": sum(values) / len(values) if values else 0.0}

        with GraphOp(name="test") as graph:
            step = mean(values=PARENT["values"])
            START >> step >> END

        result = await Hush(graph, mode="rust").run(inputs={"values": []})
        assert result["result"] == 0.0

    async def test_max(self):
        @op(rust=f"{BUILTIN_CRATE}::math_max")
        def maximum(values: list):
            return {"result": max(values)}

        with GraphOp(name="test") as graph:
            step = maximum(values=PARENT["values"])
            START >> step >> END

        result = await Hush(graph, mode="rust").run(inputs={"values": [3, 1, 4, 1, 5]})
        assert result["result"] == 5.0

    async def test_min(self):
        @op(rust=f"{BUILTIN_CRATE}::math_min")
        def minimum(values: list):
            return {"result": min(values)}

        with GraphOp(name="test") as graph:
            step = minimum(values=PARENT["values"])
            START >> step >> END

        result = await Hush(graph, mode="rust").run(inputs={"values": [3, 1, 4, 1, 5]})
        assert result["result"] == 1.0

    async def test_floats(self):
        @op(rust=f"{BUILTIN_CRATE}::math_sum")
        def total(values: list):
            return {"result": sum(values)}

        with GraphOp(name="test") as graph:
            step = total(values=PARENT["values"])
            START >> step >> END

        result = await Hush(graph, mode="rust").run(inputs={"values": [1.5, 2.5, 3.0]})
        assert result["result"] == 7.0


# =============================================================================
# Integration: built-in ops in workflows
# =============================================================================


class TestBuiltinOpsInWorkflow:
    async def test_string_concat_in_graph(self):
        @op(rust=f"{BUILTIN_CRATE}::string_concat")
        def concat(parts: list):
            return {"result": "".join(parts)}

        with GraphOp(name="test") as graph:
            step = concat(parts=PARENT["parts"])
            START >> step >> END

        result = await Hush(graph, mode="rust").run(inputs={"parts": ["a", "b", "c"]})
        assert result["result"] == "abc"

    async def test_math_chain_in_graph(self):
        @op(rust=f"{BUILTIN_CRATE}::math_sum")
        def total(values: list):
            return {"result": sum(values)}

        @op(rust=f"{BUILTIN_CRATE}::double")
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
        @op(rust=f"{BUILTIN_CRATE}::string_template")
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
