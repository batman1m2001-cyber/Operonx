"""Tests for EventEmitter, NullEmitter, and the ContextVar bindings."""

import asyncio

import pytest

from operonx.core.tracing.emitter import (
    EventEmitter,
    NullEmitter,
    _current_emitter_var,
    _current_op_var,
    current_emitter,
)
from operonx.core.tracing.events import EventKind
from operonx.core.tracing.pipeline import TracePipeline


def _new_pipeline_emitter() -> tuple[TracePipeline, EventEmitter]:
    p = TracePipeline()
    return p, p.emitter(request_id="req-test")


class TestEventEmitterBasics:
    def test_op_start_records_start_time(self):
        p, em = _new_pipeline_emitter()
        em.op_start("x", ("main",), inputs={"a": 1})
        assert em.start_time_of("x", ("main",)) > 0
        assert len(p._buffer) == 1
        assert p._buffer[0].kind is EventKind.OP_START
        assert p._buffer[0].op_name == "x"
        assert p._buffer[0].payload == {"inputs": {"a": 1}, "media_refs": []}

    def test_op_end_computes_duration_when_omitted(self):
        p, em = _new_pipeline_emitter()
        em.op_start("x", ("main",))
        em.op_end("x", ("main",), outputs={"r": 7}, status="ok")
        end = p._buffer[1]
        assert end.kind is EventKind.OP_END
        assert end.payload["status"] == "ok"
        assert end.payload["duration_ms"] >= 0

    def test_op_end_uses_explicit_duration_when_given(self):
        p, em = _new_pipeline_emitter()
        em.op_start("x", ("main",))
        em.op_end("x", ("main",), duration_ms=42.0)
        assert p._buffer[1].payload["duration_ms"] == 42.0

    def test_op_end_idempotent_second_call_dropped(self):
        """Cancel-emit (Rule 3) and the op's own finally must not double-emit."""
        p, em = _new_pipeline_emitter()
        em.op_start("x", ("main",))
        em.op_end("x", ("main",), status="cancelled")
        em.op_end("x", ("main",), status="ok")  # second call — should be ignored
        ends = [e for e in p._buffer if e.kind is EventKind.OP_END]
        assert len(ends) == 1
        assert ends[0].payload["status"] == "cancelled"

    def test_seq_monotonic(self):
        p, em = _new_pipeline_emitter()
        em.op_start("a", ())
        em.op_start("b", ())
        em.op_start("c", ())
        seqs = [e.seq for e in p._buffer]
        assert seqs == sorted(seqs)
        assert seqs == list(range(len(seqs)))

    def test_op_yield_carries_idx(self):
        p, em = _new_pipeline_emitter()
        em.op_yield("g", ("main", "[0]"), yielded={"v": 1}, idx=0)
        em.op_yield("g", ("main", "[1]"), yielded={"v": 2}, idx=1)
        ys = [e for e in p._buffer if e.kind is EventKind.OP_YIELD]
        assert [y.payload["idx"] for y in ys] == [0, 1]

    def test_llm_usage_event(self):
        p, em = _new_pipeline_emitter()
        em.llm_usage("ask", ("main",), model="gpt-4o", prompt_tokens=10,
                     completion_tokens=20, total_tokens=30, cost_usd=0.001)
        assert p._buffer[0].kind is EventKind.LLM_USAGE
        assert p._buffer[0].payload["model"] == "gpt-4o"
        assert p._buffer[0].payload["total_tokens"] == 30

    def test_media_ref_handle_only(self):
        p, em = _new_pipeline_emitter()
        em.media_ref("tts", ("main",), handle="h-123", mime="audio/wav",
                     size_bytes=4096)
        assert p._buffer[0].kind is EventKind.MEDIA_REF
        assert p._buffer[0].payload == {
            "handle": "h-123", "mime": "audio/wav", "size_bytes": 4096,
        }

    def test_group_context_manager_emits_start_and_end(self):
        p, em = _new_pipeline_emitter()
        with em.group("turn-0", turn_index=0):
            em.op_start("body", ("main",))
        kinds = [e.kind for e in p._buffer]
        assert kinds[0] is EventKind.GROUP_START
        assert kinds[-1] is EventKind.GROUP_END
        assert p._buffer[0].payload["name"] == "turn-0"
        assert p._buffer[0].payload["turn_index"] == 0
        assert p._buffer[-1].payload["status"] == "ok"


class TestAnnotateScope:
    def test_annotate_uses_current_op_var(self):
        p, em = _new_pipeline_emitter()
        token = _current_op_var.set(("inner_op", ("main", "[0]")))
        try:
            em.annotate("user_id", "u-7")
        finally:
            _current_op_var.reset(token)
        ann = [e for e in p._buffer if e.kind is EventKind.ANNOTATION][0]
        assert ann.op_name == "inner_op"
        assert ann.ctx == ("main", "[0]")
        assert ann.payload == {"key": "user_id", "value": "u-7"}

    def test_annotate_outside_scope_raises(self):
        p, em = _new_pipeline_emitter()
        with pytest.raises(LookupError):
            em.annotate("k", "v")


class TestNullEmitter:
    def test_all_methods_noop(self):
        ne = NullEmitter()
        ne.emit(object())
        ne.op_start("x", ())
        ne.op_end("x", ())
        ne.op_yield("x", (), {}, 0)
        ne.annotate("k", "v")
        ne.llm_usage("x", (), model="m")
        ne.media_ref("x", (), "h", "m", 0)
        with ne.group("g"):
            pass
        assert ne.start_time_of("x", ()) == 0.0

    def test_current_emitter_returns_null_when_unbound(self):
        # Outside any engine.start, current_emitter() must return a usable
        # NullEmitter so user code never crashes on emit.
        assert isinstance(current_emitter(), NullEmitter)

    def test_current_emitter_returns_bound_emitter(self):
        p, em = _new_pipeline_emitter()
        token = _current_emitter_var.set(em)
        try:
            assert current_emitter() is em
        finally:
            _current_emitter_var.reset(token)


class TestContextVarPropagation:
    @pytest.mark.asyncio
    async def test_emitter_propagates_through_create_task(self):
        """ContextVar must inherit across asyncio.create_task per PEP 567."""
        p, em = _new_pipeline_emitter()
        seen_id = {}

        async def child():
            seen_id["id"] = id(current_emitter())

        token = _current_emitter_var.set(em)
        try:
            await asyncio.create_task(child())
        finally:
            _current_emitter_var.reset(token)
        assert seen_id["id"] == id(em)

    @pytest.mark.asyncio
    async def test_concurrent_calls_get_independent_emitters(self):
        """Two concurrent engine.start() calls must see different emitters."""
        p1, em1 = _new_pipeline_emitter()
        p2, em2 = _new_pipeline_emitter()
        seen = {}

        async def call(label, em):
            token = _current_emitter_var.set(em)
            try:
                await asyncio.sleep(0)
                seen[label] = id(current_emitter())
            finally:
                _current_emitter_var.reset(token)

        await asyncio.gather(call("a", em1), call("b", em2))
        assert seen["a"] == id(em1)
        assert seen["b"] == id(em2)
        assert seen["a"] != seen["b"]
