"""Tests for the processor library — DropOps, KeepOps, DropKinds, DropEmpty,
TruncateIO, RedactKeys, Sample, GroupBy, Aggregate.

Each processor is a generator transformation; we feed it a hand-built event
list and assert the output. Pipeline integration is covered in test_pipeline.py.
"""

from datetime import datetime, timezone
from typing import Optional

import pytest

from operonx.core.tracing.events import EventKind, TraceEvent
from operonx.core.tracing.processors import (
    Aggregate,
    DropEmpty,
    DropKinds,
    DropOps,
    GroupBy,
    KeepOps,
    RedactKeys,
    Sample,
    TruncateIO,
)


def _ev(
    kind: EventKind,
    op_name: Optional[str] = None,
    seq: int = 0,
    payload: dict = None,
    request_id: str = "req-1",
) -> TraceEvent:
    """Build a TraceEvent with reasonable defaults."""
    return TraceEvent(
        event_id=f"e-{seq}",
        request_id=request_id,
        kind=kind,
        op_name=op_name,
        ctx=("main",) if op_name else (),
        timestamp=datetime(2026, 5, 5, 12, 0, 0, tzinfo=timezone.utc),
        seq=seq,
        payload=payload or {},
    )


# =============================================================================
# DropOps / KeepOps
# =============================================================================


class TestDropOps:
    def test_drops_matching_full_name(self):
        events = [
            _ev(EventKind.OP_START, op_name="g.picker"),
            _ev(EventKind.OP_START, op_name="g.real"),
        ]
        out = list(DropOps(["g.picker"])(events))
        assert [e.op_name for e in out] == ["g.real"]

    def test_drops_matching_short_name(self):
        events = [
            _ev(EventKind.OP_START, op_name="g.picker"),
            _ev(EventKind.OP_START, op_name="g.real"),
        ]
        out = list(DropOps(["picker"])(events))
        assert [e.op_name for e in out] == ["g.real"]

    def test_passes_synthetic_events(self):
        # GROUP_START / GROUP_END have op_name=None — should pass through
        events = [_ev(EventKind.GROUP_START), _ev(EventKind.OP_START, op_name="g.x")]
        out = list(DropOps(["g.x"])(events))
        assert [e.kind for e in out] == [EventKind.GROUP_START]


class TestKeepOps:
    def test_keeps_only_matching(self):
        events = [
            _ev(EventKind.OP_START, op_name="g.a"),
            _ev(EventKind.OP_START, op_name="g.b"),
            _ev(EventKind.OP_START, op_name="g.c"),
        ]
        out = list(KeepOps(["g.a", "g.c"])(events))
        assert [e.op_name for e in out] == ["g.a", "g.c"]

    def test_passes_synthetic_events(self):
        events = [_ev(EventKind.GROUP_START), _ev(EventKind.OP_START, op_name="g.x")]
        out = list(KeepOps(["g.y"])(events))
        # GROUP_START passes (synthetic, no op_name); OP_START dropped (not in keep)
        assert [e.kind for e in out] == [EventKind.GROUP_START]


# =============================================================================
# DropKinds
# =============================================================================


class TestDropKinds:
    def test_drops_specified_kinds(self):
        events = [
            _ev(EventKind.OP_START, op_name="g.a"),
            _ev(EventKind.OP_YIELD, op_name="g.a"),
            _ev(EventKind.OP_END, op_name="g.a"),
        ]
        out = list(DropKinds([EventKind.OP_YIELD])(events))
        assert [e.kind for e in out] == [EventKind.OP_START, EventKind.OP_END]


# =============================================================================
# DropEmpty
# =============================================================================


class TestDropEmpty:
    def test_drops_op_end_with_empty_outputs(self):
        events = [
            _ev(EventKind.OP_END, op_name="g.a", payload={"outputs": {}, "status": "ok"}),
            _ev(EventKind.OP_END, op_name="g.b", payload={"outputs": {"r": 1}, "status": "ok"}),
        ]
        out = list(DropEmpty()(events))
        assert [e.op_name for e in out] == ["g.b"]

    def test_drops_op_end_with_all_none_outputs(self):
        events = [
            _ev(EventKind.OP_END, op_name="g.a", payload={"outputs": {"x": None}, "status": "ok"}),
            _ev(EventKind.OP_END, op_name="g.b", payload={"outputs": {"x": 1}, "status": "ok"}),
        ]
        out = list(DropEmpty()(events))
        assert [e.op_name for e in out] == ["g.b"]

    def test_keeps_other_kinds_unchanged(self):
        events = [_ev(EventKind.OP_START, op_name="g.a")]
        assert list(DropEmpty()(events)) == events


# =============================================================================
# TruncateIO
# =============================================================================


class TestTruncateIO:
    def test_truncates_long_string_in_inputs(self):
        long = "x" * 5000
        events = [_ev(EventKind.OP_START, op_name="g.a", payload={"inputs": {"text": long}})]
        out = list(TruncateIO(max_bytes=2000)(events))
        truncated = out[0].payload["inputs"]["text"]
        assert truncated.startswith("xxx")
        assert "[truncated 3000 bytes]" in truncated
        assert len(truncated) < 5000

    def test_passes_short_values_unchanged(self):
        events = [_ev(EventKind.OP_START, op_name="g.a", payload={"inputs": {"text": "short"}})]
        out = list(TruncateIO(max_bytes=2000)(events))
        assert out[0].payload["inputs"]["text"] == "short"

    def test_truncates_outputs_and_yielded(self):
        long = "y" * 3000
        events = [_ev(EventKind.OP_END, op_name="g.a", payload={"outputs": {"r": long}})]
        out = list(TruncateIO(max_bytes=1000)(events))
        assert "[truncated" in out[0].payload["outputs"]["r"]


# =============================================================================
# RedactKeys
# =============================================================================


class TestRedactKeys:
    def test_redacts_matching_keys(self):
        events = [
            _ev(
                EventKind.OP_START,
                op_name="g.a",
                payload={"inputs": {"phone": "0900-123-456", "name": "alice"}},
            )
        ]
        out = list(RedactKeys(["phone"])(events))
        assert out[0].payload["inputs"]["phone"] == "<redacted>"
        assert out[0].payload["inputs"]["name"] == "alice"

    def test_custom_marker(self):
        events = [
            _ev(EventKind.OP_START, op_name="g.a", payload={"inputs": {"api_key": "sk-secret"}})
        ]
        out = list(RedactKeys(["api_key"], marker="***")(events))
        assert out[0].payload["inputs"]["api_key"] == "***"


# =============================================================================
# Sample
# =============================================================================


class TestSample:
    def test_rate_one_keeps_all(self):
        events = [_ev(EventKind.OP_START, request_id=f"r-{i}") for i in range(10)]
        out = list(Sample(rate=1.0)(events))
        assert len(out) == 10

    def test_rate_zero_drops_all(self):
        events = [_ev(EventKind.OP_START, request_id=f"r-{i}") for i in range(10)]
        out = list(Sample(rate=0.0)(events))
        assert out == []

    def test_decisions_stable_per_request_id(self):
        # Same request_id should always be kept-or-dropped consistently
        events_run1 = [_ev(EventKind.OP_START, request_id="my-req")] * 5
        events_run2 = [_ev(EventKind.OP_START, request_id="my-req")] * 5
        out1 = list(Sample(rate=0.5)(events_run1))
        out2 = list(Sample(rate=0.5)(events_run2))
        # Either both empty or both length 5
        assert (len(out1) == 0 and len(out2) == 0) or (len(out1) == 5 and len(out2) == 5)

    def test_invalid_rate_raises(self):
        with pytest.raises(ValueError):
            Sample(rate=1.5)
        with pytest.raises(ValueError):
            Sample(rate=-0.1)


# =============================================================================
# GroupBy
# =============================================================================


class TestGroupBy:
    def test_emits_start_and_end_around_first_group(self):
        events = [
            _ev(EventKind.OP_START, op_name="g.a", seq=0),
            _ev(EventKind.OP_END, op_name="g.a", seq=1, payload={"status": "ok"}),
        ]
        # Boundary: OP_END for g.a closes the group
        is_boundary = lambda e: e.kind is EventKind.OP_END and e.op_name == "g.a"
        out = list(GroupBy(boundary=is_boundary)(events))
        kinds = [e.kind for e in out]
        # Expect: GROUP_START, OP_START, OP_END, GROUP_END
        assert kinds == [
            EventKind.GROUP_START,
            EventKind.OP_START,
            EventKind.OP_END,
            EventKind.GROUP_END,
        ]
        assert out[0].payload["name"] == "group-0"
        assert out[-1].payload["status"] == "ok"

    def test_multiple_groups(self):
        events = [
            _ev(EventKind.OP_START, op_name="g.a", seq=0),
            _ev(EventKind.OP_END, op_name="g.a", seq=1),  # boundary
            _ev(EventKind.OP_START, op_name="g.b", seq=2),
            _ev(EventKind.OP_END, op_name="g.a", seq=3),  # boundary
        ]
        is_boundary = lambda e: e.kind is EventKind.OP_END and e.op_name == "g.a"
        out = list(GroupBy(boundary=is_boundary, name_fn=lambda i: f"turn-{i}")(events))
        # Two complete groups, no trailing
        starts = [e for e in out if e.kind is EventKind.GROUP_START]
        ends = [e for e in out if e.kind is EventKind.GROUP_END]
        assert [s.payload["name"] for s in starts] == ["turn-0", "turn-1"]
        assert [e.payload["name"] for e in ends] == ["turn-0", "turn-1"]
        assert all(e.payload["status"] == "ok" for e in ends)

    def test_trailing_group_truncated_at_stream_end(self):
        events = [
            _ev(EventKind.OP_START, op_name="g.a", seq=0),
            _ev(EventKind.OP_END, op_name="g.a", seq=1),  # boundary closes turn-0
            _ev(EventKind.OP_START, op_name="g.b", seq=2),  # opens turn-1, no boundary
        ]
        is_boundary = lambda e: e.kind is EventKind.OP_END and e.op_name == "g.a"
        out = list(GroupBy(boundary=is_boundary, name_fn=lambda i: f"turn-{i}")(events))
        ends = [e for e in out if e.kind is EventKind.GROUP_END]
        statuses = [e.payload["status"] for e in ends]
        # turn-0 closed normally; turn-1 truncated at stream end
        assert statuses == ["ok", "truncated"]


# =============================================================================
# Aggregate
# =============================================================================


class TestAggregate:
    def test_streams_running_tally_constant_memory(self):
        """Folds 5 events into a single ANNOTATION; never holds the list."""
        events = [
            _ev(EventKind.GROUP_START, seq=0, payload={"name": "turn-0"}),
            _ev(
                EventKind.OP_YIELD,
                op_name="g.audio",
                seq=1,
                payload={"yielded": {"duration_ms": 20}, "idx": 0},
            ),
            _ev(
                EventKind.OP_YIELD,
                op_name="g.audio",
                seq=2,
                payload={"yielded": {"duration_ms": 25}, "idx": 1},
            ),
            _ev(
                EventKind.OP_YIELD,
                op_name="g.audio",
                seq=3,
                payload={"yielded": {"duration_ms": 30}, "idx": 2},
            ),
            _ev(EventKind.GROUP_END, seq=4, payload={"name": "turn-0", "status": "ok"}),
        ]

        agg = Aggregate(
            op_name="g.audio",
            init=lambda: {"count": 0, "total": 0},
            reduce=lambda s, e: {
                "count": s["count"] + 1,
                "total": s["total"] + e.payload["yielded"]["duration_ms"],
            },
            emit=lambda s: {"count": s["count"], "avg": s["total"] / max(s["count"], 1)},
        )
        out = list(agg(events))

        # Audio events are CONSUMED. Output: GROUP_START, ANNOTATION, GROUP_END
        kinds = [e.kind for e in out]
        assert kinds == [EventKind.GROUP_START, EventKind.ANNOTATION, EventKind.GROUP_END]
        # Annotation carries the summary
        ann = out[1]
        assert ann.payload["key"] == "summary:g.audio"
        assert ann.payload["value"] == {"count": 3, "avg": 25.0}

    def test_short_name_match(self):
        """Aggregate(op_name='audio') should match 'g.audio' too."""
        events = [
            _ev(EventKind.GROUP_START, seq=0, payload={"name": "t-0"}),
            _ev(EventKind.OP_YIELD, op_name="g.audio", seq=1, payload={"yielded": {}, "idx": 0}),
            _ev(EventKind.GROUP_END, seq=2, payload={"name": "t-0", "status": "ok"}),
        ]
        agg = Aggregate(
            op_name="audio",  # short name
            init=lambda: {"n": 0},
            reduce=lambda s, _e: {"n": s["n"] + 1},
            emit=lambda s: s,
        )
        out = list(agg(events))
        ann = next(e for e in out if e.kind is EventKind.ANNOTATION)
        assert ann.payload["value"] == {"n": 1}

    def test_passes_through_non_target_events(self):
        """Aggregate(op_name='audio') doesn't consume vad events."""
        events = [
            _ev(EventKind.GROUP_START, seq=0, payload={"name": "t"}),
            _ev(EventKind.OP_YIELD, op_name="g.audio", seq=1, payload={"yielded": {}, "idx": 0}),
            _ev(
                EventKind.OP_YIELD,
                op_name="g.vad",
                seq=2,
                payload={"yielded": {"speech": True}, "idx": 0},
            ),
            _ev(EventKind.GROUP_END, seq=3, payload={"name": "t", "status": "ok"}),
        ]
        agg = Aggregate(
            op_name="audio",
            init=lambda: {"n": 0},
            reduce=lambda s, _e: {"n": s["n"] + 1},
            emit=lambda s: s,
        )
        out = list(agg(events))
        # vad event passes through unchanged; audio consumed
        op_names = [e.op_name for e in out if e.kind is EventKind.OP_YIELD]
        assert op_names == ["g.vad"]

    def test_passes_events_outside_window_through(self):
        """Events with op_name='audio' OUTSIDE any active group must pass through
        (Aggregate only consumes inside the window)."""
        events = [
            _ev(
                EventKind.OP_YIELD, op_name="g.audio", seq=0, payload={"yielded": {}, "idx": 0}
            ),  # before any group
            _ev(EventKind.GROUP_START, seq=1, payload={"name": "t"}),
            _ev(
                EventKind.OP_YIELD, op_name="g.audio", seq=2, payload={"yielded": {}, "idx": 1}
            ),  # inside — consumed
            _ev(EventKind.GROUP_END, seq=3, payload={"name": "t", "status": "ok"}),
        ]
        agg = Aggregate(
            op_name="g.audio",
            init=lambda: {"n": 0},
            reduce=lambda s, _e: {"n": s["n"] + 1},
            emit=lambda s: s,
        )
        out = list(agg(events))
        # First audio event passes (outside window); second is consumed
        audio_yields = [e for e in out if e.kind is EventKind.OP_YIELD and e.op_name == "g.audio"]
        assert len(audio_yields) == 1
        # Annotation only counts the in-window event
        ann = next(e for e in out if e.kind is EventKind.ANNOTATION)
        assert ann.payload["value"] == {"n": 1}

    def test_invalid_window_raises(self):
        with pytest.raises(NotImplementedError):
            Aggregate(op_name="g.x", window="time")


# =============================================================================
# Composition — GroupBy + Aggregate together (educa shape)
# =============================================================================


class TestProcessorComposition:
    def test_groupby_then_aggregate_replicates_turn_summary(self):
        """End-to-end: GroupBy detects turns, Aggregate summarizes audio per turn."""
        # Stream: 3 audio yields, then asr_result OP_END (boundary)
        events = [
            _ev(
                EventKind.OP_YIELD,
                op_name="g.audio",
                seq=0,
                payload={"yielded": {"duration_ms": 20}, "idx": 0},
            ),
            _ev(
                EventKind.OP_YIELD,
                op_name="g.audio",
                seq=1,
                payload={"yielded": {"duration_ms": 22}, "idx": 1},
            ),
            _ev(
                EventKind.OP_YIELD,
                op_name="g.audio",
                seq=2,
                payload={"yielded": {"duration_ms": 18}, "idx": 2},
            ),
            _ev(EventKind.OP_END, op_name="g.asr_result", seq=3, payload={"status": "ok"}),
        ]

        is_turn_boundary = lambda e: e.kind is EventKind.OP_END and e.op_name == "g.asr_result"
        groupby = GroupBy(boundary=is_turn_boundary, name_fn=lambda i: f"turn-{i}")
        aggregate = Aggregate(
            op_name="g.audio",
            init=lambda: {"count": 0, "total_ms": 0, "max_ms": 0},
            reduce=lambda s, e: {
                "count": s["count"] + 1,
                "total_ms": s["total_ms"] + e.payload["yielded"]["duration_ms"],
                "max_ms": max(s["max_ms"], e.payload["yielded"]["duration_ms"]),
            },
            emit=lambda s: {
                "chunk_count": s["count"],
                "avg_ms": s["total_ms"] / max(s["count"], 1),
                "max_ms": s["max_ms"],
            },
        )

        # Pipe events through both processors
        stream = events
        for proc in [groupby, aggregate]:
            stream = list(proc(stream))

        # Expect: GROUP_START, ANNOTATION, OP_END(asr_result), GROUP_END
        kinds = [e.kind for e in stream]
        assert EventKind.GROUP_START in kinds
        assert EventKind.ANNOTATION in kinds
        assert EventKind.GROUP_END in kinds

        ann = next(e for e in stream if e.kind is EventKind.ANNOTATION)
        assert ann.payload["key"] == "summary:g.audio"
        assert ann.payload["value"] == {"chunk_count": 3, "avg_ms": 20.0, "max_ms": 22}
