"""End-to-end T1 wiring tests — TracePipeline bound to a real engine.

Verifies the full path from `engine.start(tracer=pipeline)` through op execution
to the pipeline buffer and exporter dispatch. These are the gate tests for the
T1 wire-up: if these pass, the new event-stream path is working alongside the
legacy collector with no regressions.
"""

import asyncio

import pytest

from operonx.core import END, PARENT, START, GraphOp, Operon, op
from operonx.core.tracing.events import EventKind
from operonx.core.tracing.pipeline import TracePipeline


class _Collector:
    """Captures one export() call per flush."""

    def __init__(self):
        self.events = []
        self.calls = 0

    def export(self, events, request_id, metadata):
        self.calls += 1
        self.events.extend(events)


# =============================================================================
# Shared ops
# =============================================================================


@op
def double(x: int):
    return {"result": x * 2}


@op
def add_one(x: int):
    return {"result": x + 1}


@op
def emit_three(_signal):
    yield {"v": 1}
    yield {"v": 2}
    yield {"v": 3}


# =============================================================================
# Tests
# =============================================================================


class TestPipelineBinding:
    @pytest.mark.asyncio
    async def test_pipeline_receives_op_start_and_op_end(self):
        exp = _Collector()
        pipeline = TracePipeline(exporters=[exp])

        with GraphOp(name="g") as g:
            d = double(x=PARENT["x"])
            START >> d >> END
            d["result"] >> PARENT["out"]

        engine = Operon(g, tracer=pipeline)
        result = await engine.run(inputs={"x": 5})
        assert result["out"] == 10

        # Final flush happens in engine.start's finally — exporter should have
        # received exactly one batch with start/end for the op.
        # Op name is the qualified form: graph_name + "." + variable_name (here "g.d").
        assert exp.calls == 1
        kinds = [e.kind for e in exp.events]
        assert EventKind.OP_START in kinds
        assert EventKind.OP_END in kinds
        starts = [e for e in exp.events
                  if e.kind is EventKind.OP_START and e.op_name == "g.d"]
        ends = [e for e in exp.events
                if e.kind is EventKind.OP_END and e.op_name == "g.d"]
        assert len(starts) == 1
        assert len(ends) == 1
        assert starts[0].payload["inputs"] == {"x": 5}
        assert ends[0].payload["status"] == "ok"
        assert ends[0].payload["outputs"] == {"result": 10}
        assert ends[0].payload["duration_ms"] >= 0

    @pytest.mark.asyncio
    async def test_generator_emits_op_yield_per_yield(self):
        exp = _Collector()
        pipeline = TracePipeline(exporters=[exp])

        with GraphOp(name="g") as g:
            gen = emit_three(_signal=PARENT["seed"])
            START >> gen >> END
            gen["v"] >> PARENT["values"]

        engine = Operon(g, tracer=pipeline)
        await engine.run(inputs={"seed": 1})

        # Op name is "g.gen" (graph + variable name)
        yields = [e for e in exp.events
                  if e.kind is EventKind.OP_YIELD and e.op_name == "g.gen"]
        assert len(yields) == 3
        assert [y.payload["idx"] for y in yields] == [0, 1, 2]
        assert [y.payload["yielded"] for y in yields] == [{"v": 1}, {"v": 2}, {"v": 3}]
        # OP_END carries yield_count
        end = next(e for e in exp.events
                   if e.kind is EventKind.OP_END and e.op_name == "g.gen")
        assert end.payload["yield_count"] == 3

    @pytest.mark.asyncio
    async def test_op_end_status_error_on_exception(self):
        exp = _Collector()
        pipeline = TracePipeline(exporters=[exp])

        @op
        def boom(x: int):
            raise ValueError("nope")

        with GraphOp(name="g") as g:
            b = boom(x=PARENT["x"])
            START >> b >> END

        engine = Operon(g, tracer=pipeline)
        # The graph swallows op errors today; just await completion.
        try:
            await engine.run(inputs={"x": 1})
        except Exception:
            pass

        boom_ends = [e for e in exp.events
                     if e.kind is EventKind.OP_END and e.op_name == "g.b"]
        assert len(boom_ends) == 1
        assert boom_ends[0].payload["status"] == "error"

    @pytest.mark.asyncio
    async def test_no_pipeline_does_not_emit(self):
        """Sanity: engine without a TracePipeline → no events appear because
        no exporter is bound (NullEmitter swallows). Existing tests should
        still pass."""
        with GraphOp(name="g") as g:
            d = double(x=PARENT["x"])
            START >> d >> END
            d["result"] >> PARENT["out"]

        engine = Operon(g)
        result = await engine.run(inputs={"x": 7})
        assert result["out"] == 14

    @pytest.mark.asyncio
    async def test_concurrent_runs_isolated_buffers(self):
        """Two concurrent engine.run() calls must not cross-pollinate buffers."""
        with GraphOp(name="g") as g:
            d = double(x=PARENT["x"])
            START >> d >> END
            d["result"] >> PARENT["out"]

        engine = Operon(g)

        exp_a = _Collector()
        exp_b = _Collector()
        pipe_a = TracePipeline(exporters=[exp_a])
        pipe_b = TracePipeline(exporters=[exp_b])

        async def call(x, pipe):
            return await engine.run(inputs={"x": x}, tracer=pipe)

        ra, rb = await asyncio.gather(call(1, pipe_a), call(2, pipe_b))
        assert ra["out"] == 2
        assert rb["out"] == 4

        # Each pipeline got its own events — no cross-pollination
        for events in (exp_a.events, exp_b.events):
            assert events  # non-empty
            request_ids = {e.request_id for e in events}
            assert len(request_ids) == 1, "pipeline saw events from another call"


# =============================================================================
# Media extraction at op_start / op_end (§3.8)
# =============================================================================


class TestMediaExtraction:
    @pytest.mark.asyncio
    async def test_media_in_outputs_strips_to_placeholder_with_refs(self):
        """An op returning a ``Media`` value emits OP_END with a stripped
        outputs dict + a populated ``media_refs`` list."""
        from operonx.core import Media

        @op
        def produce_audio():
            return {"audio": Media(data=b"wav_bytes", mime_type="audio/wav")}

        with GraphOp(name="g") as g:
            p = produce_audio()
            START >> p >> END
            p["audio"] >> PARENT["audio"]

        exp = _Collector()
        pipe = TracePipeline(exporters=[exp])
        engine = Operon(g, tracer=pipe)
        await engine.run(inputs={})

        ends = [e for e in exp.events if e.kind is EventKind.OP_END
                and e.op_name == "g.p"]
        assert len(ends) == 1
        end = ends[0]

        # Outputs carry the placeholder, not the raw bytes
        assert end.payload["outputs"] == {"audio": "<media:0>"}

        # Media refs carry the actual blob + correct field path
        refs = end.payload["media_refs"]
        assert len(refs) == 1
        assert refs[0].field_path == "outputs.audio"
        assert refs[0].data == b"wav_bytes"
        assert refs[0].mime_type == "audio/wav"

    @pytest.mark.asyncio
    async def test_no_media_emits_empty_refs_list(self):
        @op
        def plain():
            return {"r": 1}

        with GraphOp(name="g") as g:
            o = plain()
            START >> o >> END

        exp = _Collector()
        pipe = TracePipeline(exporters=[exp])
        engine = Operon(g, tracer=pipe)
        await engine.run(inputs={})

        ends = [e for e in exp.events if e.kind is EventKind.OP_END
                and e.op_name == "g.o"]
        assert len(ends) == 1
        assert ends[0].payload["media_refs"] == []
