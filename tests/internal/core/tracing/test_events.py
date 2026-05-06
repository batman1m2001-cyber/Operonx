"""Tests for TraceEvent + EventKind data types (new event-stream tracing)."""

from datetime import datetime, timedelta, timezone

import pytest

from operonx.core.tracing.events import EventKind, TraceEvent


def _ev(seq: int = 0, ts: datetime = None, **overrides) -> TraceEvent:
    """Build a TraceEvent with sensible defaults for testing ordering."""
    base = dict(
        event_id=f"evt-{seq}",
        request_id="req-1",
        kind=EventKind.OP_START,
        op_name="x",
        ctx=("main",),
        timestamp=ts or datetime(2026, 5, 5, 12, 0, 0, tzinfo=timezone.utc),
        seq=seq,
        payload={},
    )
    base.update(overrides)
    return TraceEvent(**base)


class TestTraceEvent:
    def test_construction_minimal(self):
        e = _ev()
        assert e.kind is EventKind.OP_START
        assert e.payload == {}

    def test_immutable_frozen(self):
        e = _ev()
        with pytest.raises(Exception):
            e.kind = EventKind.OP_END

    def test_payload_default_is_empty_dict(self):
        e = TraceEvent(
            event_id="x",
            request_id="r",
            kind=EventKind.OP_START,
            op_name="op",
            ctx=(),
            timestamp=datetime.now(timezone.utc),
            seq=0,
        )
        assert e.payload == {}

    def test_lt_orders_by_timestamp_first(self):
        t0 = datetime(2026, 5, 5, 12, 0, 0, tzinfo=timezone.utc)
        t1 = t0 + timedelta(microseconds=1)
        a = _ev(seq=99, ts=t0)
        b = _ev(seq=0, ts=t1)
        assert a < b  # earlier timestamp wins regardless of seq

    def test_lt_breaks_ties_by_seq(self):
        t = datetime(2026, 5, 5, 12, 0, 0, tzinfo=timezone.utc)
        a = _ev(seq=0, ts=t)
        b = _ev(seq=1, ts=t)
        assert a < b

    def test_sortable(self):
        t = datetime(2026, 5, 5, 12, 0, 0, tzinfo=timezone.utc)
        out = sorted([_ev(seq=2, ts=t), _ev(seq=0, ts=t), _ev(seq=1, ts=t)])
        assert [e.seq for e in out] == [0, 1, 2]


class TestEventKind:
    def test_string_value_for_serialization(self):
        # str enum so events round-trip through JSON cleanly.
        assert EventKind.OP_START.value == "op_start"
        assert EventKind.OP_END.value == "op_end"
        assert EventKind.OP_YIELD.value == "op_yield"
        assert EventKind.ANNOTATION.value == "annotation"
        assert EventKind.GROUP_START.value == "group_start"
        assert EventKind.GROUP_END.value == "group_end"
        assert EventKind.LLM_USAGE.value == "llm_usage"
        assert EventKind.MEDIA_REF.value == "media_ref"

    def test_kind_is_str_subclass(self):
        # str(...) == ... so dict lookups by str work
        assert EventKind.OP_START == "op_start"
