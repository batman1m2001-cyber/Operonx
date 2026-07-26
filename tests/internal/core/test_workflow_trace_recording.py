"""Engine recording hook tests — V3 auto-tracing (Step 2).

Verifies:
* `handle.trace` is populated by the time the run completes.
* Batch ops → one `OpExecution`; streaming ops → one per yield with
  nested ctx.
* Multi-upstream aggregators produce a list of `UpstreamRef`s.
* Op errors surface as `status="error"` with a traceback.
* Concurrent runs each own their own `WorkflowTrace` (ContextVar
  isolation).
"""

from __future__ import annotations

import asyncio

import pytest

from operonx.core import END, PARENT, START, Operon, graph
from operonx.core.ops import op
from operonx.core.workflow_trace import (
    STATUS_ERROR,
    STATUS_OK,
    OpExecution,
    WorkflowTrace,
    make_op_id,
)


# ---------------------------------------------------------------------------
# Ops used across cases
# ---------------------------------------------------------------------------


@op
def add_one(x: int):
    return {"y": x + 1}


@op
def double(y: int):
    return {"z": y * 2}


@op
def combine(a: int, b: int):
    """Multi-upstream aggregator."""
    return {"sum": a + b}


@op
async def repeat_stream(n: int):
    """Streaming op: yields N frames."""
    for i in range(n):
        yield {"item": i}


@op
def boom(x: int):
    raise RuntimeError(f"boom at {x}")


# ---------------------------------------------------------------------------
# Batch: one OpExecution per op
# ---------------------------------------------------------------------------


class TestBatchOps:
    async def test_two_op_linear_chain(self):
        @graph
        def wf(x: int):
            a = add_one(x=PARENT["x"])
            b = double(y=a["y"])
            START >> a >> b >> END

        engine = Operon(wf, params={"x": 0})
        handle = engine.start(inputs={"x": 5})
        await handle.collect()

        trace = handle.trace
        assert isinstance(trace, WorkflowTrace)
        # Both ops recorded, both status ok
        by_var = {n.op_name: n for n in trace.nodes}
        assert "a" in by_var and "b" in by_var
        assert all(n.status == STATUS_OK for n in trace.nodes)

    async def test_inputs_outputs_captured(self):
        @graph
        def wf(x: int):
            a = add_one(x=PARENT["x"])
            START >> a >> END

        engine = Operon(wf, params={"x": 0})
        handle = engine.start(inputs={"x": 7})
        await handle.collect()

        add = next(n for n in handle.trace.nodes if n.op_name == "a")
        assert add.inputs == {"x": 7}
        assert add.outputs == {"y": 8}
        assert add.duration_ms >= 0

    async def test_upstreams_link_producer_to_consumer(self):
        """`double` has one upstream: `add_one.y → double.y`. The
        upstream's `from_op_id` matches `add_one`'s `OpExecution.op_id`."""
        @graph
        def wf(x: int):
            a = add_one(x=PARENT["x"])
            b = double(y=a["y"])
            START >> a >> b >> END

        engine = Operon(wf, params={"x": 0})
        handle = engine.start(inputs={"x": 3})
        await handle.collect()

        producer = next(n for n in handle.trace.nodes if n.op_name == "a")
        consumer = next(n for n in handle.trace.nodes if n.op_name == "b")
        assert len(consumer.upstreams) == 1
        u = consumer.upstreams[0]
        assert u.from_op_id == producer.op_id
        assert u.from_key == "y"
        assert u.to_key == "y"


# ---------------------------------------------------------------------------
# Streaming: N yields → N OpExecutions with nested ctx
# ---------------------------------------------------------------------------


class TestStreamingOps:
    async def test_yields_produce_n_executions(self):
        @graph
        def wf(n: int):
            src = repeat_stream(n=PARENT["n"])
            START >> src >> END

        engine = Operon(wf, params={"n": 0})
        handle = engine.start(inputs={"n": 3})
        await handle.collect()

        streams = [n for n in handle.trace.nodes if n.op_name == "src"]
        assert len(streams) == 3

    async def test_yields_have_nested_ctx(self):
        @graph
        def wf(n: int):
            src = repeat_stream(n=PARENT["n"])
            START >> src >> END

        engine = Operon(wf, params={"n": 0})
        handle = engine.start(inputs={"n": 3})
        await handle.collect()

        streams = sorted(
            (n for n in handle.trace.nodes if n.op_name == "src"),
            key=lambda n: n.ctx,
        )
        # Each yield's ctx = ("main", "[i]") — same top-level "main",
        # different yield sub-index.
        for i, exec_ in enumerate(streams):
            assert exec_.ctx[-1] == f"[{i}]"
            assert exec_.outputs == {"item": i}


# ---------------------------------------------------------------------------
# Multi-upstream: upstreams is a list
# ---------------------------------------------------------------------------


class TestMultiUpstream:
    async def test_two_upstreams_captured(self):
        @graph
        def wf(x: int, y: int):
            a = add_one(x=PARENT["x"])
            b = add_one(x=PARENT["y"])
            c = combine(a=a["y"], b=b["y"])
            START >> a
            START >> b
            a >> c
            b >> c
            c >> END

        engine = Operon(wf, params={"x": 0, "y": 0})
        handle = engine.start(inputs={"x": 3, "y": 4})
        await handle.collect()

        combine_exec = next(
            n for n in handle.trace.nodes if n.op_name == "c"
        )
        assert len(combine_exec.upstreams) == 2
        # Both upstreams point at the two `add_one` invocations we
        # assigned to graph vars `a` and `b`.
        producer_vars = {u.from_op_name for u in combine_exec.upstreams}
        assert producer_vars == {"a", "b"}
        # Distinct `from_op_id`s — two separate `add_one` invocations
        assert len({u.from_op_id for u in combine_exec.upstreams}) == 2


# ---------------------------------------------------------------------------
# Errors surface as status=error + traceback
# ---------------------------------------------------------------------------


class TestErrorRecording:
    async def test_error_captured_with_status_and_traceback(self):
        @graph
        def wf(x: int):
            b = boom(x=PARENT["x"])
            START >> b >> END

        engine = Operon(wf, params={"x": 0})
        handle = engine.start(inputs={"x": 99})
        try:
            await handle.collect()
        except Exception:
            pass  # error propagates through handle; we care about the trace

        errored = [n for n in handle.trace.nodes if n.status == STATUS_ERROR]
        assert len(errored) == 1
        assert errored[0].op_name == "b"
        assert errored[0].op_full_name.endswith(".b")
        assert "RuntimeError" in (errored[0].error or "")


# ---------------------------------------------------------------------------
# ContextVar isolation — two concurrent runs don't cross-contaminate
# ---------------------------------------------------------------------------


class TestConcurrentIsolation:
    async def test_two_engines_get_own_traces(self):
        @graph
        def wf(x: int):
            a = add_one(x=PARENT["x"])
            START >> a >> END

        engine = Operon(wf, params={"x": 0})

        # Fire two runs concurrently — each MUST get its own WorkflowTrace.
        h1 = engine.start(inputs={"x": 10})
        h2 = engine.start(inputs={"x": 20})
        await asyncio.gather(h1.collect(), h2.collect())

        assert h1.trace is not h2.trace
        # Each trace contains exactly the ops from its own run.
        h1_add = next(n for n in h1.trace.nodes if n.op_name == "a")
        h2_add = next(n for n in h2.trace.nodes if n.op_name == "a")
        assert h1_add.outputs == {"y": 11}
        assert h2_add.outputs == {"y": 21}


# ---------------------------------------------------------------------------
# WorkflowTrace metadata + timing
# ---------------------------------------------------------------------------


class TestTraceMetadata:
    async def test_metadata_populated(self):
        @graph
        def wf(x: int):
            a = add_one(x=PARENT["x"])
            START >> a >> END

        engine = Operon(wf, params={"x": 0})
        handle = engine.start(
            inputs={"x": 1},
            user_id="u-1", session_id="s-1", request_id="req-1",
        )
        await handle.collect()

        m = handle.trace.metadata
        assert m["user_id"] == "u-1"
        assert m["session_id"] == "s-1"
        assert m["request_id"] == "req-1"
        # trace_id defaults to request_id
        assert handle.trace.trace_id == "req-1"

    async def test_ended_at_set_after_collect(self):
        @graph
        def wf(x: int):
            a = add_one(x=PARENT["x"])
            START >> a >> END

        engine = Operon(wf, params={"x": 0})
        handle = engine.start(inputs={"x": 1})
        assert handle.trace.ended_at == 0.0    # not yet finished
        await handle.collect()
        assert handle.trace.ended_at > handle.trace.started_at
        assert handle.trace.duration_ms > 0
