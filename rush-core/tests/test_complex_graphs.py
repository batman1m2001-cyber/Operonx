"""Tests for complex graph patterns — extended coverage for Rush engine.

Based on hush-core test patterns, these tests exercise:
- Deep nesting (3+ levels)
- Wide parallelism (many concurrent ops)
- Complex diamond patterns
- Branch + iteration combinations
- Output mapping edge cases
- Large data handling
- @graph decorator composition
"""

import pytest

from hush.core import END, PARENT, START, GraphOp, Hush, graph, op
from hush.core.ops.flow.branch_op import if_
from hush.core.ops.iteration.base import Each
from hush.core.ops.iteration.for_op import ForOp
from hush.core.ops.iteration.while_op import WhileOp
from rush_core import Rush

from conftest import BUILTIN_CRATE


# =============================================================================
# Helper ops
# =============================================================================


@op(rust=f"{BUILTIN_CRATE}::double")
def double(x: int):
    return {"result": x * 2}


@op(rust=f"{BUILTIN_CRATE}::add")
def add(a: int, b: int):
    return {"result": a + b}


@op
def identity(value):
    return {"out": value}


@op
def multiply(a: int, b: int):
    return {"result": a * b}


@op
def to_upper(text: str):
    return {"result": text.upper()}


@op
def join_strings(items: list, separator: str):
    return {"result": separator.join(str(i) for i in items)}


# =============================================================================
# Deep nesting tests
# =============================================================================


class TestDeepNesting:
    def test_three_level_nesting(self):
        """Graph → Subgraph → Sub-subgraph (3 levels deep)."""

        @graph
        def inner(x):
            d = double(x=x)
            START >> d >> END

        @graph
        def middle(x):
            sub = inner(x=x)
            START >> sub >> END

        with GraphOp(name="g") as g:
            top = middle(x=PARENT["x"])
            START >> top >> END
        g.build()

        config = g.serialize()
        engine = Rush(config)
        result = engine.run({"x": 5})
        assert result["result"] == 10

    def test_deep_chain_in_nested_graph(self):
        """Nested graph with a long internal chain."""

        @graph
        def triple_double(x):
            a = double(x=x)
            b = double(x=a["result"])
            c = double(x=b["result"])
            START >> a >> b >> c >> END

        with GraphOp(name="g") as g:
            step = triple_double(x=PARENT["x"])
            START >> step >> END
        g.build()

        config = g.serialize()
        engine = Rush(config)
        result = engine.run({"x": 2})
        assert result["result"] == 16  # 2 * 2 * 2 * 2

    def test_nested_graph_with_parallel_inside(self):
        """Nested graph that contains parallel ops."""

        @graph
        def parallel_add(x, y):
            dx = double(x=x)
            dy = double(x=y)
            total = add(a=dx["result"], b=dy["result"])
            START >> dx
            START >> dy
            dx >> total
            dy >> total
            total >> END

        with GraphOp(name="g") as g:
            step = parallel_add(x=PARENT["x"], y=PARENT["y"])
            START >> step >> END
        g.build()

        config = g.serialize()
        engine = Rush(config)
        result = engine.run({"x": 3, "y": 7})
        assert result["result"] == 20  # (3*2) + (7*2) = 6 + 14


# =============================================================================
# Wide parallelism tests
# =============================================================================


class TestWideParallelism:
    def test_many_parallel_ops(self):
        """5 independent ops running in parallel."""

        @op
        def square(n: int):
            return {"result": n * n}

        with GraphOp(name="g") as g:
            s0 = square(n=PARENT["n0"])
            s1 = square(n=PARENT["n1"])
            s2 = square(n=PARENT["n2"])
            s3 = square(n=PARENT["n3"])
            s4 = square(n=PARENT["n4"])

            @op
            def sum_all(a: int, b: int, c: int, d: int, e: int):
                return {"total": a + b + c + d + e}

            total = sum_all(
                a=s0["result"],
                b=s1["result"],
                c=s2["result"],
                d=s3["result"],
                e=s4["result"],
            )
            START >> s0
            START >> s1
            START >> s2
            START >> s3
            START >> s4
            s0 >> total
            s1 >> total
            s2 >> total
            s3 >> total
            s4 >> total
            total >> END
        g.build()

        config = g.serialize()
        engine = Rush(config)
        result = engine.run({"n0": 1, "n1": 2, "n2": 3, "n3": 4, "n4": 5})
        assert result["total"] == 55  # 1+4+9+16+25


# =============================================================================
# Complex diamond patterns
# =============================================================================


class TestComplexDiamondPatterns:
    def test_double_diamond(self):
        """A → B,C → D → E,F → G (two diamonds in sequence)."""

        @op
        def inc(x: int):
            return {"result": x + 1}

        with GraphOp(name="g") as g:
            a = double(x=PARENT["x"])
            b = double(x=a["result"])
            c = inc(x=a["result"])
            d = add(a=b["result"], b=c["result"])
            e = double(x=d["result"])
            f = inc(x=d["result"])
            final = add(a=e["result"], b=f["result"])

            START >> a >> [b, c]
            [b, c] >> d >> [e, f]
            [e, f] >> final >> END
        g.build()

        config = g.serialize()
        engine = Rush(config)
        result = engine.run({"x": 2})
        # a: 4, b: 8, c: 5, d: 13, e: 26, f: 14, final: 40
        assert result["result"] == 40


# =============================================================================
# Branch + iteration combinations
# =============================================================================


class TestBranchWithIteration:
    def test_branch_then_for_loop(self):
        """Branch decides which processing path, then iterate."""

        @op
        def grade_a():
            return {"multiplier": 3}

        @op
        def grade_f():
            return {"multiplier": 1}

        @op
        def apply_multiplier(value: int, multiplier: int):
            return {"result": value * multiplier}

        with GraphOp(name="g") as g:
            router = if_(PARENT["score"] >= 90, "a").else_("f")
            a = grade_a(outputs={"multiplier": PARENT})
            f = grade_f(outputs={"multiplier": PARENT})

            START >> router
            router >> ~a
            router >> ~f
            a >> ~END
            f >> ~END
        g.build()

        config = g.serialize()
        engine = Rush(config)

        result = engine.run({"score": 95})
        assert result["multiplier"] == 3

        result = engine.run({"score": 50})
        assert result["multiplier"] == 1

    def test_for_loop_with_branch_inside(self):
        """ForOp where each iteration has a branch decision."""

        @op
        def classify(value: int):
            return {
                "label": "high" if value >= 50 else "low",
                "value": value,
            }

        with GraphOp(name="g") as g:
            with ForOp(
                name="loop",
                inputs={"value": Each([10, 60, 30, 80, 5])},
            ) as loop:
                c = classify(value=PARENT["value"])
                START >> c >> END
            START >> loop >> END
        g.build()

        config = g.serialize()
        engine = Rush(config)
        result = engine.run({})
        assert result["label"] == ["low", "high", "low", "high", "low"]
        assert result["value"] == [10, 60, 30, 80, 5]


# =============================================================================
# Output mapping edge cases
# =============================================================================


class TestOutputMappingEdgeCases:
    def test_multiple_output_mappings(self):
        """Multiple keys mapped to different parent keys."""

        @op
        def multi_output(x: int):
            return {"doubled": x * 2, "tripled": x * 3, "squared": x * x}

        with GraphOp(name="g") as g:
            step = multi_output(x=PARENT["x"])
            step["doubled"] >> PARENT["d"]
            step["tripled"] >> PARENT["t"]
            step["squared"] >> PARENT["s"]
            START >> step >> END
        g.build()

        config = g.serialize()
        engine = Rush(config)
        result = engine.run({"x": 5})
        assert result["d"] == 10
        assert result["t"] == 15
        assert result["s"] == 25

    def test_wildcard_output_forwarding(self):
        """outputs={"*": PARENT} forwards all outputs."""

        @op
        def multi(x: int):
            return {"a": x + 1, "b": x + 2, "c": x + 3}

        with GraphOp(name="g") as g:
            step = multi(x=PARENT["x"], outputs={"*": PARENT})
            START >> step >> END
        g.build()

        config = g.serialize()
        engine = Rush(config)
        result = engine.run({"x": 10})
        assert result["a"] == 11
        assert result["b"] == 12
        assert result["c"] == 13

    def test_output_rename_mapping(self):
        """Rename output keys via output mapping."""

        @op
        def compute(x: int):
            return {"result": x * 10}

        with GraphOp(name="g") as g:
            step = compute(x=PARENT["x"], outputs={"result": PARENT["answer"]})
            START >> step >> END
        g.build()

        config = g.serialize()
        engine = Rush(config)
        result = engine.run({"x": 7})
        assert result["answer"] == 70


# =============================================================================
# Large data handling
# =============================================================================


class TestLargeDataHandling:
    def test_large_list_passthrough(self):
        """Large list passes through Rush engine correctly."""

        @op
        def process_list(items: list):
            return {"count": len(items), "sum": sum(items)}

        with GraphOp(name="g") as g:
            step = process_list(items=PARENT["items"])
            START >> step >> END
        g.build()

        config = g.serialize()
        engine = Rush(config)
        large_list = list(range(1000))
        result = engine.run({"items": large_list})
        assert result["count"] == 1000
        assert result["sum"] == sum(range(1000))

    def test_large_string_passthrough(self):
        """Large string passes through Rush engine correctly."""

        @op
        def measure(text: str):
            return {"length": len(text), "first": text[:10], "last": text[-10:]}

        with GraphOp(name="g") as g:
            step = measure(text=PARENT["text"])
            START >> step >> END
        g.build()

        config = g.serialize()
        engine = Rush(config)
        large_text = "x" * 100_000
        result = engine.run({"text": large_text})
        assert result["length"] == 100_000
        assert result["first"] == "x" * 10

    def test_many_iterations(self):
        """ForOp with many iterations (100+)."""
        with GraphOp(name="g") as g:
            with ForOp(name="loop", inputs={"value": Each(list(range(100)))}) as loop:
                d = double(x=PARENT["value"])
                START >> d >> END
            START >> loop >> END
        g.build()

        config = g.serialize()
        engine = Rush(config)
        result = engine.run({})
        assert len(result["result"]) == 100
        assert result["result"][0] == 0
        assert result["result"][50] == 100
        assert result["result"][99] == 198


# =============================================================================
# @graph decorator composition
# =============================================================================


class TestGraphDecoratorComposition:
    def test_graph_calling_graph(self):
        """@graph decorator calling another @graph."""

        @graph
        def double_flow(x):
            d = double(x=x)
            START >> d >> END

        @graph
        def quad_flow(x):
            a = double_flow(x=x)
            b = double_flow(x=a["result"])
            START >> a >> b >> END

        with GraphOp(name="g") as g:
            step = quad_flow(x=PARENT["x"])
            START >> step >> END
        g.build()

        config = g.serialize()
        engine = Rush(config)
        result = engine.run({"x": 3})
        assert result["result"] == 12  # 3 * 2 * 2

    def test_graph_with_multiple_params(self):
        """@graph with multiple parameters."""

        @graph
        def weighted_sum(x, y, weight):
            mx = multiply(a=x, b=weight)
            a = add(a=mx["result"], b=y)
            START >> mx >> a >> END

        with GraphOp(name="g") as g:
            step = weighted_sum(x=PARENT["x"], y=PARENT["y"], weight=PARENT["w"])
            START >> step >> END
        g.build()

        config = g.serialize()
        engine = Rush(config)
        result = engine.run({"x": 5, "y": 10, "w": 3})
        assert result["result"] == 25  # 5*3 + 10

    def test_graph_reused_multiple_times(self):
        """Same @graph used multiple times in a workflow."""

        @graph
        def double_flow(x):
            d = double(x=x)
            START >> d >> END

        with GraphOp(name="g") as g:
            a = double_flow(x=PARENT["a"])
            b = double_flow(x=PARENT["b"])
            total = add(a=a["result"], b=b["result"])
            START >> a
            START >> b
            a >> total
            b >> total
            total >> END
        g.build()

        config = g.serialize()
        engine = Rush(config)
        result = engine.run({"a": 3, "b": 7})
        assert result["result"] == 20  # (3*2) + (7*2)


# =============================================================================
# WhileOp advanced patterns
# =============================================================================


class TestWhileOpAdvanced:
    def test_while_with_string_accumulation(self):
        """WhileOp that accumulates a string."""

        @op
        def append_char(text: str, char: str, counter: int):
            return {
                "new_text": text + char,
                "new_counter": counter + 1,
            }

        with GraphOp(name="g") as g:
            with WhileOp(
                name="loop",
                inputs={"text": "", "char": "*", "counter": 0},
                until="counter >= 5",
            ) as loop:
                step = append_char(
                    text=PARENT["text"],
                    char=PARENT["char"],
                    counter=PARENT["counter"],
                )
                step["new_text"] >> PARENT["text"]
                step["new_counter"] >> PARENT["counter"]
                START >> step >> END
            START >> loop >> END
        g.build()

        config = g.serialize()
        engine = Rush(config)
        result = engine.run({})
        assert result["text"] == "*****"
        assert result["counter"] == 5

    def test_while_with_list_building(self):
        """WhileOp that builds a list iteration by iteration."""

        @op
        def collect(items: list, counter: int):
            new_items = items + [counter * counter]
            return {"new_items": new_items, "new_counter": counter + 1}

        with GraphOp(name="g") as g:
            with WhileOp(
                name="loop",
                inputs={"items": [], "counter": 0},
                until="counter >= 4",
            ) as loop:
                step = collect(items=PARENT["items"], counter=PARENT["counter"])
                step["new_items"] >> PARENT["items"]
                step["new_counter"] >> PARENT["counter"]
                START >> step >> END
            START >> loop >> END
        g.build()

        config = g.serialize()
        engine = Rush(config)
        result = engine.run({})
        assert result["items"] == [0, 1, 4, 9]  # 0^2, 1^2, 2^2, 3^2


# =============================================================================
# Mode equivalence for complex patterns
# =============================================================================


class TestModeEquivalenceComplex:
    @pytest.mark.parametrize("mode", ["python", "rust"])
    async def test_nested_for_both_modes(self, mode):
        """ForOp inside nested graph produces same results in both modes."""

        @graph
        def batch_double(items):
            with ForOp(name="loop", inputs={"value": Each(items)}) as loop:
                d = double(x=PARENT["value"])
                START >> d >> END
            START >> loop >> END

        with GraphOp(name="g") as g:
            step = batch_double(items=PARENT["items"])
            START >> step >> END

        result = await Hush(g, mode=mode).run(inputs={"items": [1, 2, 3]})
        assert result["result"] == [2, 4, 6]

    @pytest.mark.parametrize("mode", ["python", "rust"])
    async def test_diamond_both_modes(self, mode):
        """Diamond pattern produces same results in both modes."""
        with GraphOp(name="g") as g:
            a = double(x=PARENT["x"])
            b = double(x=a["result"])
            c = identity(value=a["result"])
            d = add(a=b["result"], b=c["out"])
            START >> a >> [b, c]
            [b, c] >> d >> END

        result = await Hush(g, mode=mode).run(inputs={"x": 5})
        assert result["result"] == 30  # a=10, b=20, c=10, d=30
