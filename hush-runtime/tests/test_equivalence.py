"""Cross-mode correctness tests — Python vs Rust produce identical results.

Each test builds a graph and runs it in both modes via @pytest.mark.parametrize.
This verifies that RustMemoryState is a drop-in replacement for MemoryState.
"""

import pytest

from hush.core import END, PARENT, START, Each, ForOp, GraphOp, Hush, MapOp, graph, op


# =============================================================================
# Shared ops
# =============================================================================


@op
def double(x: int):
    return {"result": x * 2}


@op
def add(a: int, b: int):
    return {"result": a + b}


@op
def identity(x):
    return {"result": x}


# =============================================================================
# Linear workflows
# =============================================================================


class TestLinearWorkflow:
    @pytest.mark.parametrize("mode", ["python", "rust"])
    async def test_single_op(self, mode):
        with GraphOp(name="single") as graph:
            step = double(x=PARENT["x"])
            START >> step >> END

        result = await Hush(graph, mode=mode).run(inputs={"x": 5})
        assert result["result"] == 10

    @pytest.mark.parametrize("mode", ["python", "rust"])
    async def test_two_op_chain(self, mode):
        with GraphOp(name="chain") as graph:
            a = double(x=PARENT["input"])
            b = add(a=a["result"], b=PARENT["y"])
            START >> a >> b >> END

        result = await Hush(graph, mode=mode).run(inputs={"input": 5, "y": 3})
        assert result["result"] == 13

    @pytest.mark.parametrize("mode", ["python", "rust"])
    async def test_three_op_chain(self, mode):
        with GraphOp(name="triple") as graph:
            a = double(x=PARENT["x"])
            b = double(x=a["result"])
            c = double(x=b["result"])
            START >> a >> b >> c >> END

        result = await Hush(graph, mode=mode).run(inputs={"x": 2})
        assert result["result"] == 16  # 2*2=4, 4*2=8, 8*2=16


# =============================================================================
# Parallel workflows
# =============================================================================


class TestParallelWorkflow:
    @pytest.mark.parametrize("mode", ["python", "rust"])
    async def test_fork_join(self, mode):
        with GraphOp(name="fork_join") as graph:
            a = double(x=PARENT["x"])
            b = double(x=PARENT["x"])
            c = add(a=a["result"], b=b["result"])
            START >> a >> c
            START >> b >> c
            c >> END

        result = await Hush(graph, mode=mode).run(inputs={"x": 3})
        assert result["result"] == 12  # 3*2 + 3*2 = 12


# =============================================================================
# Iteration workflows
# =============================================================================


class TestIterationWorkflow:
    @pytest.mark.parametrize("mode", ["python", "rust"])
    async def test_map_op(self, mode):
        @op
        def process(value: int):
            return {"out": value * 3}

        with GraphOp(name="map_wf") as graph:
            with MapOp(name="loop", inputs={"value": Each(PARENT["items"])}) as loop:
                node = process(value=PARENT["value"])
                node["out"] >> PARENT["out"]
                START >> node >> END
            START >> loop >> END

        result = await Hush(graph, mode=mode).run(inputs={"items": [1, 2, 3, 4]})
        assert sorted(result["out"]) == [3, 6, 9, 12]

    @pytest.mark.parametrize("mode", ["python", "rust"])
    async def test_for_op(self, mode):
        @op
        def square(value: int):
            return {"out": value ** 2}

        with GraphOp(name="for_wf") as graph:
            with ForOp(name="loop", inputs={"value": Each(PARENT["items"])}) as loop:
                node = square(value=PARENT["value"])
                node["out"] >> PARENT["out"]
                START >> node >> END
            START >> loop >> END

        result = await Hush(graph, mode=mode).run(inputs={"items": [1, 2, 3]})
        assert result["out"] == [1, 4, 9]


# =============================================================================
# Various data types
# =============================================================================


class TestDataTypes:
    @pytest.mark.parametrize("mode", ["python", "rust"])
    async def test_string_passthrough(self, mode):
        with GraphOp(name="str_wf") as graph:
            step = identity(x=PARENT["text"])
            START >> step >> END

        result = await Hush(graph, mode=mode).run(inputs={"text": "hello world"})
        assert result["result"] == "hello world"

    @pytest.mark.parametrize("mode", ["python", "rust"])
    async def test_dict_passthrough(self, mode):
        with GraphOp(name="dict_wf") as graph:
            step = identity(x=PARENT["data"])
            START >> step >> END

        data = {"key": "value", "nested": {"a": 1}}
        result = await Hush(graph, mode=mode).run(inputs={"data": data})
        assert result["result"] == data

    @pytest.mark.parametrize("mode", ["python", "rust"])
    async def test_list_passthrough(self, mode):
        with GraphOp(name="list_wf") as graph:
            step = identity(x=PARENT["items"])
            START >> step >> END

        items = [1, "two", 3.0, None]
        result = await Hush(graph, mode=mode).run(inputs={"items": items})
        assert result["result"] == items


# =============================================================================
# Nested graphs
# =============================================================================


class TestNestedGraph:
    @pytest.mark.parametrize("mode", ["python", "rust"])
    async def test_nested_graph(self, mode):
        @graph
        def inner(x):
            step = double(x=x)
            START >> step >> END

        with GraphOp(name="outer") as outer:
            sub = inner(x=PARENT["input"])
            final = double(x=sub["result"])
            START >> sub >> final >> END

        result = await Hush(outer, mode=mode).run(inputs={"input": 5})
        assert result["result"] == 20  # 5*2=10, 10*2=20


# =============================================================================
# Cross-mode comparison — run both modes and compare
# =============================================================================


class TestCrossModeEquivalence:
    async def test_linear_produces_identical_output(self):
        """Same graph produces byte-identical outputs in both modes."""
        with GraphOp(name="linear") as graph:
            a = double(x=PARENT["input"])
            b = add(a=a["result"], b=PARENT["y"])
            START >> a >> b >> END

        py_result = await Hush(graph, mode="python").run(
            inputs={"input": 7, "y": 3},
            user_id="u1",
            session_id="s1",
            request_id="r1",
        )
        rs_result = await Hush(graph, mode="rust").run(
            inputs={"input": 7, "y": 3},
            user_id="u1",
            session_id="s1",
            request_id="r1",
        )

        # Compare all output keys (except $state which differs by type)
        for key in py_result:
            if key == "$state":
                continue
            assert py_result[key] == rs_result[key], f"Mismatch on key {key!r}"

    async def test_iteration_produces_identical_output(self):
        """ForOp produces identical ordered results in both modes."""

        @op
        def triple(value: int):
            return {"out": value * 3}

        with GraphOp(name="iter_eq") as graph:
            with ForOp(name="loop", inputs={"value": Each(PARENT["nums"])}) as loop:
                node = triple(value=PARENT["value"])
                node["out"] >> PARENT["out"]
                START >> node >> END
            START >> loop >> END

        py_result = await Hush(graph, mode="python").run(inputs={"nums": [2, 4, 6]})
        rs_result = await Hush(graph, mode="rust").run(inputs={"nums": [2, 4, 6]})

        assert py_result["out"] == rs_result["out"] == [6, 12, 18]
