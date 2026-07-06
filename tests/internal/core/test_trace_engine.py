"""Engine integration tests for the V2 tracing pipeline.

Verifies that ``Operon.run(sink=..., trace_id=...)`` correctly propagates
the sink into op execution via ContextVar, and that ``event()`` calls
made inside op bodies land on the sink.
"""

import asyncio

import pytest

from operonx.core import END, PARENT, START, GraphOp, Operon, op
from operonx.core.trace import TraceRecorder, event


# ============================================================
# Basic emit from inside op body
# ============================================================


class TestTraceInsideOp:
    @pytest.mark.asyncio
    async def test_trace_from_sync_op(self):
        recorder = TraceRecorder()

        @op
        def add(a: int, b: int):
            event("math/add", {"a": a, "b": b}, kind="input")
            c = a + b
            event("math/add", {"result": c}, kind="output")
            return {"result": c}

        with GraphOp(name="wf") as g:
            step = add(a=PARENT["a"], b=PARENT["b"])
            START >> step >> END

        engine = Operon(g)
        result = await engine.run(inputs={"a": 3, "b": 4}, sink=recorder, trace_id="t-add")

        assert result["result"] == 7
        assert len(recorder.events) == 2
        assert recorder.events[0].kind == "input"
        assert recorder.events[0].path == "math/add"
        assert recorder.events[0].trace_id == "t-add"
        assert recorder.events[0].data == {"a": 3, "b": 4}
        assert recorder.events[1].kind == "output"
        assert recorder.events[1].data == {"result": 7}

    @pytest.mark.asyncio
    async def test_trace_from_async_op(self):
        recorder = TraceRecorder()

        @op
        async def slow_add(a: int, b: int):
            event("math/slow_add", {"a": a, "b": b}, kind="input")
            await asyncio.sleep(0)
            c = a + b
            event("math/slow_add", {"result": c}, kind="output")
            return {"result": c}

        with GraphOp(name="wf") as g:
            step = slow_add(a=PARENT["a"], b=PARENT["b"])
            START >> step >> END

        engine = Operon(g)
        result = await engine.run(inputs={"a": 1, "b": 2}, sink=recorder, trace_id="t-slow")

        assert result["result"] == 3
        assert len(recorder.events) == 2

    @pytest.mark.asyncio
    async def test_trace_from_multiple_ops(self):
        """Two ops in a chain each emit their own events."""
        recorder = TraceRecorder()

        @op
        def a_op(x: int):
            event("chain/a", {"x": x}, kind="input")
            r = x * 2
            event("chain/a", {"r": r}, kind="output")
            return {"r": r}

        @op
        def b_op(y: int):
            event("chain/b", {"y": y}, kind="input")
            r = y + 1
            event("chain/b", {"r": r}, kind="output")
            return {"result": r}

        with GraphOp(name="wf") as g:
            a = a_op(x=PARENT["x"])
            b = b_op(y=a["r"])
            START >> a >> b >> END

        engine = Operon(g)
        result = await engine.run(inputs={"x": 5}, sink=recorder, trace_id="t-chain")

        assert result["result"] == 11
        assert len(recorder.events) == 4
        paths = [e.path for e in recorder.events]
        assert paths == ["chain/a", "chain/a", "chain/b", "chain/b"]


# ============================================================
# No sink → no-op
# ============================================================


class TestNoSink:
    @pytest.mark.asyncio
    async def test_run_without_sink_does_not_crash(self):
        """Op calls event() but no sink set → silent no-op."""

        @op
        def add(a: int, b: int):
            event("math/add", {"a": a, "b": b}, kind="input")
            return {"result": a + b}

        with GraphOp(name="wf") as g:
            step = add(a=PARENT["a"], b=PARENT["b"])
            START >> step >> END

        engine = Operon(g)
        # No sink= passed. event() calls should silently no-op.
        result = await engine.run(inputs={"a": 1, "b": 2})
        assert result["result"] == 3


# ============================================================
# trace_id auto-defaults to request_id
# ============================================================


class TestTraceIdDefault:
    @pytest.mark.asyncio
    async def test_trace_id_defaults_to_request_id(self):
        recorder = TraceRecorder()

        @op
        def noop():
            event("noop", {}, kind="output")
            return {}

        with GraphOp(name="wf") as g:
            step = noop()
            START >> step >> END

        engine = Operon(g)
        # No trace_id= passed; should default to request_id
        await engine.run(inputs={}, sink=recorder, request_id="req-42")
        assert len(recorder.events) == 1
        assert recorder.events[0].trace_id == "req-42"

    @pytest.mark.asyncio
    async def test_explicit_trace_id_overrides_request_id(self):
        recorder = TraceRecorder()

        @op
        def noop():
            event("noop", {}, kind="output")
            return {}

        with GraphOp(name="wf") as g:
            step = noop()
            START >> step >> END

        engine = Operon(g)
        await engine.run(inputs={}, sink=recorder, request_id="req-42", trace_id="my-trace")
        assert recorder.events[0].trace_id == "my-trace"


# ============================================================
# Concurrent runs — isolated sinks
# ============================================================


class TestConcurrentIsolation:
    @pytest.mark.asyncio
    async def test_two_concurrent_runs_have_isolated_sinks(self):
        rec_a = TraceRecorder()
        rec_b = TraceRecorder()

        @op
        def emit(x: int, label: str):
            event(f"{label}/step", {"x": x}, kind="output")
            return {"result": x}

        with GraphOp(name="wf") as g:
            step = emit(x=PARENT["x"], label=PARENT["label"])
            START >> step >> END

        engine = Operon(g)

        await asyncio.gather(
            engine.run(inputs={"x": 1, "label": "A"}, sink=rec_a, trace_id="ta"),
            engine.run(inputs={"x": 2, "label": "B"}, sink=rec_b, trace_id="tb"),
        )

        assert len(rec_a.events) == 1
        assert len(rec_b.events) == 1
        assert rec_a.events[0].trace_id == "ta"
        assert rec_a.events[0].data == {"x": 1}
        assert rec_b.events[0].trace_id == "tb"
        assert rec_b.events[0].data == {"x": 2}


# ============================================================
# Sink exceptions don't break op execution
# ============================================================


class TestSinkExceptions:
    @pytest.mark.asyncio
    async def test_sink_exception_swallowed_op_still_returns(self):
        def bad_sink(ev):
            raise RuntimeError("sink is broken")

        @op
        def compute(a: int):
            event("compute", {"a": a}, kind="input")
            return {"result": a * 10}

        with GraphOp(name="wf") as g:
            step = compute(a=PARENT["a"])
            START >> step >> END

        engine = Operon(g)
        result = await engine.run(inputs={"a": 3}, sink=bad_sink, trace_id="t-bad")
        assert result["result"] == 30


# ============================================================
# Streaming op — 1 input + N outputs
# ============================================================


class TestStreaming:
    @pytest.mark.asyncio
    async def test_generator_op_emits_M_output_events(self):
        recorder = TraceRecorder()

        @op
        def source(count: int):
            event("gen/source", {"count": count}, kind="input")
            for i in range(count):
                event("gen/source", {"i": i}, kind="output")
                yield {"i": i}
            event("gen/source", {"final": True}, kind="output")

        with GraphOp(name="wf") as g:
            gen = source(count=PARENT["n"])
            START >> gen >> END

        engine = Operon(g)
        await engine.run(inputs={"n": 3}, sink=recorder, trace_id="t-stream")

        # Expected: 1 input, 3 per-iteration outputs, 1 final output
        inputs = recorder.by_kind("input")
        outputs = recorder.by_kind("output")
        assert len(inputs) == 1
        assert len(outputs) == 4
        assert [e.data for e in outputs] == [
            {"i": 0}, {"i": 1}, {"i": 2}, {"final": True}
        ]
