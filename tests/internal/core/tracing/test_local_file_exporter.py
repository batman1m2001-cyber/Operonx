"""Tests for JsonFileExporter and end-to-end pipeline integration."""

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from operonx.core import END, PARENT, START, GraphOp, Operon, op
from operonx.core.tracing.events import EventKind, TraceEvent
from operonx.core.tracing.exporters import JsonFileExporter
from operonx.core.tracing.pipeline import TracePipeline
from operonx.core.tracing.processors import DropOps, TruncateIO


def _ev(seq: int, op_name: str = "g.x") -> TraceEvent:
    return TraceEvent(
        event_id=f"e-{seq}",
        request_id="req-test",
        kind=EventKind.OP_START,
        op_name=op_name,
        ctx=("main",),
        timestamp=datetime(2026, 5, 5, 12, 0, seq, tzinfo=timezone.utc),
        seq=seq,
        payload={"inputs": {"x": seq}},
    )


class TestJsonFileExporter:
    def test_writes_final_array_to_file(self, tmp_path: Path):
        exp = JsonFileExporter(directory=str(tmp_path))
        events = [_ev(0), _ev(1), _ev(2)]
        exp.export(events, "req-test", {"partial": False})

        path = tmp_path / "req-test.json"
        assert path.exists()
        data = json.loads(path.read_text())
        assert isinstance(data, list)
        assert len(data) == 3
        assert data[0]["event_id"] == "e-0"
        # ISO timestamp ends with Z (cross-runtime parity)
        assert data[0]["timestamp"].endswith("Z")
        # ctx serialized as list (JSON-friendly)
        assert data[0]["ctx"] == ["main"]
        # kind is the str-enum value
        assert data[0]["kind"] == "op_start"

    def test_no_op_when_events_empty(self, tmp_path: Path):
        exp = JsonFileExporter(directory=str(tmp_path))
        exp.export([], "req-test", {"partial": False})
        path = tmp_path / "req-test.json"
        assert not path.exists()

    def test_partial_then_final_consolidates(self, tmp_path: Path):
        exp = JsonFileExporter(directory=str(tmp_path))
        # Partial flush 1
        exp.export([_ev(0), _ev(1)], "req-test", {"partial": True})
        # Partial flush 2
        exp.export([_ev(2)], "req-test", {"partial": True})
        # Final flush — consolidates to a single array
        exp.export([_ev(3)], "req-test", {"partial": False})

        path = tmp_path / "req-test.json"
        data = json.loads(path.read_text())
        assert isinstance(data, list)
        seqs = [e["seq"] for e in data]
        assert seqs == [0, 1, 2, 3]

    def test_creates_directory_if_missing(self, tmp_path: Path):
        target = tmp_path / "nested" / "dir"
        exp = JsonFileExporter(directory=str(target))
        exp.export([_ev(0)], "req", {"partial": False})
        assert (target / "req.json").exists()


# =============================================================================
# End-to-end: pipeline with processors + JsonFileExporter
# =============================================================================


@op
def double(x: int):
    return {"result": x * 2}


class TestEndToEndPipeline:
    @pytest.mark.asyncio
    async def test_full_pipeline_through_processors_to_file(self, tmp_path: Path):
        """events → DropOps → TruncateIO → JsonFileExporter → file."""
        exporter = JsonFileExporter(directory=str(tmp_path))
        pipeline = TracePipeline(
            processors=[
                DropOps(["g.skip_me"]),
                TruncateIO(max_bytes=100),
            ],
            exporters=[exporter],
        )

        with GraphOp(name="g") as g:
            d = double(x=PARENT["x"])
            START >> d >> END
            d["result"] >> PARENT["out"]

        engine = Operon(g, tracer=pipeline)
        result = await engine.run(inputs={"x": 5})
        assert result["out"] == 10

        # File written on final flush
        files = list(tmp_path.glob("*.json"))
        assert len(files) == 1
        data = json.loads(files[0].read_text())
        op_names = {e.get("op_name") for e in data if e.get("op_name")}
        assert "g.d" in op_names
        # No g.skip_me events (none emitted, but processor is also wired)
        assert "g.skip_me" not in op_names
