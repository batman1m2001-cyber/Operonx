"""Unit tests for `operonx.core.workflow_trace` — V3 tracing primitives.

Covers the data-shape contract only. Engine wiring is tested in
`test_workflow_trace_recording.py` (Step 2).
"""

from __future__ import annotations

from dataclasses import asdict

import pytest

from operonx.core.workflow_trace import (
    STATUS_ERROR,
    STATUS_OK,
    OpExecution,
    UpstreamRef,
    WorkflowTrace,
    all_edges,
    format_ctx,
    make_op_id,
)


def _u(from_op="op-01", from_name="src", from_full="engine.src", from_key="out", to_key="in"):
    """Terse UpstreamRef factory — keyword-friendly."""
    return UpstreamRef(
        from_op_id=from_op,
        from_op_name=from_name,
        from_op_full_name=from_full,
        from_key=from_key,
        to_key=to_key,
    )


def _mkexec(op_id, op_name, ctx=("main",), upstreams=None, start=0.0, end=0.0, op_full_name=None):
    return OpExecution(
        op_id=op_id,
        op_name=op_name,
        op_full_name=op_full_name or f"engine.{op_name}",
        ctx=ctx,
        start_time=start,
        end_time=end,
        inputs={},
        outputs={},
        upstreams=upstreams or [],
    )


# ============================================================
# ctx / op_id helpers
# ============================================================


class TestFormatCtx:
    def test_root_ctx(self):
        assert format_ctx(("main",)) == "main"

    def test_streaming_yield(self):
        assert format_ctx(("main", "[3]")) == "main.[3]"

    def test_nested_streaming(self):
        assert format_ctx(("main", "[1]", "[0]")) == "main.[1].[0]"


class TestMakeOpId:
    def test_deterministic(self):
        assert make_op_id("engine.classify", ("main", "[1]")) == "engine.classify#main.[1]"

    def test_matches_downstream_lookup(self):
        """Producer at ctx=("main","[3]") gets same id downstream ref."""
        producer_id = make_op_id("engine.stt", ("main", "[3]"))
        downstream_ref = make_op_id("engine.stt", ("main", "[3]"))
        assert producer_id == downstream_ref


# ============================================================
# UpstreamRef / OpExecution — dataclass shape
# ============================================================


class TestUpstreamRef:
    def test_five_fields(self):
        u = UpstreamRef(
            from_op_id="engine.stt#main.[1]",
            from_op_name="stt",
            from_op_full_name="engine.stt",
            from_key="TRANSCRIPT",
            to_key="audio_text",
        )
        assert u.from_op_id == "engine.stt#main.[1]"
        assert u.from_op_name == "stt"
        assert u.from_op_full_name == "engine.stt"
        assert u.from_key == "TRANSCRIPT"
        assert u.to_key == "audio_text"


class TestOpExecution:
    def test_minimal_construction(self):
        e = OpExecution(
            op_id="engine.classify#main.[1]",
            op_name="classify",
            op_full_name="engine.classify",
            ctx=("main", "[1]"),
            start_time=1.0,
            end_time=1.2,
            inputs={"state": "MAIN"},
            outputs={"intent": "affirm"},
        )
        assert e.upstreams == []
        assert e.status == STATUS_OK
        assert e.error is None

    def test_local_and_full_name_are_distinct(self):
        e = _mkexec("x", "classify")
        assert e.op_name == "classify"
        assert e.op_full_name == "engine.classify"

    def test_duration_ms(self):
        e = _mkexec("x", "x", start=1.0, end=1.25)
        assert e.duration_ms == pytest.approx(250.0)

    def test_error_status_carries_traceback(self):
        e = OpExecution(
            op_id="x",
            op_name="x",
            op_full_name="engine.x",
            ctx=("main",),
            start_time=0.0,
            end_time=0.1,
            inputs={},
            outputs={},
            status=STATUS_ERROR,
            error="RuntimeError: boom\n  at foo.py:10",
        )
        assert e.status == STATUS_ERROR
        assert "RuntimeError" in (e.error or "")

    def test_asdict_round_trip(self):
        """Serializes cleanly — required for JSONL / Langfuse consumers."""
        e = OpExecution(
            op_id="engine.stt#main.[1]",
            op_name="stt",
            op_full_name="engine.stt",
            ctx=("main", "[1]"),
            start_time=4.064,
            end_time=4.175,
            inputs={"AUDIO_SIGNAL": [0.1, 0.2]},
            outputs={"TRANSCRIPT": "hello"},
            upstreams=[
                _u(
                    from_op="engine.prepare#main.[1]",
                    from_name="prepare",
                    from_full="engine.prepare",
                    from_key="speech_audio",
                    to_key="AUDIO_SIGNAL",
                )
            ],
        )
        d = asdict(e)
        assert d["op_name"] == "stt"
        assert d["op_full_name"] == "engine.stt"
        assert d["ctx"] == ("main", "[1]")
        assert d["upstreams"][0]["from_op_full_name"] == "engine.prepare"


# ============================================================
# WorkflowTrace — container + helpers
# ============================================================


class TestWorkflowTrace:
    def test_empty_defaults(self):
        t = WorkflowTrace(
            trace_id="t1",
            workflow_name="callbot",
            started_at=0.0,
            ended_at=1.0,
        )
        assert t.nodes == []
        assert t.metadata == {}
        assert t.duration_ms == pytest.approx(1000.0)

    def test_by_op_filters_by_name(self):
        t = WorkflowTrace(
            trace_id="t1",
            workflow_name="w",
            started_at=0.0,
            ended_at=1.0,
            nodes=[
                _mkexec("op-01", "stt"),
                _mkexec("op-02", "classify"),
                _mkexec("op-03", "stt"),  # second stt (streaming)
            ],
        )
        stt_execs = t.by_op("stt")
        assert [e.op_id for e in stt_execs] == ["op-01", "op-03"]
        assert t.by_op("nope") == []

    def test_roots_have_no_upstreams(self):
        n1 = _mkexec("op-01", "src")
        n2 = _mkexec("op-02", "downstream", upstreams=[_u(from_op="op-01", from_name="src")])
        t = WorkflowTrace(
            trace_id="t",
            workflow_name="w",
            started_at=0.0,
            ended_at=1.0,
            nodes=[n1, n2],
        )
        assert [n.op_id for n in t.roots()] == ["op-01"]

    def test_leaves_are_ops_no_one_consumed(self):
        # DAG: A ──> B ──> C   (A and B have downstream consumers, C is a leaf)
        a = _mkexec("op-A", "A")
        b = _mkexec("op-B", "B", upstreams=[_u(from_op="op-A", from_name="A")])
        c = _mkexec("op-C", "C", upstreams=[_u(from_op="op-B", from_name="B")])
        t = WorkflowTrace(
            trace_id="t",
            workflow_name="w",
            started_at=0.0,
            ended_at=1.0,
            nodes=[a, b, c],
        )
        assert [n.op_id for n in t.leaves()] == ["op-C"]

    def test_leaves_returns_multiple_when_fanout(self):
        # DAG: A ──┬──> B (leaf)
        #         └──> C (leaf)
        a = _mkexec("op-A", "A")
        b = _mkexec("op-B", "B", upstreams=[_u(from_op="op-A", from_name="A")])
        c = _mkexec("op-C", "C", upstreams=[_u(from_op="op-A", from_name="A")])
        t = WorkflowTrace(
            trace_id="t",
            workflow_name="w",
            started_at=0.0,
            ended_at=1.0,
            nodes=[a, b, c],
        )
        assert sorted(n.op_id for n in t.leaves()) == ["op-B", "op-C"]


# ============================================================
# all_edges — the "edges.jsonl is redundant" proof
# ============================================================


class TestAllEdges:
    def test_empty_when_no_upstreams(self):
        nodes = [_mkexec("op-01", "src"), _mkexec("op-02", "src2")]
        assert all_edges(nodes) == []

    def test_flattens_all_upstream_refs(self):
        # A ──> B (2 inputs into B, both from A)
        a = _mkexec("op-A", "A")
        b = _mkexec(
            "op-B",
            "B",
            upstreams=[
                _u(from_op="op-A", from_name="A", from_key="out1", to_key="in1"),
                _u(from_op="op-A", from_name="A", from_key="out2", to_key="in2"),
            ],
        )
        edges = all_edges([a, b])
        assert set(edges) == {
            ("op-A", "out1", "op-B", "in1"),
            ("op-A", "out2", "op-B", "in2"),
        }

    def test_multi_upstream_aggregator(self):
        # Real callbot pattern: pick_transcript with N upstreams.
        u1 = _mkexec("op-src", "frame_source")
        u2 = _mkexec("op-stt", "stt")
        u3 = _mkexec("op-noop", "noop_stt")
        picker = _mkexec(
            "op-pick",
            "pick_transcript",
            upstreams=[
                _u(from_op="op-src", from_name="frame_source", from_key="kind", to_key="kind"),
                _u(from_op="op-stt", from_name="stt", from_key="TRANSCRIPT", to_key="audio_text"),
                _u(
                    from_op="op-noop",
                    from_name="noop_stt",
                    from_key="skip_transcript",
                    to_key="skip_text",
                ),
            ],
        )
        edges = all_edges([u1, u2, u3, picker])
        assert len(edges) == 3
        assert all(dst_id == "op-pick" for _, _, dst_id, _ in edges)
