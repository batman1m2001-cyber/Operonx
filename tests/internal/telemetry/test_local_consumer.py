"""LocalConsumer tests — writes files, offloads media, atomic rename.

Includes a **golden-file** test for `view.txt` rendering. If you
intentionally change the render format, update
`tests/internal/telemetry/golden/local_consumer_view.txt`.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from operonx.core.workflow_trace import (
    STATUS_OK,
    OpExecution,
    UpstreamRef,
    WorkflowTrace,
)
from operonx.telemetry.consumers.local import LocalConsumer

GOLDEN = Path(__file__).parent / "golden" / "local_consumer_view.txt"


def _mkexec(op_id, op_name, start, end, ctx=("main",), inputs=None, outputs=None, upstreams=None):
    return OpExecution(
        op_id=op_id,
        op_name=op_name,
        op_full_name=f"engine.{op_name}",
        ctx=ctx,
        start_time=start,
        end_time=end,
        inputs=inputs or {},
        outputs=outputs or {},
        upstreams=upstreams or [],
        status=STATUS_OK,
    )


def _u(from_op_id, from_name, from_key, to_key):
    return UpstreamRef(
        from_op_id=from_op_id,
        from_op_name=from_name,
        from_op_full_name=f"engine.{from_name}",
        from_key=from_key,
        to_key=to_key,
    )


@pytest.fixture
def synthetic_trace():
    """Fixed-timing synthetic trace so timing values in the golden file
    are deterministic. Two "turns" (yield ctx varies) with a simple
    linear chain per turn."""
    started = 100.000
    nodes = [
        # Turn 0 — greeting only
        _mkexec(
            "engine.greet#main.[0]",
            "greet",
            start=100.001,
            end=100.005,
            ctx=("main", "[0]"),
            inputs={"text": "hello"},
            outputs={"audio_len": 128},
        ),
        # Turn 1 — customer utterance → classify → tts
        _mkexec(
            "engine.stt#main.[1]",
            "stt",
            start=101.000,
            end=101.100,
            ctx=("main", "[1]"),
            inputs={"audio_ms": 2140.0},
            outputs={"text": "alo em nghe"},
            upstreams=[_u("engine.src#main", "src", "audio", "audio_ms")],
        ),
        _mkexec(
            "engine.classify#main.[1]",
            "classify",
            start=101.100,
            end=101.300,
            ctx=("main", "[1]"),
            inputs={"state": "MAIN", "text": "alo em nghe"},
            outputs={"intent": "affirm"},
            upstreams=[_u("engine.stt#main.[1]", "stt", "text", "text")],
        ),
        _mkexec(
            "engine.tts#main.[1]",
            "tts",
            start=101.310,
            end=101.560,
            ctx=("main", "[1]"),
            inputs={"text": "Dạ vâng ạ"},
            outputs={"duration_ms": 850},
            upstreams=[_u("engine.classify#main.[1]", "classify", "intent", "text")],
        ),
    ]
    return WorkflowTrace(
        trace_id="t-123",
        workflow_name="callbot",
        started_at=started,
        ended_at=101.600,
        nodes=nodes,
        metadata={
            "request_id": "t-123",
            "user_id": "cust-1",
            "session_id": "0912345678",
        },
    )


# ============================================================
# File layout — 3 files + media/
# ============================================================


class TestFileLayout:
    def test_writes_expected_files(self, tmp_path, synthetic_trace):
        c = LocalConsumer(config={"root": tmp_path})
        out = c.consume(synthetic_trace)
        assert out == tmp_path / "t-123"
        assert (out / "meta.json").exists()
        assert (out / "nodes.jsonl").exists()
        assert (out / "view.txt").exists()
        assert (out / "media").is_dir()

    def test_no_tmp_dir_after_success(self, tmp_path, synthetic_trace):
        LocalConsumer(config={"root": tmp_path}).consume(synthetic_trace)
        assert not (tmp_path / "t-123.tmp").exists()

    def test_latest_symlink_updated(self, tmp_path, synthetic_trace):
        LocalConsumer(config={"root": tmp_path}).consume(synthetic_trace)
        latest = tmp_path / "latest"
        if latest.is_symlink():  # FS may not support symlinks
            assert latest.resolve().name == "t-123"

    def test_repeat_trace_id_replaces(self, tmp_path, synthetic_trace):
        """Re-running the same trace_id overwrites cleanly."""
        c = LocalConsumer(config={"root": tmp_path})
        c.consume(synthetic_trace)
        c.consume(synthetic_trace)  # second run
        # Only one trace dir exists (no leftover tmp).
        entries = {p.name for p in tmp_path.iterdir()}
        assert "t-123.tmp" not in entries


# ============================================================
# nodes.jsonl — programmatic form
# ============================================================


class TestNodesJsonl:
    def test_one_line_per_node(self, tmp_path, synthetic_trace):
        LocalConsumer(config={"root": tmp_path}).consume(synthetic_trace)
        lines = (tmp_path / "t-123" / "nodes.jsonl").read_text().splitlines()
        assert len(lines) == len(synthetic_trace.nodes)

    def test_each_row_is_valid_json(self, tmp_path, synthetic_trace):
        LocalConsumer(config={"root": tmp_path}).consume(synthetic_trace)
        for line in (tmp_path / "t-123" / "nodes.jsonl").read_text().splitlines():
            row = json.loads(line)
            assert "op_id" in row and "op_name" in row and "ctx" in row

    def test_upstreams_preserved_inline(self, tmp_path, synthetic_trace):
        LocalConsumer(config={"root": tmp_path}).consume(synthetic_trace)
        rows = [
            json.loads(line)
            for line in (tmp_path / "t-123" / "nodes.jsonl").read_text().splitlines()
        ]
        classify = next(r for r in rows if r["op_name"] == "classify")
        assert len(classify["upstreams"]) == 1
        assert classify["upstreams"][0]["from_op_name"] == "stt"

    def test_media_offloaded_as_ref(self, tmp_path):
        """Large bytes in inputs get replaced with $media_ref tokens."""
        node = _mkexec(
            "engine.stt#main",
            "stt",
            start=0.0,
            end=0.1,
            inputs={"audio": b"X" * 4096},
            outputs={"text": "hi"},
        )
        trace = WorkflowTrace(
            trace_id="t-media",
            workflow_name="w",
            started_at=0.0,
            ended_at=0.2,
            nodes=[node],
        )
        LocalConsumer(config={"root": tmp_path}).consume(trace)
        rows = [
            json.loads(line)
            for line in (tmp_path / "t-media" / "nodes.jsonl").read_text().splitlines()
        ]
        assert "$media_ref" in rows[0]["inputs"]["audio"]
        # media file actually on disk
        media = list((tmp_path / "t-media" / "media").iterdir())
        assert len(media) == 1


# ============================================================
# meta.json
# ============================================================


class TestMetaJson:
    def test_metadata_and_timings(self, tmp_path, synthetic_trace):
        LocalConsumer(config={"root": tmp_path}).consume(synthetic_trace)
        meta = json.loads((tmp_path / "t-123" / "meta.json").read_text())
        assert meta["trace_id"] == "t-123"
        assert meta["workflow_name"] == "callbot"
        assert meta["node_count"] == 4
        assert meta["metadata"]["user_id"] == "cust-1"


# ============================================================
# view.txt — golden file
# ============================================================


class TestGoldenView:
    def test_view_matches_golden(self, tmp_path, synthetic_trace):
        """If this fails and you meant to change the render format,
        overwrite `local_consumer_view.txt` with the actual output."""
        LocalConsumer(config={"root": tmp_path}).consume(synthetic_trace)
        actual = (tmp_path / "t-123" / "view.txt").read_text()
        if not GOLDEN.exists():
            GOLDEN.parent.mkdir(parents=True, exist_ok=True)
            GOLDEN.write_text(actual)
            pytest.skip("golden file created — rerun test to compare")
        expected = GOLDEN.read_text()
        assert actual == expected, (
            "view.txt drifted from golden. If intentional, delete "
            f"{GOLDEN} and rerun to regenerate.\n"
            f"--- ACTUAL ---\n{actual}\n--- EXPECTED ---\n{expected}"
        )
