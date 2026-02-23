"""Equivalence tests — verify Python and Rust modes produce identical results.

Each test runs the same workflow through Hush(graph, mode="python") and
Hush(graph, mode="rust"), asserting that outputs match exactly.
"""

import asyncio


from hush.core import END, PARENT, START, GraphOp, Hush, graph, op
from hush.core.ops.flow.branch_op import Branch
from hush.core.ops.iteration.base import Each
from hush.core.ops.iteration.for_op import ForOp
from hush.core.ops.iteration.while_op import WhileOp


def run_both(graph_op, inputs=None):
    """Run a graph in both modes and return (python_result, rust_result).

    Filters out $state (Python-only) and None values (from unexecuted branch paths).
    """
    inputs = inputs or {}
    py_engine = Hush(graph_op, mode="python")
    rs_engine = Hush(graph_op, mode="rust")
    py_result = asyncio.run(py_engine.run(inputs))
    rs_result = asyncio.run(rs_engine.run(inputs))
    # Both modes include $state — filter it for equivalence comparison
    py_result.pop("$state", None)
    rs_result.pop("$state", None)
    # Filter None values (unexecuted branch paths leave None outputs)
    py_result = {k: v for k, v in py_result.items() if v is not None}
    rs_result = {k: v for k, v in rs_result.items() if v is not None}
    return py_result, rs_result


# ── Ops used across tests ───────────────────────────────────────────


@op
def double(x: int):
    return {"result": x * 2}


@op
def add(a: int, b: int):
    return {"result": a + b}


@op
def identity(value):
    return {"value": value}


@op
def increment(counter: int):
    return {"new_counter": counter + 1}


@op
def accumulate(total: int, step: int):
    return {"new_total": total + step}


@op
def multiply(value: int, factor: int):
    return {"result": value * factor}


@op
def greet(name: str):
    return {"greeting": f"Hello, {name}!"}


# ── Linear chain tests ──────────────────────────────────────────────


class TestLinearChain:
    def test_single_op(self):
        with GraphOp(name="g") as g:
            d = double(x=PARENT["x"])
            START >> d >> END
        py, rs = run_both(g, {"x": 21})
        assert py == rs == {"result": 42}

    def test_two_op_chain(self):
        with GraphOp(name="g") as g:
            a = double(x=PARENT["x"])
            b = double(x=a["result"])
            START >> a >> b >> END
        py, rs = run_both(g, {"x": 5})
        assert py == rs == {"result": 20}

    def test_three_op_chain(self):
        with GraphOp(name="g") as g:
            a = double(x=PARENT["x"])
            b = double(x=a["result"])
            c = double(x=b["result"])
            START >> a >> b >> c >> END
        py, rs = run_both(g, {"x": 3})
        assert py == rs == {"result": 24}

    def test_string_data(self):
        with GraphOp(name="g") as g:
            g_op = greet(name=PARENT["name"])
            START >> g_op >> END
        py, rs = run_both(g, {"name": "World"})
        assert py == rs == {"greeting": "Hello, World!"}


# ── Fork-join (diamond) tests ───────────────────────────────────────


class TestForkJoin:
    def test_diamond_graph(self):
        with GraphOp(name="g") as g:
            a = double(x=PARENT["x"])
            b = double(x=a["result"])
            c = identity(value=a["result"])
            d = add(a=b["result"], b=c["value"])
            START >> a >> [b, c]
            [b, c] >> d >> END
        py, rs = run_both(g, {"x": 5})
        assert py == rs == {"result": 30}


# ── Branch tests ─────────────────────────────────────────────────────


class TestBranch:
    def test_branch_true_path(self):
        with GraphOp(name="g") as g:
            router = Branch("router").if_(PARENT["score"] >= 90, "high").else_("low")
            high = double(x=PARENT["score"])
            low = identity(value=PARENT["score"])
            START >> router
            router >> ~high >> END
            router >> ~low >> END
        py, rs = run_both(g, {"score": 95})
        assert py == rs == {"result": 190}

    def test_branch_false_path(self):
        with GraphOp(name="g") as g:
            router = Branch("router").if_(PARENT["score"] >= 90, "high").else_("low")
            high = double(x=PARENT["score"])
            low = identity(value=PARENT["score"])
            START >> router
            router >> ~high >> END
            router >> ~low >> END
        py, rs = run_both(g, {"score": 50})
        assert py == rs == {"value": 50}

    def test_multi_condition_branch(self):
        with GraphOp(name="g") as g:
            router = (
                Branch("router")
                .if_(PARENT["score"] >= 90, "a")
                .if_(PARENT["score"] >= 70, "b")
                .else_("c")
            )
            a = multiply(value=PARENT["score"], factor=3)
            b = multiply(value=PARENT["score"], factor=2)
            c = identity(value=PARENT["score"])
            START >> router
            router >> ~a >> END
            router >> ~b >> END
            router >> ~c >> END
        # Test middle case (score=80 → b path)
        py, rs = run_both(g, {"score": 80})
        assert py == rs == {"result": 160}


# ── Nested GraphOp tests ────────────────────────────────────────────


class TestNestedGraph:
    def test_simple_nested(self):
        @graph
        def inner(x):
            d = double(x=x)
            START >> d >> END

        with GraphOp(name="g") as g:
            sub = inner(x=PARENT["x"])
            START >> sub >> END
        py, rs = run_both(g, {"x": 7})
        assert py == rs == {"result": 14}

    def test_chained_nested(self):
        @graph
        def step(x):
            d = double(x=x)
            START >> d >> END

        with GraphOp(name="g") as g:
            a = step(x=PARENT["x"])
            b = step(x=a["result"])
            START >> a >> b >> END
        py, rs = run_both(g, {"x": 3})
        assert py == rs == {"result": 12}

    def test_nested_with_output_mapping(self):
        @graph
        def inner(x):
            d = double(x=x)
            d["result"] >> PARENT["doubled"]
            START >> d >> END

        with GraphOp(name="g") as g:
            sub = inner(x=PARENT["x"])
            START >> sub >> END
        py, rs = run_both(g, {"x": 8})
        assert py == rs == {"doubled": 16}


# ── ForOp tests ──────────────────────────────────────────────────────


class TestForOp:
    def test_simple_each(self):
        with GraphOp(name="g") as g:
            with ForOp(name="loop", inputs={"value": Each([1, 2, 3])}) as loop:
                d = double(x=PARENT["value"])
                START >> d >> END
            START >> loop >> END
        py, rs = run_both(g)
        assert py["result"] == rs["result"] == [2, 4, 6]

    def test_broadcast(self):
        with GraphOp(name="g") as g:
            with ForOp(
                name="loop",
                inputs={"value": Each([1, 2, 3]), "factor": 10},
            ) as loop:
                m = multiply(value=PARENT["value"], factor=PARENT["factor"])
                START >> m >> END
            START >> loop >> END
        py, rs = run_both(g)
        assert py["result"] == rs["result"] == [10, 20, 30]

    def test_multiple_each_zip(self):
        with GraphOp(name="g") as g:
            with ForOp(
                name="loop",
                inputs={"a": Each([1, 2, 3]), "b": Each([10, 20, 30])},
            ) as loop:
                s = add(a=PARENT["a"], b=PARENT["b"])
                START >> s >> END
            START >> loop >> END
        py, rs = run_both(g)
        assert py["result"] == rs["result"] == [11, 22, 33]

    def test_empty_list(self):
        with GraphOp(name="g") as g:
            with ForOp(name="loop", inputs={"value": Each([])}) as loop:
                d = double(x=PARENT["value"])
                START >> d >> END
            START >> loop >> END
        py, rs = run_both(g)
        assert py["result"] == rs["result"] == []

    def test_for_with_upstream_ref(self):
        with GraphOp(name="g") as g:
            src = identity(value=PARENT["items"])
            with ForOp(name="loop", inputs={"value": Each(src["value"])}) as loop:
                d = double(x=PARENT["value"])
                START >> d >> END
            START >> src >> loop >> END
        py, rs = run_both(g, {"items": [10, 20, 30]})
        assert py["result"] == rs["result"] == [20, 40, 60]


# ── WhileOp tests ────────────────────────────────────────────────────


class TestWhileOp:
    def test_simple_counter(self):
        with GraphOp(name="g") as g:
            with WhileOp(
                name="loop", inputs={"counter": 0}, until="counter >= 5"
            ) as loop:
                step = increment(counter=PARENT["counter"])
                step["new_counter"] >> PARENT["counter"]
                START >> step >> END
            START >> loop >> END
        py, rs = run_both(g)
        assert py["counter"] == rs["counter"] == 5

    def test_max_iterations_safety(self):
        with GraphOp(name="g") as g:
            with WhileOp(name="loop", inputs={"counter": 0}, max_iterations=5) as loop:
                step = increment(counter=PARENT["counter"])
                step["new_counter"] >> PARENT["counter"]
                START >> step >> END
            START >> loop >> END
        py, rs = run_both(g)
        assert py["counter"] == rs["counter"] == 5

    def test_while_accumulator(self):
        with GraphOp(name="g") as g:
            with WhileOp(
                name="loop",
                inputs={"total": 0, "step": 15},
                until="total >= 100",
            ) as loop:
                acc = accumulate(total=PARENT["total"], step=PARENT["step"])
                acc["new_total"] >> PARENT["total"]
                START >> acc >> END
            START >> loop >> END
        py, rs = run_both(g)
        assert py["total"] == rs["total"] == 105

    def test_while_with_upstream_ref(self):
        with GraphOp(name="g") as g:
            src = identity(value=PARENT["start"])
            with WhileOp(
                name="loop", inputs={"counter": src["value"]}, until="counter >= 10"
            ) as loop:
                step = increment(counter=PARENT["counter"])
                step["new_counter"] >> PARENT["counter"]
                START >> step >> END
            START >> src >> loop >> END
        py, rs = run_both(g, {"start": 7})
        assert py["counter"] == rs["counter"] == 10


# ── @graph decorator tests ───────────────────────────────────────────


class TestGraphDecorator:
    def test_graph_decorator(self):
        @graph
        def double_graph(x):
            d = double(x=x)
            START >> d >> END

        with GraphOp(name="g") as g:
            dg = double_graph(x=PARENT["x"])
            START >> dg >> END
        py, rs = run_both(g, {"x": 15})
        assert py == rs == {"result": 30}

    def test_chained_graph_decorators(self):
        @graph
        def step1(x):
            d = double(x=x)
            START >> d >> END

        @graph
        def step2(x):
            d = double(x=x)
            START >> d >> END

        with GraphOp(name="g") as g:
            a = step1(x=PARENT["x"])
            b = step2(x=a["result"])
            START >> a >> b >> END
        py, rs = run_both(g, {"x": 4})
        assert py == rs == {"result": 16}


# ── Engine reuse tests ───────────────────────────────────────────────


class TestEngineReuse:
    def test_run_twice_same_engine(self):
        with GraphOp(name="g") as g:
            d = double(x=PARENT["x"])
            START >> d >> END

        py_engine = Hush(g, mode="python")
        rs_engine = Hush(g, mode="rust")

        for x_val in [5, 10, 100]:
            py_res = asyncio.run(py_engine.run({"x": x_val}))
            rs_res = asyncio.run(rs_engine.run({"x": x_val}))
            py_res.pop("$state", None)
            rs_res.pop("$state", None)
            assert py_res == rs_res == {"result": x_val * 2}
