"""Unit tests for LangfuseTreeExporter — event stream → Langfuse ingestion batch.

Mocked client (captures the ingest call). Integration is covered by the
manual probe at ``scripts/probe_langfuse_edupia_roundtrip.py``.
"""

from datetime import datetime, timedelta, timezone
from typing import Optional

import pytest

from operonx.core.tracing.events import EventKind, TraceEvent
from operonx.telemetry.exporters import LangfuseTreeExporter


# =============================================================================
# Test helpers
# =============================================================================


class _MockClient:
    """Captures ingest() calls. Returns the empty success response shape."""

    def __init__(self):
        self.batches: list = []
        self.host = "https://mock.local"

    def ingest(self, batch, timeout: int = 30):
        self.batches.append(batch)
        return {"successes": [{"id": e.get("id")} for e in batch], "errors": []}

    def trace_url(self, trace_id):
        return f"{self.host}/trace/{trace_id}"


def _exporter(client: Optional[_MockClient] = None) -> tuple[LangfuseTreeExporter, _MockClient]:
    """Build an exporter with a mock client wired in."""
    client = client or _MockClient()
    # We can't pass a config without LangfuseConfig, but we can construct
    # the exporter via direct config + monkey-patch the lazy client lookup.
    exp = LangfuseTreeExporter.__new__(LangfuseTreeExporter)
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


class TestExportEarlyReturn:
    def test_no_events_no_post(self):
        exp, client = _exporter()
        exp.export([], "req-1", {})
        assert client.batches == []

    def test_only_synthetic_events_no_op_data_no_post(self):
        """A stream of only GROUP_START/END (no OP_START) → nothing to render."""
        exp, client = _exporter()
        exp.export([
            _ev(EventKind.GROUP_START, payload={"name": "g"}, seq=0),
            _ev(EventKind.GROUP_END, payload={"name": "g", "status": "ok"}, seq=1),
        ], "req-1", {})
        assert client.batches == []


class TestSimpleOp:
    def test_single_op_produces_trace_plus_span(self):
        exp, client = _exporter()
        events = [
            _ev(EventKind.OP_START, op_name="g.add",
                ctx=("main",), seq=0, payload={"inputs": {"x": 5}}),
            _ev(EventKind.OP_END, op_name="g.add",
                ctx=("main",), seq=1,
                payload={"outputs": {"r": 6}, "status": "ok",
                         "duration_ms": 1.2, "yield_count": 0},
                timestamp=datetime(2026, 5, 5, 12, 0, 0, 1000, tzinfo=timezone.utc)),
        ]
        exp.export(events, "req-1", {"user_id": "u-1", "session_id": "s-1"})

        assert len(client.batches) == 1
        batch = client.batches[0]
        # trace-create + 1 span-create
        types = [e["type"] for e in batch]
        assert types == ["trace-create", "span-create"]

        trace = batch[0]["body"]
        assert trace["id"] == "req-1"
        assert trace["userId"] == "u-1"
        assert trace["sessionId"] == "s-1"

        span = batch[1]["body"]
        assert span["traceId"] == "req-1"
        assert span["name"] == "add"  # short name
        assert span["input"] == {"x": 5}
        assert span["output"] == {"r": 6}
        assert span["startTime"].endswith("Z")
        assert span["endTime"].endswith("Z")

    def test_op_with_no_op_end_still_renders_span(self):
        """E.g. cancel-after-start where the cancel-emit didn't reach. Spec
        says start without end is rare but we don't want to crash on it."""
        exp, client = _exporter()
        events = [
            _ev(EventKind.OP_START, op_name="g.x", ctx=("main",), seq=0,
                payload={"inputs": {}}),
        ]
        exp.export(events, "req-1", {})
        batch = client.batches[0]
        span = batch[1]["body"]
        assert span["name"] == "x"
        assert "endTime" not in span


class TestGenerationDetection:
    def test_llm_usage_promotes_op_to_generation(self):
        exp, client = _exporter()
        events = [
            _ev(EventKind.OP_START, op_name="g.ask", ctx=("main",), seq=0,
                payload={"inputs": {"prompt": "hi"}}),
            _ev(EventKind.LLM_USAGE, op_name="g.ask", ctx=("main",), seq=1,
                payload={"model": "gpt-4o", "prompt_tokens": 10,
                         "completion_tokens": 20, "total_tokens": 30,
                         "cost_usd": 0.001}),
            _ev(EventKind.OP_END, op_name="g.ask", ctx=("main",), seq=2,
                payload={"outputs": {"r": "hello"}, "status": "ok"}),
        ]
        exp.export(events, "req-1", {})
        batch = client.batches[0]
        # trace-create + 1 generation-create
        assert batch[1]["type"] == "generation-create"
        gen = batch[1]["body"]
        assert gen["model"] == "gpt-4o"
        assert gen["usageDetails"] == {"input": 10, "output": 20, "total": 30}
        assert gen["costDetails"] == {"total": 0.001}

    def test_no_usage_stays_a_span(self):
        """Op without LLM_USAGE renders as a plain span, not a generation."""
        exp, client = _exporter()
        events = [
            _ev(EventKind.OP_START, op_name="g.plain", ctx=("main",), seq=0,
                payload={"inputs": {}}),
            _ev(EventKind.OP_END, op_name="g.plain", ctx=("main",), seq=1,
                payload={"outputs": {}, "status": "ok"}),
        ]
        exp.export(events, "req-1", {})
        assert client.batches[0][1]["type"] == "span-create"


class TestParentLinking:
    def test_child_ctx_links_to_parent_via_prefix(self):
        """Op at ctx ('main', 'a', '[0]') should link to op at ('main', 'a')."""
        exp, client = _exporter()
        events = [
            # Parent
            _ev(EventKind.OP_START, op_name="g.gen", ctx=("main",), seq=0,
                payload={"inputs": {}}),
            _ev(EventKind.OP_END, op_name="g.gen", ctx=("main",), seq=1,
                payload={"outputs": {}, "status": "ok"}),
            # Child at deeper ctx (would normally be a sub-op via item ctx)
            _ev(EventKind.OP_START, op_name="g.child",
                ctx=("main", "[0]"), seq=2, payload={"inputs": {}}),
            _ev(EventKind.OP_END, op_name="g.child",
                ctx=("main", "[0]"), seq=3,
                payload={"outputs": {}, "status": "ok"}),
        ]
        exp.export(events, "req-1", {})
        batch = client.batches[0]

        # Find the child span
        child_obs = next(e["body"] for e in batch
                         if e["type"] in ("span-create", "generation-create")
                         and e["body"]["name"] == "child")
        # Should reference the parent's observation id
        assert "parentObservationId" in child_obs

        parent_obs = next(e["body"] for e in batch
                          if e["type"] in ("span-create", "generation-create")
                          and e["body"]["name"] == "gen")
        assert child_obs["parentObservationId"] == parent_obs["id"]

    def test_root_op_has_no_parent_observation(self):
        """An op at ctx ('main',) should NOT have parentObservationId."""
        exp, client = _exporter()
        events = [
            _ev(EventKind.OP_START, op_name="g.root", ctx=("main",), seq=0,
                payload={"inputs": {}}),
            _ev(EventKind.OP_END, op_name="g.root", ctx=("main",), seq=1,
                payload={"outputs": {}, "status": "ok"}),
        ]
        exp.export(events, "req-1", {})
        batch = client.batches[0]
        span = batch[1]["body"]
        assert "parentObservationId" not in span


class TestStatusAndLevel:
    def test_error_sets_level_error(self):
        exp, client = _exporter()
        events = [
            _ev(EventKind.OP_START, op_name="g.boom", ctx=("main",), seq=0,
                payload={"inputs": {}}),
            _ev(EventKind.OP_END, op_name="g.boom", ctx=("main",), seq=1,
                payload={"outputs": {}, "status": "error"}),
        ]
        exp.export(events, "req-1", {})
        span = client.batches[0][1]["body"]
        assert span["level"] == "ERROR"
        assert span.get("metadata", {}).get("status") == "error"

    def test_cancelled_sets_level_warning(self):
        exp, client = _exporter()
        events = [
            _ev(EventKind.OP_START, op_name="g.slow", ctx=("main",), seq=0,
                payload={"inputs": {}}),
            _ev(EventKind.OP_END, op_name="g.slow", ctx=("main",), seq=1,
                payload={"outputs": {}, "status": "cancelled"}),
        ]
        exp.export(events, "req-1", {})
        span = client.batches[0][1]["body"]
        assert span["level"] == "WARNING"
        assert span.get("metadata", {}).get("status") == "cancelled"

    def test_ok_sets_no_level(self):
        exp, client = _exporter()
        events = [
            _ev(EventKind.OP_START, op_name="g.ok", ctx=("main",), seq=0,
                payload={"inputs": {}}),
            _ev(EventKind.OP_END, op_name="g.ok", ctx=("main",), seq=1,
                payload={"outputs": {}, "status": "ok"}),
        ]
        exp.export(events, "req-1", {})
        span = client.batches[0][1]["body"]
        assert "level" not in span


class TestYieldsAndAnnotations:
    def test_yields_fold_into_metadata(self):
        """OP_YIELD events become metadata on the parent observation, not
        separate observations (matching legacy: one gen op = one span)."""
        exp, client = _exporter()
        events = [
            _ev(EventKind.OP_START, op_name="g.gen", ctx=("main",), seq=0,
                payload={"inputs": {}}),
            _ev(EventKind.OP_YIELD, op_name="g.gen",
                ctx=("main", "[0]"), seq=1,
                payload={"yielded": {"v": 1}, "idx": 0}),
            _ev(EventKind.OP_YIELD, op_name="g.gen",
                ctx=("main", "[1]"), seq=2,
                payload={"yielded": {"v": 2}, "idx": 1}),
            _ev(EventKind.OP_YIELD, op_name="g.gen",
                ctx=("main", "[2]"), seq=3,
                payload={"yielded": {"v": 3}, "idx": 2}),
            _ev(EventKind.OP_END, op_name="g.gen", ctx=("main",), seq=4,
                payload={"outputs": {}, "status": "ok", "yield_count": 3}),
        ]
        exp.export(events, "req-1", {})
        # ONE span observation for g.gen (yields folded in)
        spans = [e for e in client.batches[0] if e["type"] == "span-create"]
        assert len(spans) == 1
        meta = spans[0]["body"]["metadata"]
        assert meta["yield_count"] == 3
        assert meta["last_yielded"] == {"v": 3}

    def test_annotations_land_in_metadata(self):
        exp, client = _exporter()
        events = [
            _ev(EventKind.OP_START, op_name="g.x", ctx=("main",), seq=0,
                payload={"inputs": {}}),
            _ev(EventKind.ANNOTATION, op_name="g.x", ctx=("main",), seq=1,
                payload={"key": "user_id", "value": "u-7"}),
            _ev(EventKind.ANNOTATION, op_name="g.x", ctx=("main",), seq=2,
                payload={"key": "tag", "value": "important"}),
            _ev(EventKind.OP_END, op_name="g.x", ctx=("main",), seq=3,
                payload={"outputs": {}, "status": "ok"}),
        ]
        exp.export(events, "req-1", {})
        meta = client.batches[0][1]["body"]["metadata"]
        assert meta["user_id"] == "u-7"
        assert meta["tag"] == "important"


class TestTagsAndMetadata:
    def test_static_and_dynamic_tags_merge(self):
        """Tags from constructor + per-call metadata should both appear."""
        exp, client = _exporter()
        exp.tags = ["prod", "callbot"]
        events = [
            _ev(EventKind.OP_START, op_name="g.x", ctx=("main",), seq=0,
                payload={"inputs": {}}),
            _ev(EventKind.OP_END, op_name="g.x", ctx=("main",), seq=1,
                payload={"outputs": {}, "status": "ok"}),
        ]
        exp.export(events, "req-1", {"tags": ["live", "prod"]})
        trace = client.batches[0][0]["body"]
        # Static first, dynamic appended (deduped)
        assert trace["tags"] == ["prod", "callbot", "live"]

    def test_no_tags_means_no_tags_field(self):
        exp, client = _exporter()
        events = [
            _ev(EventKind.OP_START, op_name="g.x", ctx=("main",), seq=0,
                payload={"inputs": {}}),
            _ev(EventKind.OP_END, op_name="g.x", ctx=("main",), seq=1,
                payload={"outputs": {}, "status": "ok"}),
        ]
        exp.export(events, "req-1", {})
        trace = client.batches[0][0]["body"]
        assert "tags" not in trace

    def test_workflow_name_from_metadata_overrides_default(self):
        exp, client = _exporter()
        events = [
            _ev(EventKind.OP_START, op_name="g.x", ctx=("main",), seq=0,
                payload={"inputs": {}}),
            _ev(EventKind.OP_END, op_name="g.x", ctx=("main",), seq=1,
                payload={"outputs": {}, "status": "ok"}),
        ]
        exp.export(events, "req-1", {"workflow_name": "callbot"})
        trace = client.batches[0][0]["body"]
        assert trace["name"] == "callbot"


class TestErrorPropagation:
    def test_ingestion_errors_raise(self):
        class _ErrClient:
            host = "https://mock.local"

            def ingest(self, batch, timeout: int = 30):
                return {"errors": [{"id": "x", "error": "oops"}], "successes": []}

            def trace_url(self, trace_id):
                return ""

        exp, _ = _exporter()
        exp._client_cache = _ErrClient()
        events = [
            _ev(EventKind.OP_START, op_name="g.x", ctx=("main",), seq=0,
                payload={"inputs": {}}),
            _ev(EventKind.OP_END, op_name="g.x", ctx=("main",), seq=1,
                payload={"outputs": {}, "status": "ok"}),
        ]
        with pytest.raises(RuntimeError, match="ingestion had 1 error"):
            exp.export(events, "req-1", {})


class TestConstructorValidation:
    def test_requires_config_or_resource(self):
        with pytest.raises(ValueError, match="either 'config' or 'resource'"):
            LangfuseTreeExporter()

    def test_rejects_both_config_and_resource(self):
        with pytest.raises(ValueError, match="Cannot provide both"):
            LangfuseTreeExporter(config=object(), resource="x")


# =============================================================================
# Media handling (§3.8) — port of legacy _upload_node_media
# =============================================================================


class _MediaClient(_MockClient):
    """Mock that also captures upload_media calls + returns predictable tokens."""

    def __init__(self, fail: bool = False):
        super().__init__()
        self.uploads: list = []
        self._fail = fail
        self._counter = 0

    def upload_media(self, *, trace_id, field, content_type, content,
                     observation_id=None, timeout: int = 30):
        self.uploads.append({
            "trace_id": trace_id, "field": field,
            "content_type": content_type, "content": content,
            "observation_id": observation_id,
        })
        if self._fail:
            return None
        self._counter += 1
        return f"@@@MEDIA-{self._counter}@@@"


def _exporter_with(client: _MediaClient) -> LangfuseTreeExporter:
    exp = LangfuseTreeExporter.__new__(LangfuseTreeExporter)
    exp._config = None
    exp._resource = "mock:test"
    exp.tags = []
    exp.workflow_name = "operonx"
    exp._client_cache = client
    return exp


class TestMediaUpload:
    def test_outputs_media_uploaded_and_substituted(self):
        from operonx.core.media import Media, MediaRef, extract_media

        client = _MediaClient()
        exp = _exporter_with(client)

        # Op produced an output with Media → simulate the producer emit shape
        # (BaseOp does this via _extract_trace_io; here we just call directly).
        outputs = {"audio": Media(data=b"wav_bytes", mime_type="audio/wav")}
        stripped, refs = extract_media(outputs, "outputs")

        events = [
            _ev(EventKind.OP_START, op_name="g.tts", ctx=("main",), seq=0,
                payload={"inputs": {"text": "hello"}, "media_refs": []}),
            _ev(EventKind.OP_END, op_name="g.tts", ctx=("main",), seq=1,
                payload={"outputs": stripped, "status": "ok",
                         "duration_ms": 1.0, "yield_count": 0,
                         "media_refs": refs},
                timestamp=datetime(2026, 5, 5, 12, 0, 0, 1000, tzinfo=timezone.utc)),
        ]
        exp.export(events, "req-media", {})

        # Upload was called with the raw bytes + output side
        assert len(client.uploads) == 1
        up = client.uploads[0]
        assert up["trace_id"] == "req-media"
        assert up["field"] == "output"
        assert up["content"] == b"wav_bytes"
        assert up["content_type"] == "audio/wav"

        # Token substituted into the observation body
        batch = client.batches[0]
        spans = [e for e in batch if e["type"] == "span-create"]
        assert len(spans) == 1
        body = spans[0]["body"]
        assert body["output"]["audio"] == "@@@MEDIA-1@@@"

    def test_inputs_media_uploaded_and_substituted(self):
        from operonx.core.media import Media, MediaRef, extract_media

        client = _MediaClient()
        exp = _exporter_with(client)

        # Vision-shaped input — image is at messages[0].content[1].image_url
        img = Media(data=b"png_bytes", mime_type="image/png")
        inputs = {"messages": [{"role": "user", "content": [
            {"type": "text", "text": "what?"},
            {"type": "image_url", "image_url": img},
        ]}]}
        stripped_in, refs_in = extract_media(inputs, "inputs")

        events = [
            _ev(EventKind.OP_START, op_name="g.ask", ctx=("main",), seq=0,
                payload={"inputs": stripped_in, "media_refs": refs_in}),
            _ev(EventKind.OP_END, op_name="g.ask", ctx=("main",), seq=1,
                payload={"outputs": {"answer": "a cat"}, "status": "ok",
                         "duration_ms": 1.0, "yield_count": 0,
                         "media_refs": []},
                timestamp=datetime(2026, 5, 5, 12, 0, 0, 1000, tzinfo=timezone.utc)),
        ]
        exp.export(events, "req-vision", {})

        # Upload called with input side + image bytes
        assert len(client.uploads) == 1
        assert client.uploads[0]["field"] == "input"
        assert client.uploads[0]["content"] == b"png_bytes"

        # Token substituted at the right nested path
        body = [e for e in client.batches[0] if e["type"] == "span-create"][0]["body"]
        assert body["input"]["messages"][0]["content"][1]["image_url"] == "@@@MEDIA-1@@@"

    def test_upload_failure_substitutes_fallback_string(self):
        from operonx.core.media import Media, extract_media

        client = _MediaClient(fail=True)
        exp = _exporter_with(client)

        outputs = {"audio": Media(data=b"x" * 100, mime_type="audio/wav")}
        stripped, refs = extract_media(outputs, "outputs")
        events = [
            _ev(EventKind.OP_START, op_name="g.tts", ctx=("main",), seq=0,
                payload={"inputs": {}, "media_refs": []}),
            _ev(EventKind.OP_END, op_name="g.tts", ctx=("main",), seq=1,
                payload={"outputs": stripped, "status": "ok",
                         "duration_ms": 1.0, "yield_count": 0,
                         "media_refs": refs},
                timestamp=datetime(2026, 5, 5, 12, 0, 0, 1000, tzinfo=timezone.utc)),
        ]
        exp.export(events, "req-fail", {})

        body = [e for e in client.batches[0] if e["type"] == "span-create"][0]["body"]
        assert body["output"]["audio"] == "[media upload failed: audio/wav, 100B]"

    def test_data_url_decoded_then_uploaded(self):
        from operonx.core.media import Media, extract_media
        import base64

        client = _MediaClient()
        exp = _exporter_with(client)

        raw = b"\x89PNG\r\n\x1a\nfake"
        b64 = base64.b64encode(raw).decode("ascii")
        url = f"data:image/png;base64,{b64}"
        outputs = {"img": Media(data=url, mime_type="image/png")}
        stripped, refs = extract_media(outputs, "outputs")

        events = [
            _ev(EventKind.OP_START, op_name="g.gen", ctx=("main",), seq=0,
                payload={"inputs": {}, "media_refs": []}),
            _ev(EventKind.OP_END, op_name="g.gen", ctx=("main",), seq=1,
                payload={"outputs": stripped, "status": "ok",
                         "duration_ms": 1.0, "yield_count": 0,
                         "media_refs": refs},
                timestamp=datetime(2026, 5, 5, 12, 0, 0, 1000, tzinfo=timezone.utc)),
        ]
        exp.export(events, "req-dataurl", {})

        # Decoded bytes uploaded, not the data: URL
        assert client.uploads[0]["content"] == raw

    def test_no_media_no_upload_call(self):
        client = _MediaClient()
        exp = _exporter_with(client)
        events = [
            _ev(EventKind.OP_START, op_name="g.x", ctx=("main",), seq=0,
                payload={"inputs": {"a": 1}, "media_refs": []}),
            _ev(EventKind.OP_END, op_name="g.x", ctx=("main",), seq=1,
                payload={"outputs": {"r": 2}, "status": "ok",
                         "duration_ms": 1.0, "yield_count": 0,
                         "media_refs": []},
                timestamp=datetime(2026, 5, 5, 12, 0, 0, 1000, tzinfo=timezone.utc)),
        ]
        exp.export(events, "req-clean", {})
        assert client.uploads == []
