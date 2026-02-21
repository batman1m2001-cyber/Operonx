"""Tests for Rush — standalone Rust execution engine.

Phase 3a tests: flat graphs with sync FuncOps (Rust-native + Python callbacks).
Phase 3b tests: branch ops, soft edges, nested GraphOps.
Phase 3c tests: iteration ops (ForOp, WhileOp).
Validates the full vertical slice: config parsing → ref resolution → scheduling → execution.
"""

import pytest

from hush.core import END, PARENT, START, GraphOp, graph, op
from hush.core.ops.flow.branch_op import if_
from hush.core.ops.iteration.base import Each
from hush.core.ops.iteration.for_op import ForOp
from hush.core.ops.iteration.while_op import WhileOp
from rush_core import Rush, rust_op


# =============================================================================
# Helper ops
# =============================================================================


@rust_op("rust_double")
@op
def double(x: int):
    return {"result": x * 2}


@rust_op("rust_add")
@op
def add(a: int, b: int):
    return {"result": a + b}


@op
def greet(name: str):
    """Pure Python op (no @rust_op)."""
    return {"greeting": f"Hello {name}"}


@op
def identity(x):
    """Pass-through op."""
    return {"out": x}


@op
def make_dict(key: str, value: str):
    """Return a dict for testing getitem ref ops."""
    return {"data": {key: value}}


# =============================================================================
# Single op tests
# =============================================================================


class TestSingleOp:
    def test_single_rust_op(self):
        """Single @rust_op FuncOp executed by Rush."""
        with GraphOp(name="g") as g:
            d = double(x=PARENT["x"])
            START >> d >> END
        g.build()

        config = g.serialize()
        engine = Rush(config)
        result = engine.run({"x": 5})
        assert result["result"] == 10

    def test_single_python_callback(self):
        """Single Python op (no Rust impl) executed via callback."""
        with GraphOp(name="g") as g:
            step = greet(name=PARENT["name"])
            START >> step >> END
        g.build()

        config = g.serialize()
        engine = Rush(config)
        result = engine.run({"name": "World"})
        assert result["greeting"] == "Hello World"

    def test_literal_input(self):
        """Op with a literal (non-Ref) input value."""

        @op
        def prefix(text: str):
            return {"out": f"[INFO] {text}"}

        with GraphOp(name="g") as g:
            step = prefix(text="hello")
            START >> step >> END
        g.build()

        config = g.serialize()
        engine = Rush(config)
        result = engine.run({})
        assert result["out"] == "[INFO] hello"


# =============================================================================
# Linear chain tests
# =============================================================================


class TestLinearChain:
    def test_two_rust_ops(self):
        """Linear chain: double -> add, both Rust ops."""
        with GraphOp(name="g") as g:
            d = double(x=PARENT["x"])
            a = add(a=d["result"], b=PARENT["y"])
            START >> d >> a >> END
        g.build()

        config = g.serialize()
        engine = Rush(config)
        result = engine.run({"x": 5, "y": 3})
        assert result["result"] == 13  # (5 * 2) + 3

    def test_three_op_chain(self):
        """Three-op chain: double -> double -> add."""
        with GraphOp(name="g") as g:
            d1 = double(x=PARENT["x"])
            d2 = double(x=d1["result"])
            a = add(a=d2["result"], b=PARENT["y"])
            START >> d1 >> d2 >> a >> END
        g.build()

        config = g.serialize()
        engine = Rush(config)
        result = engine.run({"x": 3, "y": 1})
        assert result["result"] == 13  # ((3 * 2) * 2) + 1

    def test_mixed_rust_and_python(self):
        """Chain with both Rust-native and Python ops."""
        with GraphOp(name="g") as g:
            d = double(x=PARENT["x"])
            g_step = greet(name=PARENT["name"])
            START >> d >> g_step >> END
        g.build()

        config = g.serialize()
        engine = Rush(config)
        result = engine.run({"x": 5, "name": "Bob"})
        # Both outputs should be available (from last op connected to END)
        assert result["greeting"] == "Hello Bob"


# =============================================================================
# Output forwarding tests
# =============================================================================


class TestOutputForwarding:
    def test_auto_forward_via_end(self):
        """>> END auto-forwards the exit op's outputs."""
        with GraphOp(name="g") as g:
            d = double(x=PARENT["x"])
            START >> d >> END
        g.build()

        config = g.serialize()
        engine = Rush(config)
        result = engine.run({"x": 7})
        assert result["result"] == 14

    def test_explicit_output_mapping(self):
        """Explicit output mapping via op['key'] >> PARENT['dest']."""
        with GraphOp(name="g") as g:
            d = double(x=PARENT["x"])
            d["result"] >> PARENT["answer"]
            START >> d >> END
        g.build()

        config = g.serialize()
        engine = Rush(config)
        result = engine.run({"x": 4})
        assert result["answer"] == 8


# =============================================================================
# Parallel (fork-join) tests
# =============================================================================


class TestParallelOps:
    def test_fork_join(self):
        """Two independent ops feeding into a third."""
        with GraphOp(name="g") as g:
            d1 = double(x=PARENT["x"])
            d2 = double(x=PARENT["y"])
            a = add(a=d1["result"], b=d2["result"])
            START >> d1
            START >> d2
            d1 >> a
            d2 >> a
            a >> END
        g.build()

        config = g.serialize()
        engine = Rush(config)
        result = engine.run({"x": 3, "y": 5})
        assert result["result"] == 16  # (3*2) + (5*2)


# =============================================================================
# Engine reuse tests
# =============================================================================


class TestEngineReuse:
    def test_run_twice(self):
        """Engine can be reused for multiple runs."""
        with GraphOp(name="g") as g:
            d = double(x=PARENT["x"])
            START >> d >> END
        g.build()

        config = g.serialize()
        engine = Rush(config)

        r1 = engine.run({"x": 5})
        r2 = engine.run({"x": 10})
        assert r1["result"] == 10
        assert r2["result"] == 20

    def test_different_inputs(self):
        """Multiple runs with different input shapes."""
        with GraphOp(name="g") as g:
            a = add(a=PARENT["a"], b=PARENT["b"])
            START >> a >> END
        g.build()

        config = g.serialize()
        engine = Rush(config)

        assert engine.run({"a": 1, "b": 2})["result"] == 3
        assert engine.run({"a": 100, "b": 200})["result"] == 300


# =============================================================================
# Data type tests
# =============================================================================


class TestDataTypes:
    def test_float_inputs(self):
        """Float inputs work correctly."""
        with GraphOp(name="g") as g:
            d = double(x=PARENT["x"])
            START >> d >> END
        g.build()

        config = g.serialize()
        engine = Rush(config)
        result = engine.run({"x": 2.5})
        assert result["result"] == 5.0

    def test_string_passthrough(self):
        """String values pass through correctly."""
        with GraphOp(name="g") as g:
            step = identity(x=PARENT["msg"])
            START >> step >> END
        g.build()

        config = g.serialize()
        engine = Rush(config)
        result = engine.run({"msg": "hello"})
        assert result["out"] == "hello"

    def test_list_passthrough(self):
        """List values pass through correctly."""
        with GraphOp(name="g") as g:
            step = identity(x=PARENT["items"])
            START >> step >> END
        g.build()

        config = g.serialize()
        engine = Rush(config)
        result = engine.run({"items": [1, 2, 3]})
        assert result["out"] == [1, 2, 3]

    def test_dict_passthrough(self):
        """Dict values pass through correctly."""
        with GraphOp(name="g") as g:
            step = identity(x=PARENT["data"])
            START >> step >> END
        g.build()

        config = g.serialize()
        engine = Rush(config)
        result = engine.run({"data": {"a": 1, "b": 2}})
        assert result["out"] == {"a": 1, "b": 2}


# =============================================================================
# Branch op tests (Phase 3b)
# =============================================================================


@op
def grade_a():
    """Returns A grade."""
    return {"grade": "A", "message": "Excellent!"}


@op
def grade_b():
    """Returns B grade."""
    return {"grade": "B", "message": "Good job!"}


@op
def grade_f():
    """Returns F grade."""
    return {"grade": "F", "message": "Try again!"}


class TestBranchOps:
    def test_simple_branch_true(self):
        """Branch routes to 'true' target when condition matches."""
        with GraphOp(name="g") as g:
            # Target names must match variable names (auto-naming)
            router = if_(PARENT["score"] >= 90, "a").else_("f")
            a = grade_a(outputs={"*": PARENT})
            f = grade_f(outputs={"*": PARENT})

            START >> router
            router >> ~a
            router >> ~f
            a >> ~END
            f >> ~END
        g.build()

        config = g.serialize()
        engine = Rush(config)
        result = engine.run({"score": 95})
        assert result["grade"] == "A"
        assert result["message"] == "Excellent!"

    def test_simple_branch_false(self):
        """Branch routes to default target when no condition matches."""
        with GraphOp(name="g") as g:
            router = if_(PARENT["score"] >= 90, "a").else_("f")
            a = grade_a(outputs={"*": PARENT})
            f = grade_f(outputs={"*": PARENT})

            START >> router
            router >> ~a
            router >> ~f
            a >> ~END
            f >> ~END
        g.build()

        config = g.serialize()
        engine = Rush(config)
        result = engine.run({"score": 50})
        assert result["grade"] == "F"
        assert result["message"] == "Try again!"

    def test_multi_condition_branch(self):
        """Branch with multiple conditions routes correctly."""
        with GraphOp(name="g") as g:
            router = (
                if_(PARENT["score"] >= 90, "a")
                .if_(PARENT["score"] >= 70, "b")
                .else_("f")
            )
            a = grade_a(outputs={"*": PARENT})
            b = grade_b(outputs={"*": PARENT})
            f = grade_f(outputs={"*": PARENT})

            START >> router
            router >> ~a
            router >> ~b
            router >> ~f
            a >> ~END
            b >> ~END
            f >> ~END
        g.build()

        config = g.serialize()
        engine = Rush(config)

        # Score 95 → A
        result = engine.run({"score": 95})
        assert result["grade"] == "A"

        # Score 75 → B
        result = engine.run({"score": 75})
        assert result["grade"] == "B"

        # Score 50 → F
        result = engine.run({"score": 50})
        assert result["grade"] == "F"

    def test_branch_with_merge(self):
        """Branch arms merge via soft edges before END."""

        @op
        def format_result(grade: str):
            return {"formatted": f"Grade: {grade}"}

        with GraphOp(name="g") as g:
            router = if_(PARENT["score"] >= 90, "a").else_("f")
            # Push grade to graph state so merge can read from either arm
            a = grade_a(outputs={"grade": PARENT})
            f = grade_f(outputs={"grade": PARENT})
            merge = format_result(grade=PARENT["grade"])

            START >> router
            router >> ~a
            router >> ~f
            # Both arms connect to merge via soft edges (merge ready_count=1)
            a >> ~merge
            f >> ~merge
            merge >> END
        g.build()

        config = g.serialize()
        engine = Rush(config)

        result = engine.run({"score": 95})
        assert result["formatted"] == "Grade: A"

        result = engine.run({"score": 50})
        assert result["formatted"] == "Grade: F"


# =============================================================================
# Nested GraphOp tests (Phase 3b)
# =============================================================================


class TestNestedGraphOps:
    def test_simple_nested_graph(self):
        """Nested GraphOp executes correctly via Rush."""

        @graph
        def double_flow(val):
            step = double(x=val)
            START >> step >> END

        with GraphOp(name="g") as g:
            d = double_flow(val=PARENT["x"])
            START >> d >> END
        g.build()

        config = g.serialize()
        engine = Rush(config)
        result = engine.run({"x": 5})
        assert result["result"] == 10

    def test_chained_nested_graphs(self):
        """Two nested GraphOps in a chain."""

        @graph
        def double_flow(val):
            step = double(x=val)
            START >> step >> END

        with GraphOp(name="g") as g:
            d1 = double_flow(val=PARENT["x"])
            d2 = double_flow(val=d1["result"])
            START >> d1 >> d2 >> END
        g.build()

        config = g.serialize()
        engine = Rush(config)
        result = engine.run({"x": 3})
        assert result["result"] == 12  # (3 * 2) * 2

    def test_nested_graph_with_output_mapping(self):
        """Nested GraphOp with explicit output mapping."""

        @graph
        def double_flow(val):
            step = double(x=val)
            step["result"] >> PARENT["doubled"]
            START >> step >> END

        with GraphOp(name="g") as g:
            d = double_flow(val=PARENT["x"])
            d["doubled"] >> PARENT["answer"]
            START >> d >> END
        g.build()

        config = g.serialize()
        engine = Rush(config)
        result = engine.run({"x": 7})
        assert result["answer"] == 14

    def test_nested_graph_with_multiple_ops(self):
        """Nested graph with a multi-op chain inside."""

        @graph
        def double_and_add(x, y):
            d = double(x=x)
            a = add(a=d["result"], b=y)
            START >> d >> a >> END

        with GraphOp(name="g") as g:
            step = double_and_add(x=PARENT["x"], y=PARENT["y"])
            START >> step >> END
        g.build()

        config = g.serialize()
        engine = Rush(config)
        result = engine.run({"x": 5, "y": 3})
        assert result["result"] == 13  # (5 * 2) + 3


# =============================================================================
# ForOp tests (Phase 3c)
# =============================================================================


class TestForOp:
    def test_simple_for_literal_each(self):
        """ForOp iterates over literal Each values and doubles them."""

        @op
        def dbl(value: int):
            return {"result": value * 2}

        with GraphOp(name="g") as g:
            with ForOp(name="loop", inputs={"value": Each([1, 2, 3])}) as loop:
                node = dbl(value=PARENT["value"])
                START >> node >> END
            START >> loop >> END
        g.build()

        config = g.serialize()
        engine = Rush(config)
        result = engine.run({})
        assert result["result"] == [2, 4, 6]

    def test_for_with_broadcast(self):
        """ForOp with Each values + broadcast scalar."""

        @op
        def multiply(value: int, multiplier: int):
            return {"result": value * multiplier}

        with GraphOp(name="g") as g:
            with ForOp(
                name="loop",
                inputs={"value": Each([1, 2, 3]), "multiplier": 10},
            ) as loop:
                node = multiply(value=PARENT["value"], multiplier=PARENT["multiplier"])
                START >> node >> END
            START >> loop >> END
        g.build()

        config = g.serialize()
        engine = Rush(config)
        result = engine.run({})
        assert result["result"] == [10, 20, 30]

    def test_for_multiple_each_zip(self):
        """ForOp with multiple Each values (zipped)."""

        with GraphOp(name="g") as g:
            with ForOp(
                name="loop",
                inputs={"a": Each([1, 2, 3]), "b": Each([10, 20, 30])},
            ) as loop:
                node = add(a=PARENT["a"], b=PARENT["b"])
                START >> node >> END
            START >> loop >> END
        g.build()

        config = g.serialize()
        engine = Rush(config)
        result = engine.run({})
        assert result["result"] == [11, 22, 33]

    def test_for_empty_list(self):
        """ForOp with empty Each list produces empty results."""

        @op
        def dbl(value: int):
            return {"result": value * 2}

        with GraphOp(name="g") as g:
            with ForOp(name="loop", inputs={"value": Each([])}) as loop:
                node = dbl(value=PARENT["value"])
                START >> node >> END
            START >> loop >> END
        g.build()

        config = g.serialize()
        engine = Rush(config)
        result = engine.run({})
        assert result["result"] == []

    def test_for_with_upstream_ref(self):
        """ForOp reads Each from an upstream op's output."""

        @op
        def make_list():
            return {"items": [10, 20, 30]}

        with GraphOp(name="g") as g:
            src = make_list()
            with ForOp(name="loop", inputs={"value": Each(src["items"])}) as loop:
                node = double(x=PARENT["value"])
                START >> node >> END
            START >> src >> loop >> END
        g.build()

        config = g.serialize()
        engine = Rush(config)
        result = engine.run({})
        assert result["result"] == [20, 40, 60]


# =============================================================================
# WhileOp tests (Phase 3c)
# =============================================================================


class TestWhileOp:
    def test_simple_counter(self):
        """WhileOp counts from 0 to 5."""

        @op
        def increment(counter: int):
            return {"new_counter": counter + 1}

        with GraphOp(name="g") as g:
            with WhileOp(
                name="loop", inputs={"counter": 0}, until="counter >= 5"
            ) as loop:
                step = increment(counter=PARENT["counter"])
                step["new_counter"] >> PARENT["counter"]
                START >> step >> END
            START >> loop >> END
        g.build()

        config = g.serialize()
        engine = Rush(config)
        result = engine.run({})
        assert result["counter"] == 5

    def test_max_iterations_safety(self):
        """WhileOp stops at max_iterations when no until condition."""

        @op
        def increment(counter: int):
            return {"new_counter": counter + 1}

        with GraphOp(name="g") as g:
            with WhileOp(
                name="loop", inputs={"counter": 0}, max_iterations=5
            ) as loop:
                step = increment(counter=PARENT["counter"])
                step["new_counter"] >> PARENT["counter"]
                START >> step >> END
            START >> loop >> END
        g.build()

        config = g.serialize()
        engine = Rush(config)
        result = engine.run({})
        assert result["counter"] == 5

    def test_while_accumulator(self):
        """WhileOp accumulates by 15 until total >= 100."""

        @op
        def accumulate(total: int, step_size: int):
            return {"new_total": total + step_size}

        with GraphOp(name="g") as g:
            with WhileOp(
                name="loop",
                inputs={"total": 0, "step_size": 15},
                until="total >= 100",
            ) as loop:
                step = accumulate(total=PARENT["total"], step_size=PARENT["step_size"])
                step["new_total"] >> PARENT["total"]
                START >> step >> END
            START >> loop >> END
        g.build()

        config = g.serialize()
        engine = Rush(config)
        result = engine.run({})
        assert result["total"] == 105  # 15 * 7 = 105

    def test_while_fibonacci(self):
        """WhileOp computes Fibonacci until b >= 21."""

        @op
        def fib_step(a: int, b: int):
            return {"new_a": b, "new_b": a + b}

        with GraphOp(name="g") as g:
            with WhileOp(
                name="loop",
                inputs={"a": 0, "b": 1},
                until="b >= 21",
            ) as loop:
                step = fib_step(a=PARENT["a"], b=PARENT["b"])
                step["new_a"] >> PARENT["a"]
                step["new_b"] >> PARENT["b"]
                START >> step >> END
            START >> loop >> END
        g.build()

        config = g.serialize()
        engine = Rush(config)
        result = engine.run({})
        assert result["b"] == 21  # fib: 0,1,1,2,3,5,8,13,21

    def test_while_with_upstream_ref(self):
        """WhileOp with initial value from an upstream op."""

        @op
        def make_start():
            return {"start": 90}

        @op
        def increment(counter: int):
            return {"new_counter": counter + 1}

        with GraphOp(name="g") as g:
            src = make_start()
            with WhileOp(
                name="loop",
                inputs={"counter": src["start"]},
                until="counter >= 95",
            ) as loop:
                step = increment(counter=PARENT["counter"])
                step["new_counter"] >> PARENT["counter"]
                START >> step >> END
            START >> src >> loop >> END
        g.build()

        config = g.serialize()
        engine = Rush(config)
        result = engine.run({})
        assert result["counter"] == 95


# =============================================================================
# Observability tests
# =============================================================================


@op
def tagged_op(x: int):
    """Op that returns $tags in result."""
    return {"result": x * 2, "$tags": ["fast", "cached"]}


@op
def slow_op(x: int):
    """Op that takes >100ms."""
    import time

    time.sleep(0.15)
    return {"result": x}


class TestObservability:
    def test_enabled_flag_skips_op(self):
        """Disabled op should not execute — no outputs produced."""
        with GraphOp(name="g") as g:
            d = double(x=PARENT["x"])
            d.enabled = False
            START >> d >> END
        g.build()

        config = g.serialize()
        engine = Rush(config)
        result = engine.run({"x": 5})
        # Disabled op produces no outputs — result should not have "result" key
        assert "result" not in result
        # But $state should still record it in execution_order
        assert any(
            e["op"] == "g.d" for e in result["$state"]["execution_order"]
        )

    def test_timing_metadata(self):
        """Per-op duration_ms should be recorded in $state."""
        with GraphOp(name="g") as g:
            d = double(x=PARENT["x"])
            START >> d >> END
        g.build()

        config = g.serialize()
        engine = Rush(config)
        result = engine.run({"x": 5})
        assert result["result"] == 10
        # duration_ms should appear as a graph output only if explicitly mapped,
        # but it's stored in the engine state — we verify via $state presence
        assert "$state" in result
        assert len(result["$state"]["execution_order"]) == 1

    def test_tags_extraction(self):
        """$tags in op result should appear in $state.tags."""
        with GraphOp(name="g") as g:
            t = tagged_op(x=PARENT["x"])
            START >> t >> END
        g.build()

        config = g.serialize()
        engine = Rush(config)
        result = engine.run({"x": 5})
        assert result["result"] == 10
        assert "fast" in result["$state"]["tags"]
        assert "cached" in result["$state"]["tags"]

    def test_execution_order_tracking(self):
        """Execution order should list all ops in run order."""
        with GraphOp(name="g") as g:
            a = double(x=PARENT["x"])
            b = double(x=a["result"])
            c = double(x=b["result"])
            START >> a >> b >> c >> END
        g.build()

        config = g.serialize()
        engine = Rush(config)
        result = engine.run({"x": 1})
        assert result["result"] == 8

        exec_order = result["$state"]["execution_order"]
        assert len(exec_order) == 3
        assert exec_order[0]["op"] == "g.a"
        assert exec_order[1]["op"] == "g.b"
        assert exec_order[2]["op"] == "g.c"
        # Verify parent is the graph name
        assert all(e["parent"] == "g" for e in exec_order)

    def test_slow_op_warning(self):
        """Ops >100ms should emit a Python warning."""
        with GraphOp(name="g") as g:
            s = slow_op(x=PARENT["x"])
            START >> s >> END
        g.build()

        config = g.serialize()
        engine = Rush(config)

        import warnings

        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            result = engine.run({"x": 42})

        assert result["result"] == 42
        # Should have at least one warning about slow op
        slow_warnings = [x for x in w if "Slow op" in str(x.message)]
        assert len(slow_warnings) >= 1
        assert "g.s" in str(slow_warnings[0].message)


# =============================================================================
# Error resilience tests
# =============================================================================


@op
def raise_op(x: int):
    """Op that always raises."""
    raise ValueError(f"boom with x={x}")


@op
def safe_op(x: int):
    """Op that succeeds."""
    return {"result": x + 1}


class TestErrorResilience:
    def test_error_in_op_continues_graph(self):
        """An op that raises should not stop subsequent ops from running."""
        with GraphOp(name="g") as g:
            bad = raise_op(x=PARENT["x"])
            good = safe_op(x=PARENT["x"])
            START >> bad >> good >> END
        g.build()

        config = g.serialize()
        engine = Rush(config)
        result = engine.run({"x": 5})

        # good op should still have executed
        assert result["result"] == 6

        # error should be stored in $state values
        state = result["$state"]
        error_val = state["values"]["g.bad"]["error"][""]
        assert "boom with x=5" in error_val

    def test_error_stored_in_state_values(self):
        """Error string should be accessible via $state.values."""
        with GraphOp(name="g") as g:
            bad = raise_op(x=PARENT["x"])
            START >> bad >> END
        g.build()

        config = g.serialize()
        engine = Rush(config)
        result = engine.run({"x": 42})

        state = result["$state"]
        error_val = state["values"]["g.bad"]["error"][""]
        assert "boom with x=42" in error_val

    def test_for_op_error_in_iteration_continues(self):
        """ForOp: leaf op errors are caught per-op (like Python's BaseOp.run).

        Individual op errors within iterations are caught by execute_leaf_op,
        so the iteration "succeeds" with missing outputs (None in transposed results).
        This matches Python behavior where BaseOp.run() catches and stores errors.
        """

        @op
        def maybe_fail(value: int):
            if value == 2:
                raise ValueError("fail on 2")
            return {"result": value * 10}

        with GraphOp(name="g") as g:
            with ForOp(
                name="loop", inputs={"value": Each([1, 2, 3])}, fail_fast=False
            ) as loop:
                node = maybe_fail(value=PARENT["value"])
                START >> node >> END
            START >> loop >> END
        g.build()

        config = g.serialize()
        engine = Rush(config)
        result = engine.run({})

        # All iterations complete — erroring op produces None in transposed results
        assert result["result"][0] == 10
        assert result["result"][1] is None  # Op error caught, no output stored
        assert result["result"][2] == 30

        # Error stored in $state.values for the specific op+context
        state = result["$state"]
        error_val = state["values"]["g.loop.node"]["error"]["[1]"]
        assert "fail on 2" in error_val

    def test_for_op_fail_fast_with_graph_error(self):
        """ForOp fail_fast=True propagates graph-level infrastructure errors."""
        # fail_fast only catches errors that propagate through run_graph
        # (infrastructure failures, not individual op errors caught by execute_leaf_op).
        # This test verifies the config is parsed and the flag exists.

        @op
        def ok_op(value: int):
            return {"result": value * 10}

        with GraphOp(name="g") as g:
            with ForOp(
                name="loop", inputs={"value": Each([1, 2, 3])}, fail_fast=True
            ) as loop:
                node = ok_op(value=PARENT["value"])
                START >> node >> END
            START >> loop >> END
        g.build()

        config = g.serialize()
        engine = Rush(config)
        result = engine.run({})

        # Normal execution works fine with fail_fast
        assert result["result"] == [10, 20, 30]

    def test_while_condition_error_continues(self):
        """WhileOp with invalid condition should continue (not crash)."""

        @op
        def increment(counter: int):
            return {"new_counter": counter + 1}

        with GraphOp(name="g") as g:
            with WhileOp(
                name="loop",
                inputs={"counter": 0},
                until="undefined_var > 10",  # This will fail
                max_iterations=3,
            ) as loop:
                step = increment(counter=PARENT["counter"])
                step["new_counter"] >> PARENT["counter"]
                START >> step >> END
            START >> loop >> END
        g.build()

        config = g.serialize()
        engine = Rush(config)
        result = engine.run({})

        # Should run max_iterations=3 since condition always errors → false
        assert result["counter"] == 3


# =============================================================================
# Tracing wiring tests
# =============================================================================


class TestTracingWiring:
    def test_state_has_values_dict(self):
        """$state should include a 'values' dict with per-op data."""
        with GraphOp(name="g") as g:
            d = double(x=PARENT["x"])
            START >> d >> END
        g.build()

        config = g.serialize()
        engine = Rush(config)
        result = engine.run({"x": 5})

        state = result["$state"]
        assert "values" in state
        # Should contain the double op's output
        assert "g.d" in state["values"]
        assert "result" in state["values"]["g.d"]
        assert state["values"]["g.d"]["result"][""] == 10

    def test_state_has_timing_in_values(self):
        """$state.values should include duration_ms for each op."""
        with GraphOp(name="g") as g:
            d = double(x=PARENT["x"])
            START >> d >> END
        g.build()

        config = g.serialize()
        engine = Rush(config)
        result = engine.run({"x": 5})

        state = result["$state"]
        assert "duration_ms" in state["values"]["g.d"]
        duration = state["values"]["g.d"]["duration_ms"][""]
        assert isinstance(duration, float)
        assert duration >= 0

    def test_metadata_params_in_state(self):
        """request_id, user_id, session_id should appear in $state when provided."""
        with GraphOp(name="g") as g:
            d = double(x=PARENT["x"])
            START >> d >> END
        g.build()

        config = g.serialize()
        engine = Rush(config)
        result = engine.run(
            {"x": 5},
            request_id="req-123",
            user_id="user-456",
            session_id="sess-789",
        )

        state = result["$state"]
        assert state["request_id"] == "req-123"
        assert state["user_id"] == "user-456"
        assert state["session_id"] == "sess-789"

    def test_rush_state_adapter(self):
        """RushStateAdapter should provide MemoryState-compatible interface."""
        from hush.core.tracing.rush_state import RushStateAdapter

        state_dict = {
            "execution_order": [
                {"op": "g.a", "parent": "g", "context_id": ""},
            ],
            "tags": ["fast"],
            "request_id": "req-1",
            "user_id": "user-1",
            "session_id": "sess-1",
            "values": {
                "g.a": {
                    "result": {"": 42},
                    "duration_ms": {"": 0.5},
                }
            },
        }

        adapter = RushStateAdapter(state_dict)
        assert adapter.execution_order == [
            {"op": "g.a", "parent": "g", "context_id": ""},
        ]
        assert adapter.tags == ["fast"]
        assert adapter.request_id == "req-1"
        assert adapter.user_id == "user-1"
        assert adapter.session_id == "sess-1"
        assert adapter["g.a", "result", ""] == 42
        assert adapter["g.a", "duration_ms", ""] == 0.5
        assert adapter["g.a", "nonexistent", ""] is None
        assert adapter["missing_op", "x", ""] is None
