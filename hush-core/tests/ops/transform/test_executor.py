"""Tests for @op(executor=...) — thread pool execution."""

import threading

import pytest

from hush.core import END, PARENT, START, GraphOp, Hush, op


# ============================================================
# Sync ops with different executors
# ============================================================


@op
def default_sync(x: int):
    """Sync op, default executor (runs on event loop)."""
    return {"result": x * 2, "tid": threading.current_thread().ident}


@op(executor="thread")
def thread_sync(x: int):
    """Sync op, thread executor."""
    return {"result": x * 3, "tid": threading.current_thread().ident}


# ============================================================
# Tests
# ============================================================


class TestDefaultExecutor:
    """Default: sync ops run on the event loop thread."""

    async def test_sync_runs_on_event_loop(self):
        with GraphOp(name="g") as graph:
            step = default_sync(x=PARENT["x"])
            START >> step >> END

        result = await Hush(graph).run(inputs={"x": 7})
        assert result["result"] == 14

    async def test_sync_same_thread_as_event_loop(self):
        main_tid = threading.current_thread().ident

        with GraphOp(name="g") as graph:
            step = default_sync(x=PARENT["x"])
            START >> step >> END

        result = await Hush(graph).run(inputs={"x": 1})
        assert result["tid"] == main_tid


class TestThreadExecutor:
    """executor="thread": sync ops run in a thread pool."""

    async def test_thread_correct_result(self):
        with GraphOp(name="g") as graph:
            step = thread_sync(x=PARENT["x"])
            START >> step >> END

        result = await Hush(graph).run(inputs={"x": 7})
        assert result["result"] == 21

    async def test_thread_different_thread(self):
        main_tid = threading.current_thread().ident

        with GraphOp(name="g") as graph:
            step = thread_sync(x=PARENT["x"])
            START >> step >> END

        result = await Hush(graph).run(inputs={"x": 1})
        assert result["tid"] != main_tid


class TestAsyncOpIgnoresExecutor:
    """Async ops always run on event loop regardless of executor setting."""

    async def test_async_with_thread_executor(self):
        @op(executor="thread")
        async def async_double(x: int):
            return {"result": x * 2}

        with GraphOp(name="g") as graph:
            step = async_double(x=PARENT["x"])
            START >> step >> END

        result = await Hush(graph).run(inputs={"x": 5})
        assert result["result"] == 10


class TestCallTimeExecutorOverride:
    """executor= at call time overrides decoration-time default."""

    async def test_override_to_thread(self):
        main_tid = threading.current_thread().ident

        with GraphOp(name="g") as graph:
            # default_sync has no executor, but we override at call time
            step = default_sync(x=PARENT["x"], executor="thread")
            START >> step >> END

        result = await Hush(graph).run(inputs={"x": 3})
        assert result["result"] == 6
        assert result["tid"] != main_tid


class TestInvalidExecutor:
    """Invalid executor values raise ValueError."""

    def test_invalid_executor_at_decoration(self):
        with pytest.raises(ValueError, match="executor must be"):

            @op(executor="gpu")
            def bad(x: int):
                return {"result": x}

            bad(x=PARENT["x"])


class TestParallelExecutors:
    """Thread executors don't block each other in parallel graphs."""

    async def test_parallel_thread_ops(self):
        @op(executor="thread")
        def slow_a(x: int):
            import time

            time.sleep(0.1)
            return {"result": x + 1}

        @op(executor="thread")
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

        result = await Hush(graph).run(inputs={"x": 10})
        assert result["result"] == 23  # (10+1) + (10+2)
