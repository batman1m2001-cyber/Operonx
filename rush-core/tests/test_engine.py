"""Tests for Rush — standalone Rust execution engine.

Phase 3a tests: flat graphs with sync FuncOps (Rust-native + Python callbacks).
Phase 3b tests: branch ops, soft edges, nested GraphOps.
Phase 3c tests: iteration ops (ForOp, WhileOp).
Validates the full vertical slice: config parsing → ref resolution → scheduling → execution.
"""

import pytest
from conftest import BUILTIN_CRATE, make_rush

from hush.core import END, PARENT, START, GraphOp, graph, op
from hush.core.ops.flow.branch_op import if_
from hush.core.ops.iteration.base import Each
from hush.core.ops.iteration.for_op import ForOp
from hush.core.ops.iteration.map_op import MapOp
from hush.core.ops.iteration.while_op import WhileOp

# =============================================================================
# Helper ops
# =============================================================================


@op(rust=f"{BUILTIN_CRATE}::double")
def double(x: int):
    return {"result": x * 2}


@op(rust=f"{BUILTIN_CRATE}::add")
def add(a: int, b: int):
    return {"result": a + b}


@op(rust=f"{BUILTIN_CRATE}::greet")
def greet(name: str):
    """Greet by name — Rust native with Python fallback."""
    return {"greeting": f"Hello, {name}!"}


@op(rust=f"{BUILTIN_CRATE}::passthrough")
def identity(value):
    """Pass-through op — Rust native with Python fallback."""
    return {"out": value}


@op(rust=f"{BUILTIN_CRATE}::make_dict")
def make_dict(key: str, value):
    """Return a dict for testing getitem ref ops — Rust native with Python fallback."""
    return {"out": {key: value}}


# =============================================================================
# Single op tests
# =============================================================================


class TestSingleOp:
    def test_single_rust_op(self):
        """Single Rust-tagged FuncOp executed by Rush."""
        with GraphOp(name="g") as g:
            d = double(x=PARENT["x"])
            START >> d >> END
        g.build()

        config = g.serialize()
        engine = make_rush(config)
        result = engine.run({"x": 5})
        assert result["result"] == 10

    def test_single_python_callback(self):
        """Single Python op (no Rust impl) executed via callback."""
        with GraphOp(name="g") as g:
            step = greet(name=PARENT["name"])
            START >> step >> END
        g.build()

        config = g.serialize()
        engine = make_rush(config)
        result = engine.run({"name": "World"})
        assert result["greeting"] == "Hello, World!"

    @pytest.mark.skip(reason="Python-only op, not supported in GIL-free Rust mode")
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
        engine = make_rush(config)
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
        engine = make_rush(config)
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
        engine = make_rush(config)
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
        engine = make_rush(config)
        result = engine.run({"x": 5, "name": "Bob"})
        # Both outputs should be available (from last op connected to END)
        assert result["greeting"] == "Hello, Bob!"


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
        engine = make_rush(config)
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
        engine = make_rush(config)
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
        engine = make_rush(config)
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
        engine = make_rush(config)

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
        engine = make_rush(config)

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
        engine = make_rush(config)
        result = engine.run({"x": 2.5})
        assert result["result"] == 5.0

    def test_string_passthrough(self):
        """String values pass through correctly."""
        with GraphOp(name="g") as g:
            step = identity(value=PARENT["msg"])
            START >> step >> END
        g.build()

        config = g.serialize()
        engine = make_rush(config)
        result = engine.run({"msg": "hello"})
        assert result["out"] == "hello"

    def test_list_passthrough(self):
        """List values pass through correctly."""
        with GraphOp(name="g") as g:
            step = identity(value=PARENT["items"])
            START >> step >> END
        g.build()

        config = g.serialize()
        engine = make_rush(config)
        result = engine.run({"items": [1, 2, 3]})
        assert result["out"] == [1, 2, 3]

    def test_dict_passthrough(self):
        """Dict values pass through correctly."""
        with GraphOp(name="g") as g:
            step = identity(value=PARENT["data"])
            START >> step >> END
        g.build()

        config = g.serialize()
        engine = make_rush(config)
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


@pytest.mark.skip(reason="Branch tests use Python-only grade ops")
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
        engine = make_rush(config)
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
        engine = make_rush(config)
        result = engine.run({"score": 50})
        assert result["grade"] == "F"
        assert result["message"] == "Try again!"

    def test_multi_condition_branch(self):
        """Branch with multiple conditions routes correctly."""
        with GraphOp(name="g") as g:
            router = if_(PARENT["score"] >= 90, "a").if_(PARENT["score"] >= 70, "b").else_("f")
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
        engine = make_rush(config)

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
        engine = make_rush(config)

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
        engine = make_rush(config)
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
        engine = make_rush(config)
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
        engine = make_rush(config)
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
        engine = make_rush(config)
        result = engine.run({"x": 5, "y": 3})
        assert result["result"] == 13  # (5 * 2) + 3


# =============================================================================
# ForOp tests (Phase 3c)
# =============================================================================


class TestForOp:
    def test_simple_for_literal_each(self):
        """ForOp iterates over literal Each values and doubles them."""

        @op(rust=f"{BUILTIN_CRATE}::dbl")
        def dbl(value: int):
            return {"result": value * 2}

        with GraphOp(name="g") as g:
            with ForOp(name="loop", inputs={"value": Each([1, 2, 3])}) as loop:
                node = dbl(value=PARENT["value"])
                START >> node >> END
            START >> loop >> END
        g.build()

        config = g.serialize()
        engine = make_rush(config)
        result = engine.run({})
        assert result["result"] == [2, 4, 6]

    def test_for_with_broadcast(self):
        """ForOp with Each values + broadcast scalar."""

        @op(rust=f"{BUILTIN_CRATE}::multiply_values")
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
        engine = make_rush(config)
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
        engine = make_rush(config)
        result = engine.run({})
        assert result["result"] == [11, 22, 33]

    def test_for_empty_list(self):
        """ForOp with empty Each list produces empty results."""

        @op(rust=f"{BUILTIN_CRATE}::dbl")
        def dbl(value: int):
            return {"result": value * 2}

        with GraphOp(name="g") as g:
            with ForOp(name="loop", inputs={"value": Each([])}) as loop:
                node = dbl(value=PARENT["value"])
                START >> node >> END
            START >> loop >> END
        g.build()

        config = g.serialize()
        engine = make_rush(config)
        result = engine.run({})
        assert result["result"] == []

    def test_for_with_upstream_ref(self):
        """ForOp reads Each from an upstream op's output."""

        @op(rust=f"{BUILTIN_CRATE}::make_list")
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
        engine = make_rush(config)
        result = engine.run({})
        assert result["result"] == [20, 40, 60]


# =============================================================================
# WhileOp tests (Phase 3c)
# =============================================================================


class TestWhileOp:
    def test_simple_counter(self):
        """WhileOp counts from 0 to 5."""

        @op(rust=f"{BUILTIN_CRATE}::increment_counter")
        def increment(counter: int):
            return {"new_counter": counter + 1}

        with GraphOp(name="g") as g:
            with WhileOp(name="loop", inputs={"counter": 0}, until="counter >= 5") as loop:
                step = increment(counter=PARENT["counter"])
                step["new_counter"] >> PARENT["counter"]
                START >> step >> END
            START >> loop >> END
        g.build()

        config = g.serialize()
        engine = make_rush(config)
        result = engine.run({})
        assert result["counter"] == 5

    def test_max_iterations_safety(self):
        """WhileOp stops at max_iterations when no until condition."""

        @op(rust=f"{BUILTIN_CRATE}::increment_counter")
        def increment(counter: int):
            return {"new_counter": counter + 1}

        with GraphOp(name="g") as g:
            with WhileOp(name="loop", inputs={"counter": 0}, max_iterations=5) as loop:
                step = increment(counter=PARENT["counter"])
                step["new_counter"] >> PARENT["counter"]
                START >> step >> END
            START >> loop >> END
        g.build()

        config = g.serialize()
        engine = make_rush(config)
        result = engine.run({})
        assert result["counter"] == 5

    def test_while_accumulator(self):
        """WhileOp accumulates by 15 until total >= 100."""

        @op(rust=f"{BUILTIN_CRATE}::accumulate")
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
        engine = make_rush(config)
        result = engine.run({})
        assert result["total"] == 105  # 15 * 7 = 105

    def test_while_fibonacci(self):
        """WhileOp computes Fibonacci until b >= 21."""

        @op(rust=f"{BUILTIN_CRATE}::fib_step")
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
        engine = make_rush(config)
        result = engine.run({})
        assert result["b"] == 21  # fib: 0,1,1,2,3,5,8,13,21

    @pytest.mark.skip(reason="Python-only op, not supported in GIL-free Rust mode")
    def test_while_with_upstream_ref(self):
        """WhileOp with initial value from an upstream op."""

        @op
        def make_start():
            return {"start": 90}

        @op(rust=f"{BUILTIN_CRATE}::increment_counter")
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
        engine = make_rush(config)
        result = engine.run({})
        assert result["counter"] == 95


# =============================================================================
# Observability tests
# =============================================================================


@op(rust=f"{BUILTIN_CRATE}::tagged_op")
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
        engine = make_rush(config)
        result = engine.run({"x": 5})
        # Disabled op produces no outputs — result should not have "result" key
        assert "result" not in result
        # Disabled ops skip entirely — no start_time stored
        assert "g.d" not in result["$state"].get("values", {})

    def test_timing_metadata(self):
        """Per-op duration_ms should be recorded in $state."""
        with GraphOp(name="g") as g:
            d = double(x=PARENT["x"])
            START >> d >> END
        g.build()

        config = g.serialize()
        engine = make_rush(config)
        result = engine.run({"x": 5})
        assert result["result"] == 10
        # Timing metadata should be stored in $state values
        assert "$state" in result
        values = result["$state"]["values"]
        assert "g.d" in values
        assert "duration_ms" in values["g.d"]
        assert "start_time" in values["g.d"]
        assert "end_time" in values["g.d"]

    def test_tags_extraction(self):
        """$tags in op result should appear in $state.tags."""
        with GraphOp(name="g") as g:
            t = tagged_op(x=PARENT["x"])
            START >> t >> END
        g.build()

        config = g.serialize()
        engine = make_rush(config)
        result = engine.run({"x": 5})
        assert result["result"] == 10
        assert "fast" in result["$state"]["tags"]
        assert "cached" in result["$state"]["tags"]

    def test_start_time_tracking(self):
        """Each executed op should have start_time in $state values."""
        with GraphOp(name="g") as g:
            a = double(x=PARENT["x"])
            b = double(x=a["result"])
            c = double(x=b["result"])
            START >> a >> b >> c >> END
        g.build()

        config = g.serialize()
        engine = make_rush(config)
        result = engine.run({"x": 1})
        assert result["result"] == 8

        values = result["$state"]["values"]
        # All three ops should have start_time stored
        for op_name in ["g.a", "g.b", "g.c"]:
            assert op_name in values, f"{op_name} missing from values"
            assert "start_time" in values[op_name], f"{op_name} missing start_time"
            assert "" in values[op_name]["start_time"], (
                f"{op_name} start_time has no default context"
            )

    @pytest.mark.skip(reason="Python-only op, not supported in GIL-free Rust mode")
    def test_slow_op_warning(self):
        """Ops >100ms should emit a Python warning."""
        with GraphOp(name="g") as g:
            s = slow_op(x=PARENT["x"])
            START >> s >> END
        g.build()

        config = g.serialize()
        engine = make_rush(config)

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


@op(rust=f"{BUILTIN_CRATE}::safe_op")
def safe_op(x: int):
    """Op that succeeds."""
    return {"result": x + 1}


@pytest.mark.skip(reason="Python-only op, not supported in GIL-free Rust mode")
class TestErrorResilience:
    def test_error_in_op_continues_graph(self):
        """An op that raises should not stop subsequent ops from running."""
        with GraphOp(name="g") as g:
            bad = raise_op(x=PARENT["x"])
            good = safe_op(x=PARENT["x"])
            START >> bad >> good >> END
        g.build()

        config = g.serialize()
        engine = make_rush(config)
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
        engine = make_rush(config)
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
            with ForOp(name="loop", inputs={"value": Each([1, 2, 3])}, fail_fast=False) as loop:
                node = maybe_fail(value=PARENT["value"])
                START >> node >> END
            START >> loop >> END
        g.build()

        config = g.serialize()
        engine = make_rush(config)
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

        @op(rust=f"{BUILTIN_CRATE}::ok_op")
        def ok_op(value: int):
            return {"result": value * 10}

        with GraphOp(name="g") as g:
            with ForOp(name="loop", inputs={"value": Each([1, 2, 3])}, fail_fast=True) as loop:
                node = ok_op(value=PARENT["value"])
                START >> node >> END
            START >> loop >> END
        g.build()

        config = g.serialize()
        engine = make_rush(config)
        result = engine.run({})

        # Normal execution works fine with fail_fast
        assert result["result"] == [10, 20, 30]

    def test_while_condition_error_continues(self):
        """WhileOp with invalid condition should continue (not crash)."""

        @op(rust=f"{BUILTIN_CRATE}::increment_counter")
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
        engine = make_rush(config)
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
        engine = make_rush(config)
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
        engine = make_rush(config)
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
        engine = make_rush(config)
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
        from datetime import datetime

        from hush.core.tracing.rush_state import RushStateAdapter

        t1 = datetime(2024, 1, 1, 12, 0, 0)

        state_dict = {
            "tags": ["fast"],
            "request_id": "req-1",
            "user_id": "user-1",
            "session_id": "sess-1",
            "values": {
                "g.a": {
                    "result": {"": 42},
                    "duration_ms": {"": 0.5},
                    "start_time": {"": t1},
                }
            },
        }

        adapter = RushStateAdapter(state_dict)
        # iter_executed derives execution from start_time values
        executed = list(adapter.iter_executed("g.a"))
        assert len(executed) == 1
        assert executed[0] == ("", t1)
        # Non-existent op returns empty
        assert list(adapter.iter_executed("missing_op")) == []
        # Properties
        assert adapter.tags == ["fast"]
        assert adapter.request_id == "req-1"
        assert adapter.user_id == "user-1"
        assert adapter.session_id == "sess-1"
        # __getitem__ lookups
        assert adapter["g.a", "result", ""] == 42
        assert adapter["g.a", "duration_ms", ""] == 0.5
        assert adapter["g.a", "nonexistent", ""] is None
        assert adapter["missing_op", "x", ""] is None


# =============================================================================
# Async op tests (Phase 1: async coroutine driving)
# =============================================================================


@op
async def async_double(x: int):
    """Async op that doubles a value."""
    import asyncio

    await asyncio.sleep(0)  # yield to event loop
    return {"result": x * 2}


@op
async def async_add(a: int, b: int):
    """Async op that adds two values."""
    import asyncio

    await asyncio.sleep(0)
    return {"result": a + b}


@pytest.mark.skip(reason="Python-only op, not supported in GIL-free Rust mode")
class TestAsyncOps:
    def test_single_async_op(self):
        """Single async Python op runs correctly in Rust mode."""
        with GraphOp(name="g") as g:
            step = async_double(x=PARENT["x"])
            START >> step >> END
        g.build()

        config = g.serialize()
        engine = make_rush(config)
        result = engine.run({"x": 21})
        assert result["result"] == 42

    def test_async_op_chain(self):
        """Chain of async ops — each coroutine driven to completion."""
        with GraphOp(name="g") as g:
            d = async_double(x=PARENT["x"])
            a = async_add(a=d["result"], b=PARENT["y"])
            START >> d >> a >> END
        g.build()

        config = g.serialize()
        engine = make_rush(config)
        result = engine.run({"x": 5, "y": 3})
        assert result["result"] == 13  # 5*2=10, 10+3=13

    def test_parallel_async_ops(self):
        """Multiple async ops in parallel batch — heuristic triggers parallelism."""
        with GraphOp(name="g") as g:
            a = async_double(x=PARENT["x"])
            b = async_double(x=PARENT["y"])
            c = add(a=a["result"], b=b["result"])
            START >> a
            START >> b
            a >> c
            b >> c
            c >> END
        g.build()

        config = g.serialize()
        engine = make_rush(config)
        result = engine.run({"x": 3, "y": 7})
        assert result["result"] == 20  # 3*2=6, 7*2=14, 6+14=20

    def test_mixed_async_and_sync(self):
        """Graph with both async and sync ops."""
        with GraphOp(name="g") as g:
            d = async_double(x=PARENT["x"])
            a = add(a=d["result"], b=PARENT["y"])
            START >> d >> a >> END
        g.build()

        config = g.serialize()
        engine = make_rush(config)
        result = engine.run({"x": 5, "y": 3})
        assert result["result"] == 13

    @pytest.mark.parametrize("mode", ["python", "rust"])
    async def test_async_op_both_modes(self, mode):
        """Async op produces same result in both execution modes."""
        from hush.core import Hush

        with GraphOp(name="g") as g:
            step = async_double(x=PARENT["x"])
            START >> step >> END

        result = await Hush(g, mode=mode).run(inputs={"x": 7})
        assert result["result"] == 14


# =============================================================================
# MapOp tests (Phase 2: concurrent iteration)
# =============================================================================


class TestMapOp:
    def test_simple_map_literal_each(self):
        """MapOp iterates over literal Each values concurrently."""

        @op(rust=f"{BUILTIN_CRATE}::dbl")
        def dbl(value: int):
            return {"result": value * 2}

        with GraphOp(name="g") as g:
            with MapOp(name="loop", inputs={"value": Each([1, 2, 3])}) as loop:
                node = dbl(value=PARENT["value"])
                START >> node >> END
            START >> loop >> END
        g.build()

        config = g.serialize()
        engine = make_rush(config)
        result = engine.run({})
        assert result["result"] == [2, 4, 6]

    def test_map_with_broadcast(self):
        """MapOp with Each values + broadcast scalar."""

        @op(rust=f"{BUILTIN_CRATE}::multiply_values")
        def multiply(value: int, multiplier: int):
            return {"result": value * multiplier}

        with GraphOp(name="g") as g:
            with MapOp(
                name="loop",
                inputs={"value": Each([1, 2, 3]), "multiplier": 10},
            ) as loop:
                node = multiply(value=PARENT["value"], multiplier=PARENT["multiplier"])
                START >> node >> END
            START >> loop >> END
        g.build()

        config = g.serialize()
        engine = make_rush(config)
        result = engine.run({})
        assert result["result"] == [10, 20, 30]

    def test_map_multiple_each_zip(self):
        """MapOp with multiple Each values (zipped)."""

        with GraphOp(name="g") as g:
            with MapOp(
                name="loop",
                inputs={"a": Each([1, 2, 3]), "b": Each([10, 20, 30])},
            ) as loop:
                node = add(a=PARENT["a"], b=PARENT["b"])
                START >> node >> END
            START >> loop >> END
        g.build()

        config = g.serialize()
        engine = make_rush(config)
        result = engine.run({})
        assert result["result"] == [11, 22, 33]

    def test_map_empty_list(self):
        """MapOp with empty Each list produces empty results."""

        @op(rust=f"{BUILTIN_CRATE}::dbl")
        def dbl(value: int):
            return {"result": value * 2}

        with GraphOp(name="g") as g:
            with MapOp(name="loop", inputs={"value": Each([])}) as loop:
                node = dbl(value=PARENT["value"])
                START >> node >> END
            START >> loop >> END
        g.build()

        config = g.serialize()
        engine = make_rush(config)
        result = engine.run({})
        assert result["result"] == []

    def test_map_with_upstream_ref(self):
        """MapOp reads Each from an upstream op's output."""

        @op(rust=f"{BUILTIN_CRATE}::make_list")
        def make_list():
            return {"items": [10, 20, 30]}

        with GraphOp(name="g") as g:
            src = make_list()
            with MapOp(name="loop", inputs={"value": Each(src["items"])}) as loop:
                node = double(x=PARENT["value"])
                START >> node >> END
            START >> src >> loop >> END
        g.build()

        config = g.serialize()
        engine = make_rush(config)
        result = engine.run({})
        assert result["result"] == [20, 40, 60]

    def test_map_with_max_concurrency(self):
        """MapOp respects max_concurrency parameter."""

        @op(rust=f"{BUILTIN_CRATE}::dbl")
        def dbl(value: int):
            return {"result": value * 2}

        with GraphOp(name="g") as g:
            with MapOp(
                name="loop",
                inputs={"value": Each([1, 2, 3, 4, 5])},
                max_concurrency=2,
            ) as loop:
                node = dbl(value=PARENT["value"])
                START >> node >> END
            START >> loop >> END
        g.build()

        config = g.serialize()
        engine = make_rush(config)
        result = engine.run({})
        assert result["result"] == [2, 4, 6, 8, 10]

    def test_map_fail_fast_config_parsed(self):
        """MapOp fail_fast=True: config is parsed, normal execution works.

        fail_fast only catches errors that propagate through run_graph
        (infrastructure failures, not individual op errors caught by execute_leaf_op).
        This matches ForOp behavior.
        """

        @op(rust=f"{BUILTIN_CRATE}::ok_op")
        def ok_op(value: int):
            return {"result": value * 10}

        with GraphOp(name="g") as g:
            with MapOp(
                name="loop",
                inputs={"value": Each([1, 2, 3])},
                fail_fast=True,
                max_concurrency=1,
            ) as loop:
                node = ok_op(value=PARENT["value"])
                START >> node >> END
            START >> loop >> END
        g.build()

        config = g.serialize()
        engine = make_rush(config)
        result = engine.run({})
        assert result["result"] == [10, 20, 30]

    @pytest.mark.skip(reason="Python-only op, not supported in GIL-free Rust mode")
    def test_map_no_fail_fast_continues(self):
        """MapOp with fail_fast=False continues on error (per-op error handling)."""

        @op
        def maybe_fail(value: int):
            if value == 2:
                raise ValueError("fail on 2")
            return {"result": value * 10}

        with GraphOp(name="g") as g:
            with MapOp(
                name="loop",
                inputs={"value": Each([1, 2, 3])},
                fail_fast=False,
                max_concurrency=1,
            ) as loop:
                node = maybe_fail(value=PARENT["value"])
                START >> node >> END
            START >> loop >> END
        g.build()

        config = g.serialize()
        engine = make_rush(config)
        result = engine.run({})

        # All iterations complete — erroring op produces None
        assert result["result"][0] == 10
        assert result["result"][1] is None  # Op error caught
        assert result["result"][2] == 30

    def test_map_iteration_metrics(self):
        """MapOp stores iteration_metrics in state."""

        @op(rust=f"{BUILTIN_CRATE}::dbl")
        def dbl(value: int):
            return {"result": value * 2}

        with GraphOp(name="g") as g:
            with MapOp(name="loop", inputs={"value": Each([1, 2, 3])}) as loop:
                node = dbl(value=PARENT["value"])
                START >> node >> END
            START >> loop >> END
        g.build()

        config = g.serialize()
        engine = make_rush(config)
        result = engine.run({})

        state = result["$state"]
        metrics = state["values"]["g.loop"]["iteration_metrics"][""]
        assert metrics["total_iterations"] == 3
        assert metrics["success_count"] == 3
        assert metrics["error_count"] == 0

    @pytest.mark.parametrize("mode", ["python", "rust"])
    async def test_map_both_modes(self, mode):
        """MapOp produces same result in both execution modes."""
        from hush.core import Hush

        @op(rust=f"{BUILTIN_CRATE}::dbl")
        def dbl(value: int):
            return {"result": value * 2}

        with GraphOp(name="g") as g:
            with MapOp(name="loop", inputs={"value": Each([1, 2, 3])}) as loop:
                node = dbl(value=PARENT["value"])
                START >> node >> END
            START >> loop >> END

        result = await Hush(g, mode=mode).run(inputs={})
        assert result["result"] == [2, 4, 6]
