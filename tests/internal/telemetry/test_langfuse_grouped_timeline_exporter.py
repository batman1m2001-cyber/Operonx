"""Unit tests for LangfuseGroupedTimelineExporter — events with GROUP_START/END
markers render as turn-grouped trace structures.

The educa shape: each turn becomes a synthetic span (parent of the trace);
ops inside that turn attach to the synthetic span instead of fanning out
flat under the trace; Aggregate-emitted summary annotations land in the
turn span's metadata.
"""

from datetime import datetime, timezone
from typing import Optional

import pytest

from operonx.core.tracing.events import EventKind, TraceEvent
from operonx.telemetry.exporters import LangfuseGroupedTimelineExporter

# =============================================================================
# Test helpers
# =============================================================================


class _MockClient:
    def __init__(self):
        self.batches: list = []
        self.host = "https://mock.local"

    def ingest(self, batch, timeout: int = 30):
        self.batches.append(batch)
        return {"successes": [{"id": e.get("id")} for e in batch], "errors": []}

    def trace_url(self, trace_id):
        return f"{self.host}/trace/{trace_id}"


def _exporter() -> tuple[LangfuseGroupedTimelineExporter, _MockClient]:
    client = _MockClient()
    exp = LangfuseGroupedTimelineExporter.__new__(LangfuseGroupedTimelineExporter)
    exp._config = None
    exp._resource = "mock:test"
    exp.tags = []
    exp.workflow_name = "operonx"
    exp._client_cache = client
    return exp, client


def _ev(
    kind: EventKind,
    op_name: Optional[str] = None,
    ctx: tuple = (),
    seq: int = 0,
    payload: dict = None,
    request_id: str = "req-1",
    timestamp: Optional[datetime] = None,
) -> TraceEvent:
    return TraceEvent(
        event_id=f"e-{seq}",
        request_id=request_id,
        kind=kind,
        op_name=op_name,
        ctx=ctx,
        timestamp=timestamp or datetime(2026, 5, 5, 12, 0, 0, tzinfo=timezone.utc),
        seq=seq,
        payload=payload or {},
    )


# =============================================================================
# Tests
# =============================================================================


class TestGroupRendering:
    def test_one_group_creates_synthetic_span(self):
        """A GROUP_START/END pair produces a span-create with the group name."""
        exp, client = _exporter()
        events = [
            _ev(EventKind.GROUP_START, seq=0, payload={"name": "turn-0"}),
            _ev(EventKind.OP_START, op_name="g.a", ctx=("main",), seq=1, payload={"inputs": {}}),
            _ev(
                EventKind.OP_END,
                op_name="g.a",
                ctx=("main",),
                seq=2,
                payload={"outputs": {"r": 1}, "status": "ok"},
            ),
            _ev(EventKind.GROUP_END, seq=3, payload={"name": "turn-0", "status": "ok"}),
        ]
        exp.export(events, "req-1", {})

        batch = client.batches[0]
        types = [e["type"] for e in batch]
        # trace-create + group span-create + op span-create
        assert types == ["trace-create", "span-create", "span-create"]

        # The first span-create is the synthetic group
        group_span = batch[1]["body"]
        assert group_span["name"] == "turn-0"
        assert group_span["traceId"] == "req-1"
        assert "endTime" in group_span

    def test_op_inside_group_links_to_group_not_trace(self):
        """The op's parentObservationId must point at the synthetic group span."""
        exp, client = _exporter()
        events = [
            _ev(EventKind.GROUP_START, seq=0, payload={"name": "turn-0"}),
            _ev(EventKind.OP_START, op_name="g.a", ctx=("main",), seq=1, payload={"inputs": {}}),
            _ev(
                EventKind.OP_END,
                op_name="g.a",
                ctx=("main",),
                seq=2,
                payload={"outputs": {}, "status": "ok"},
            ),
            _ev(EventKind.GROUP_END, seq=3, payload={"name": "turn-0"}),
        ]
        exp.export(events, "req-1", {})

        batch = client.batches[0]
        group_span = next(
            e["body"] for e in batch if e["type"] == "span-create" and e["body"]["name"] == "turn-0"
        )
        op_span = next(
            e["body"] for e in batch if e["type"] == "span-create" and e["body"]["name"] == "a"
        )
        assert op_span["parentObservationId"] == group_span["id"]

    def test_op_outside_any_group_attaches_to_trace_root(self):
        """Op without an enclosing group has no parentObservationId."""
        exp, client = _exporter()
        events = [
            _ev(EventKind.OP_START, op_name="g.solo", ctx=("main",), seq=0, payload={"inputs": {}}),
            _ev(
                EventKind.OP_END,
                op_name="g.solo",
                ctx=("main",),
                seq=1,
                payload={"outputs": {}, "status": "ok"},
            ),
        ]
        exp.export(events, "req-1", {})

        batch = client.batches[0]
        op_span = batch[1]["body"]
        assert op_span["name"] == "solo"
        assert "parentObservationId" not in op_span


class TestMultipleGroups:
    def test_two_turns_each_get_their_own_span(self):
        exp, client = _exporter()
        events = [
            _ev(EventKind.GROUP_START, seq=0, payload={"name": "turn-0"}),
            _ev(EventKind.OP_START, op_name="g.a", ctx=("main",), seq=1, payload={"inputs": {}}),
            _ev(
                EventKind.OP_END,
                op_name="g.a",
                ctx=("main",),
                seq=2,
                payload={"outputs": {}, "status": "ok"},
            ),
            _ev(EventKind.GROUP_END, seq=3, payload={"name": "turn-0"}),
            _ev(EventKind.GROUP_START, seq=4, payload={"name": "turn-1"}),
            _ev(EventKind.OP_START, op_name="g.b", ctx=("main",), seq=5, payload={"inputs": {}}),
            _ev(
                EventKind.OP_END,
                op_name="g.b",
                ctx=("main",),
                seq=6,
                payload={"outputs": {}, "status": "ok"},
            ),
            _ev(EventKind.GROUP_END, seq=7, payload={"name": "turn-1"}),
        ]
        exp.export(events, "req-1", {})

        batch = client.batches[0]
        group_spans = [
            e["body"]
            for e in batch
            if e["type"] == "span-create" and e["body"]["name"].startswith("turn-")
        ]
        assert {g["name"] for g in group_spans} == {"turn-0", "turn-1"}
        assert len({g["id"] for g in group_spans}) == 2  # distinct ids

        # Each op attaches to its own turn
        a_span = next(
            e["body"] for e in batch if e["type"] == "span-create" and e["body"]["name"] == "a"
        )
        b_span = next(
            e["body"] for e in batch if e["type"] == "span-create" and e["body"]["name"] == "b"
        )
        turn0_id = next(g["id"] for g in group_spans if g["name"] == "turn-0")
        turn1_id = next(g["id"] for g in group_spans if g["name"] == "turn-1")
        assert a_span["parentObservationId"] == turn0_id
        assert b_span["parentObservationId"] == turn1_id


class TestAnnotationSummaryOnGroup:
    def test_synthetic_annotation_lands_on_group_metadata(self):
        """ANNOTATION events with op_name=None (Aggregate summary output)
        must land on the enclosing group's metadata, not on any op."""
        exp, client = _exporter()
        events = [
            _ev(EventKind.GROUP_START, seq=0, payload={"name": "turn-0"}),
            _ev(
                EventKind.OP_START, op_name="g.audio", ctx=("main",), seq=1, payload={"inputs": {}}
            ),
            _ev(
                EventKind.OP_END,
                op_name="g.audio",
                ctx=("main",),
                seq=2,
                payload={"outputs": {}, "status": "ok"},
            ),
            # Aggregate emits annotation with op_name=None
            _ev(
                EventKind.ANNOTATION,
                op_name=None,
                ctx=(),
                seq=3,
                payload={"key": "summary:g.audio", "value": {"chunk_count": 1500, "avg_ms": 20}},
            ),
            _ev(EventKind.GROUP_END, seq=4, payload={"name": "turn-0"}),
        ]
        exp.export(events, "req-1", {})

        batch = client.batches[0]
        group_span = next(
            e["body"] for e in batch if e["type"] == "span-create" and e["body"]["name"] == "turn-0"
        )
        meta = group_span.get("metadata") or {}
        assert meta.get("summary:g.audio") == {"chunk_count": 1500, "avg_ms": 20}

    def test_op_scoped_annotation_lands_on_op_not_group(self):
        """ANNOTATION with op_name set targets the op, not the group."""
        exp, client = _exporter()
        events = [
            _ev(EventKind.GROUP_START, seq=0, payload={"name": "turn-0"}),
            _ev(EventKind.OP_START, op_name="g.x", ctx=("main",), seq=1, payload={"inputs": {}}),
            _ev(
                EventKind.ANNOTATION,
                op_name="g.x",
                ctx=("main",),
                seq=2,
                payload={"key": "user_id", "value": "u-7"},
            ),
            _ev(
                EventKind.OP_END,
                op_name="g.x",
                ctx=("main",),
                seq=3,
                payload={"outputs": {}, "status": "ok"},
            ),
            _ev(EventKind.GROUP_END, seq=4, payload={"name": "turn-0"}),
        ]
        exp.export(events, "req-1", {})

        batch = client.batches[0]
        op_span = next(
            e["body"] for e in batch if e["type"] == "span-create" and e["body"]["name"] == "x"
        )
        assert (op_span.get("metadata") or {}).get("user_id") == "u-7"
        # The group span shouldn't have user_id (it was op-scoped)
        group_span = next(
            e["body"] for e in batch if e["type"] == "span-create" and e["body"]["name"] == "turn-0"
        )
        assert "user_id" not in (group_span.get("metadata") or {})


class TestTruncatedGroupStatus:
    def test_truncated_group_gets_warning_level(self):
        """Pipeline shutdown synthesizes GROUP_END(status='truncated') for any
        groups still open. The exporter renders these with level=WARNING."""
        exp, client = _exporter()
        events = [
            _ev(EventKind.GROUP_START, seq=0, payload={"name": "turn-0"}),
            _ev(EventKind.GROUP_END, seq=1, payload={"name": "turn-0", "status": "truncated"}),
        ]
        exp.export(events, "req-1", {})

        batch = client.batches[0]
        group_span = next(
            e["body"] for e in batch if e["type"] == "span-create" and e["body"]["name"] == "turn-0"
        )
        assert group_span.get("level") == "WARNING"
