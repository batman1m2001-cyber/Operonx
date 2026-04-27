"""Tests for @op(bound=...) — dispatch strategy."""

import threading

import pytest

from operonx.core import END, PARENT, START, GraphOp, Operon, op

# ============================================================
# Sync ops with different bounds
# ============================================================


@op
def default_sync(x: int):
    """Sync op, default bound (auto → sync)."""
    return {"result": x * 2, "tid": threading.current_thread().ident}


@op(bound="cpu")
def cpu_sync(x: int):
    """Sync op, cpu bound (thread pool)."""
    return {"result": x * 3, "tid": threading.current_thread().ident}


# ============================================================
# Tests
# ============================================================


class TestDefaultBound:
    """Default: sync ops run inline on the event loop thread."""

    async def test_sync_runs_on_event_loop(self):
        with GraphOp(name="g") as graph:
            step = default_sync(x=PARENT["x"])
            START >> step >> END

        result = await Operon(graph).run(inputs={"x": 7})
        assert result["result"] == 14

    async def test_sync_same_thread_as_event_loop(self):
        main_tid = threading.current_thread().ident

        with GraphOp(name="g") as graph:
            step = default_sync(x=PARENT["x"])
            START >> step >> END

        result = await Operon(graph).run(inputs={"x": 1})
        assert result["tid"] == main_tid


class TestCPUBound:
    """bound="cpu": sync ops run in a thread pool."""

    async def test_cpu_correct_result(self):
        with GraphOp(name="g") as graph:
            step = cpu_sync(x=PARENT["x"])
            START >> step >> END

        result = await Operon(graph).run(inputs={"x": 7})
        assert result["result"] == 21

    async def test_cpu_different_thread(self):
        main_tid = threading.current_thread().ident

        with GraphOp(name="g") as graph:
            step = cpu_sync(x=PARENT["x"])
            START >> step >> END

        result = await Operon(graph).run(inputs={"x": 1})
        assert result["tid"] != main_tid


class TestAsyncOpIgnoresCPUBound:
    """Async ops always run on event loop regardless of bound setting."""

    async def test_async_with_cpu_bound(self):
        @op(bound="cpu")
        async def async_double(x: int):
            return {"result": x * 2}

        with GraphOp(name="g") as graph:
            step = async_double(x=PARENT["x"])
            START >> step >> END

        result = await Operon(graph).run(inputs={"x": 5})
        assert result["result"] == 10


class TestCallTimeBoundOverride:
    """bound= at call time overrides decoration-time default."""

    async def test_override_to_cpu(self):
        main_tid = threading.current_thread().ident

        with GraphOp(name="g") as graph:
            # default_sync has no bound, but we override at call time
            step = default_sync(x=PARENT["x"], bound="cpu")
            START >> step >> END

        result = await Operon(graph).run(inputs={"x": 3})
        assert result["result"] == 6
        assert result["tid"] != main_tid


class TestBackwardCompatExecutor:
    """executor='thread' still works, mapped to bound='cpu'."""

    async def test_executor_thread_maps_to_cpu(self):
        @op(executor="thread")
        def legacy_op(x: int):
            return {"result": x * 4, "tid": threading.current_thread().ident}

        main_tid = threading.current_thread().ident

        with GraphOp(name="g") as graph:
            step = legacy_op(x=PARENT["x"])
            START >> step >> END

        result = await Operon(graph).run(inputs={"x": 2})
        assert result["result"] == 8
        assert result["tid"] != main_tid


class TestInvalidBound:
    """Invalid bound values raise ValueError."""

    def test_invalid_bound(self):
        with pytest.raises(ValueError, match="bound must be"):

            @op(bound="gpu")
            def bad(x: int):
                return {"result": x}

            bad(x=PARENT["x"])


class TestParallelCPUOps:
    """CPU-bound ops don't block each other in parallel graphs."""

    async def test_parallel_cpu_ops(self):
        @op(bound="cpu")
        def slow_a(x: int):
            import time

            time.sleep(0.1)
            return {"result": x + 1}

        @op(bound="cpu")
        def slow_b(x: int):
            import time

            time.sleep(0.1)
            return {"result": x + 2}

        @op
        def combine(a: int, b: int):
            return {"result": a + b}

        with GraphOp(name="g") as graph:
            a = slow_a(x=PARENT["x"])
            b = slow_b(x=PARENT["x"])
            c = combine(a=a["result"], b=b["result"])
            START >> [a, b] >> c >> END

        result = await Operon(graph).run(inputs={"x": 10})
        assert result["result"] == 23  # (10+1) + (10+2)
