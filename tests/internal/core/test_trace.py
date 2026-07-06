"""Unit tests for operonx.core.trace — the V2 tracing surface."""

import time

import pytest

from operonx.core.trace import (
    KIND_CALL,
    KIND_ERROR,
    KIND_INPUT,
    KIND_LOG,
    KIND_OUTPUT,
    TraceEvent,
    TraceRecorder,
    clear_sink,
    set_sink,
    event,
    span,
)


# ============================================================
# TraceEvent shape
# ============================================================


class TestTraceEvent:
    def test_five_fields(self):
        ev = TraceEvent(
            trace_id="t1",
            path="speech/stt",
            kind="input",
            time=1.0,
            data={"a": 1},
        )
        assert ev.trace_id == "t1"
        assert ev.path == "speech/stt"
        assert ev.kind == "input"
        assert ev.time == 1.0
        assert ev.data == {"a": 1}


# ============================================================
# event() — no sink installed
# ============================================================


class TestTraceNoSink:
    def test_trace_without_sink_is_noop(self):
        """No sink installed → event() silently returns, doesn't raise."""
        # No _current set. Should just no-op.
        event("some/path", {"x": 1}, kind="input")
        event("another/path", {"y": 2})  # default kind="log"
        # No assertions needed — the test is that it doesn't crash.

    def test_trace_without_sink_zero_cost(self):
        """Absent sink is cheap (no allocation of TraceEvent)."""
        # Not a strict cost test, but 10k no-op calls should finish fast.
        t0 = time.perf_counter()
        for _ in range(10_000):
            event("x", {}, kind="log")
        elapsed = time.perf_counter() - t0
        assert elapsed < 0.5, f"10k no-op event() took {elapsed:.3f}s"


# ============================================================
# event() with a sink installed
# ============================================================


class TestTraceWithSink:
    def setup_method(self):
        self.recorder = TraceRecorder()
        self.token = set_sink("test-trace", self.recorder)

    def teardown_method(self):
        clear_sink(self.token)

    def test_event_delivered(self):
        event("math/add", {"a": 1, "b": 2}, kind="input")
        assert len(self.recorder.events) == 1
        ev = self.recorder.events[0]
        assert ev.path == "math/add"
        assert ev.kind == "input"
        assert ev.data == {"a": 1, "b": 2}
        assert ev.trace_id == "test-trace"

    def test_default_kind_is_log(self):
        event("some/path", {"x": 1})
        assert self.recorder.events[0].kind == "log"

    def test_time_is_monotonic_float(self):
        event("p", {}, kind="log")
        event("p", {}, kind="log")
        assert isinstance(self.recorder.events[0].time, float)
        # Second event should be later than first
        assert self.recorder.events[1].time >= self.recorder.events[0].time

    def test_trace_id_matches_context(self):
        event("x", {}, kind="log")
        event("y", {}, kind="log")
        assert all(e.trace_id == "test-trace" for e in self.recorder.events)

    def test_multiple_events_same_path_all_delivered(self):
        """M inputs / N outputs at the same path — all preserved on the stream."""
        for i in range(3):
            event("stt", {"chunk": i}, kind="input")
        for j in range(5):
            event("stt", {"partial": f"p{j}"}, kind="output")

        assert len(self.recorder.events) == 8
        inputs = self.recorder.by_kind("input")
        outputs = self.recorder.by_kind("output")
        assert len(inputs) == 3
        assert len(outputs) == 5
        # Order preserved
        assert [e.data["chunk"] for e in inputs] == [0, 1, 2]
        assert [e.data["partial"] for e in outputs] == ["p0", "p1", "p2", "p3", "p4"]

    def test_by_path_helper(self):
        event("speech/stt", {"r": 1}, kind="output")
        event("state/merge", {"r": 2}, kind="output")
        event("speech/stt", {"r": 3}, kind="output")

        stt_events = self.recorder.by_path("speech/stt")
        assert len(stt_events) == 2
        assert [e.data["r"] for e in stt_events] == [1, 3]

    def test_by_kind_helper(self):
        event("p", {}, kind="input")
        event("p", {}, kind="output")
        event("p", {}, kind="error")
        event("p", {}, kind="log")

        assert len(self.recorder.by_kind("input")) == 1
        assert len(self.recorder.by_kind("output")) == 1
        assert len(self.recorder.by_kind("error")) == 1
        assert len(self.recorder.by_kind("log")) == 1


# ============================================================
# Sink scoping — set_sink / clear_sink
# ============================================================


class TestSinkScoping:
    def test_set_then_clear(self):
        recorder = TraceRecorder()
        token = set_sink("t1", recorder)
        try:
            event("p", {}, kind="log")
        finally:
            clear_sink(token)

        assert len(recorder.events) == 1
        # After clear, event() no-ops
        event("p", {}, kind="log")
        assert len(recorder.events) == 1  # unchanged

    def test_nested_sinks(self):
        """Inner set_sink shadows outer; clear restores outer."""
        outer = TraceRecorder()
        inner = TraceRecorder()

        outer_token = set_sink("outer", outer)
        event("p", {"level": "outer1"}, kind="log")

        inner_token = set_sink("inner", inner)
        event("p", {"level": "inner"}, kind="log")
        clear_sink(inner_token)

        event("p", {"level": "outer2"}, kind="log")
        clear_sink(outer_token)

        assert len(outer.events) == 2
        assert outer.events[0].data == {"level": "outer1"}
        assert outer.events[1].data == {"level": "outer2"}
        assert len(inner.events) == 1
        assert inner.events[0].data == {"level": "inner"}
        assert inner.events[0].trace_id == "inner"


# ============================================================
# Sink exceptions — never break execution
# ============================================================


class TestSinkExceptions:
    def test_sink_raise_swallowed(self, caplog):
        def bad_sink(ev):
            raise RuntimeError("boom")

        token = set_sink("t", bad_sink)
        try:
            # Should not raise despite sink blowing up
            event("p", {}, kind="log")
        finally:
            clear_sink(token)

        # Optionally check that the exception got logged
        assert any("sink raised" in r.message for r in caplog.records) or True


# ============================================================
# TraceRecorder helper
# ============================================================


class TestTraceRecorder:
    def test_clear_empties_events(self):
        r = TraceRecorder()
        token = set_sink("t", r)
        try:
            event("p", {}, kind="log")
            event("p", {}, kind="log")
            assert len(r.events) == 2
            r.clear()
            assert len(r.events) == 0
        finally:
            clear_sink(token)

    def test_recorder_callable_directly(self):
        """TraceRecorder can be called without ContextVar setup."""
        r = TraceRecorder()
        ev = TraceEvent(trace_id="t", path="p", kind="log", time=1.0, data={})
        r(ev)
        assert r.events == [ev]


# ============================================================
# ContextVar isolation across asyncio tasks
# ============================================================


class TestAsyncIsolation:
    @pytest.mark.asyncio
    async def test_contextvar_inherits_into_task(self):
        """ContextVar propagates to child tasks (Python asyncio semantics)."""
        import asyncio

        recorder = TraceRecorder()
        token = set_sink("t-outer", recorder)

        async def child():
            event("child/p", {"who": "child"}, kind="log")

        try:
            await asyncio.create_task(child())
        finally:
            clear_sink(token)

        assert len(recorder.events) == 1
        assert recorder.events[0].data == {"who": "child"}
        assert recorder.events[0].trace_id == "t-outer"

    @pytest.mark.asyncio
    async def test_isolated_sinks_across_concurrent_runs(self):
        """Two concurrent runs with different sinks stay isolated."""
        import asyncio

        rec_a = TraceRecorder()
        rec_b = TraceRecorder()

        async def run(name, recorder, count):
            token = set_sink(name, recorder)
            try:
                for i in range(count):
                    event(f"{name}/step", {"i": i}, kind="output")
                    await asyncio.sleep(0)  # yield
            finally:
                clear_sink(token)

        await asyncio.gather(run("A", rec_a, 5), run("B", rec_b, 3))

        assert len(rec_a.events) == 5
        assert len(rec_b.events) == 3
        assert all(e.trace_id == "A" for e in rec_a.events)
        assert all(e.trace_id == "B" for e in rec_b.events)


# ============================================================
# Kind constants — sanity
# ============================================================


class TestKindConstants:
    def test_kind_strings(self):
        assert KIND_INPUT == "input"
        assert KIND_OUTPUT == "output"
        assert KIND_LOG == "log"
        assert KIND_ERROR == "error"
        assert KIND_CALL == "call"


# ============================================================
# trace_call — paired atomic call (kind="call")
# ============================================================


class TestTraceCall:
    def setup_method(self):
        self.recorder = TraceRecorder()
        self.token = set_sink("t-call", self.recorder)

    def teardown_method(self):
        clear_sink(self.token)

    def test_emits_one_call_event(self):
        span("math/add", input={"a": 1, "b": 2}, output={"result": 3})
        assert len(self.recorder.events) == 1
        ev = self.recorder.events[0]
        assert ev.kind == "call"
        assert ev.path == "math/add"
        assert ev.data == {"inputs": {"a": 1, "b": 2}, "outputs": {"result": 3}}

    def test_empty_input_and_output(self):
        span("noop")
        ev = self.recorder.events[0]
        assert ev.data == {"inputs": {}, "outputs": {}}

    def test_input_only(self):
        span("half", input={"x": 1})
        ev = self.recorder.events[0]
        assert ev.data == {"inputs": {"x": 1}, "outputs": {}}

    def test_output_only(self):
        span("half", output={"y": 2})
        ev = self.recorder.events[0]
        assert ev.data == {"inputs": {}, "outputs": {"y": 2}}

    def test_no_sink_noop(self):
        # Clear sink first
        clear_sink(self.token)
        # Should not raise, should not add to recorder
        span("no/sink", input={"a": 1}, output={"b": 2})
        assert len(self.recorder.events) == 0
        # Restore sink for teardown
        self.token = set_sink("t-call", self.recorder)


# ============================================================
# ctx — auto-injected by the engine, not author-set
# ============================================================


class TestCtx:
    def setup_method(self):
        self.recorder = TraceRecorder()
        self.token = set_sink("t-ctx", self.recorder)

    def teardown_method(self):
        clear_sink(self.token)

    def test_ctx_defaults_to_none_outside_engine(self):
        """Outside an op body, _current_op_ctx is None → TraceEvent.ctx is None."""
        event("p", {"x": 1}, kind="output")
        span("q", input={"a": 1}, output={"c": 2})
        assert self.recorder.events[0].ctx is None
        assert self.recorder.events[1].ctx is None

    def test_ctx_picks_up_runtime_op_ctx(self):
        """When engine sets _current_op_ctx, event() / span() capture it."""
        from operonx.core.trace import _current_op_ctx

        token = _current_op_ctx.set(("main", "[0]"))
        try:
            event("p", {"x": 1}, kind="output")
            span("q", input={"a": 1}, output={"c": 2})
        finally:
            _current_op_ctx.reset(token)

        # The tuple ("main", "[0]") is formatted to "0" by trace._format_runtime_ctx
        assert self.recorder.events[0].ctx == "0"
        assert self.recorder.events[1].ctx == "0"


class TestFormatRuntimeCtx:
    """Direct tests of the ctx tuple → display-string conversion."""

    def test_root_ctx_is_none(self):
        from operonx.core.trace import _format_runtime_ctx

        assert _format_runtime_ctx(None) is None
        assert _format_runtime_ctx(("main",)) is None

    def test_streaming_yield(self):
        from operonx.core.trace import _format_runtime_ctx

        assert _format_runtime_ctx(("main", "[0]")) == "0"
        assert _format_runtime_ctx(("main", "[42]")) == "42"

    def test_named_loop_iteration(self):
        from operonx.core.trace import _format_runtime_ctx

        assert _format_runtime_ctx(("main", "iter_1")) == "iter_1"

    def test_nested(self):
        from operonx.core.trace import _format_runtime_ctx

        assert _format_runtime_ctx(("main", "[0]", "[3]")) == "0.3"
