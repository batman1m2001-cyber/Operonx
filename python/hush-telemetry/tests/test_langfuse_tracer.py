"""Test LangfuseTracer with direct HTTP API (no SDK).

Tests cover:
- LangfuseConfig/Client creation
- LangfuseTracer creation and validation
- Inheritance from hush.core.tracing.Tracer
- flush() builds correct batch events from nodes format
- Batch event structure (trace-create, span-create, generation-create)
"""

import os
import urllib.error
import uuid
from unittest.mock import MagicMock, patch

import pytest

# Langfuse cloud credentials (for integration tests) - loaded from environment
# Prefer LANGFUSE_HUSH_* keys (Langfuse Cloud) over LANGFUSE_* (VPBank internal)
LANGFUSE_CONFIG = {
    "public_key": os.environ.get("LANGFUSE_HUSH_PUBLIC_KEY")
    or os.environ.get("LANGFUSE_PUBLIC_KEY", ""),
    "secret_key": os.environ.get("LANGFUSE_HUSH_SECRET_KEY")
    or os.environ.get("LANGFUSE_SECRET_KEY", ""),
    "host": os.environ.get("LANGFUSE_HUSH_BASE_URL")
    or os.environ.get("LANGFUSE_HOST", "https://cloud.langfuse.com"),
}


def test_langfuse_config_creation():
    """Test LangfuseConfig can be created."""
    from hush.telemetry import LangfuseConfig

    config = LangfuseConfig(**LANGFUSE_CONFIG)
    assert config.public_key == LANGFUSE_CONFIG["public_key"]
    assert config.secret_key == LANGFUSE_CONFIG["secret_key"]
    assert config.host == LANGFUSE_CONFIG["host"]


def test_langfuse_client_creation():
    """Test LangfuseClient can be created (no SDK needed)."""
    from hush.telemetry import LangfuseClient, LangfuseConfig

    config = LangfuseConfig(**LANGFUSE_CONFIG)
    client = LangfuseClient(config)

    assert client.config == config
    assert repr(client) == f"<LangfuseClient host={config.host}>"


def test_langfuse_client_has_ingest_method():
    """Test LangfuseClient has ingest() and trace_url() methods."""
    from hush.telemetry import LangfuseClient, LangfuseConfig

    config = LangfuseConfig(**LANGFUSE_CONFIG)
    client = LangfuseClient(config)

    assert hasattr(client, "ingest")
    assert hasattr(client, "trace_url")
    assert client.trace_url("test-id") == f"{config.host}/trace/test-id"


def test_langfuse_client_auth_header():
    """Test LangfuseClient builds correct Basic auth header."""
    import base64

    from hush.telemetry import LangfuseClient, LangfuseConfig

    config = LangfuseConfig(public_key="pk-test", secret_key="sk-test", host="https://example.com")
    client = LangfuseClient(config)

    expected = base64.b64encode(b"pk-test:sk-test").decode()
    assert client._auth == expected


def test_langfuse_tracer_creation():
    """Test LangfuseTracer can be created with resource."""
    from hush.telemetry import LangfuseTracer

    tracer = LangfuseTracer(resource="langfuse:default")

    assert tracer.resource == "langfuse:default"
    assert repr(tracer) == "<LangfuseTracer resource=langfuse:default>"


def test_langfuse_tracer_inherits_from_new_tracer():
    """Test LangfuseTracer inherits from hush.core.tracing.Tracer."""
    from hush.core.tracing import Tracer

    from hush.telemetry import LangfuseTracer

    tracer = LangfuseTracer(resource="langfuse:default", tags=["test"])
    assert isinstance(tracer, Tracer)
    assert tracer.tags == ["test"]


def test_langfuse_tracer_requires_config_or_resource():
    """Test LangfuseTracer raises error if neither config nor resource provided."""
    from hush.telemetry import LangfuseTracer

    with pytest.raises(ValueError, match="Must provide either"):
        LangfuseTracer()


def test_langfuse_tracer_rejects_both_config_and_resource():
    """Test LangfuseTracer raises error if both config and resource provided."""
    from hush.telemetry import LangfuseConfig, LangfuseTracer

    config = LangfuseConfig(**LANGFUSE_CONFIG)
    with pytest.raises(ValueError, match="Cannot provide both"):
        LangfuseTracer(config=config, resource="langfuse:default")


def test_langfuse_tracer_flush_builds_correct_batch(sample_trace_data):
    """Test flush() builds correct batch events from nodes."""
    from hush.telemetry import LangfuseTracer

    tracer = LangfuseTracer(resource="langfuse:default")

    captured_batch = []

    mock_client = MagicMock()
    mock_client.ingest.return_value = {"successes": [], "errors": []}
    mock_client.trace_url.return_value = "https://example.com/trace/test"

    def capture_ingest(batch):
        captured_batch.extend(batch)
        return {"successes": [], "errors": []}

    mock_client.ingest.side_effect = capture_ingest

    mock_hub = MagicMock()
    mock_hub.get.return_value = mock_client

    with patch("hush.core.registry.get_hub", return_value=mock_hub):
        tracer.flush(sample_trace_data)

    # Should have 3 events: 1 trace-create, 1 span-create, 1 generation-create
    assert len(captured_batch) == 3

    # Event types
    types = [e["type"] for e in captured_batch]
    assert types[0] == "trace-create"
    assert "span-create" in types
    assert "generation-create" in types

    # Root trace event
    trace_event = captured_batch[0]
    assert trace_event["body"]["name"] == "test-workflow"
    assert trace_event["body"]["userId"] == "test-user"
    assert trace_event["body"]["sessionId"] == "test-session"

    # Generation event (llm-node)
    gen_events = [e for e in captured_batch if e["type"] == "generation-create"]
    assert len(gen_events) == 1
    gen_body = gen_events[0]["body"]
    assert gen_body["name"] == "llm-node"
    assert gen_body["model"] == "gpt-4"
    assert gen_body["usageDetails"] == {"input": 10, "output": 20, "total": 30}
    assert gen_body["costDetails"] == {"total": 0.0015}

    # Client methods called correctly
    mock_client.ingest.assert_called_once()
    mock_client.trace_url.assert_called_once()


def test_langfuse_tracer_flush_with_direct_config(sample_trace_data):
    """Test flush() with direct config (no ResourceHub)."""
    from hush.telemetry import LangfuseConfig, LangfuseTracer

    config = LangfuseConfig(**LANGFUSE_CONFIG)
    tracer = LangfuseTracer(config=config)

    mock_client = MagicMock()
    mock_client.ingest.return_value = {"successes": [], "errors": []}
    mock_client.trace_url.return_value = "https://example.com/trace/test"

    with patch(
        "hush.telemetry.backends.langfuse.LangfuseClient", return_value=mock_client
    ) as MockClient:
        tracer.flush(sample_trace_data)

    MockClient.assert_called_once_with(config)
    mock_client.ingest.assert_called_once()


def test_langfuse_tracer_flush_parent_linking(sample_iteration_trace_data):
    """Test flush() correctly sets parentObservationId for nested nodes."""
    from hush.telemetry import LangfuseTracer

    tracer = LangfuseTracer(resource="langfuse:default")

    captured_batch = []

    mock_client = MagicMock()
    mock_client.ingest.side_effect = lambda b: (
        captured_batch.extend(b),
        {"successes": [], "errors": []},
    )[1]
    mock_client.trace_url.return_value = "https://example.com/trace/test"

    mock_hub = MagicMock()
    mock_hub.get.return_value = mock_client

    with patch("hush.core.registry.get_hub", return_value=mock_hub):
        tracer.flush(sample_iteration_trace_data)

    # 9 nodes: 1 trace + 1 generator + 3 stream_contexts + 3 stream_items + 1 aggregate
    assert len(captured_batch) == 9

    # Extract events by type
    trace_events = [e for e in captured_batch if e["type"] == "trace-create"]
    span_events = [e for e in captured_batch if e["type"] == "span-create"]

    assert len(trace_events) == 1
    assert len(span_events) == 8  # all non-trace nodes are spans

    # Direct children of root (map_op, 3 stream_contexts, aggregate) should NOT have parentObservationId
    # (they are direct children of the trace, linked only via traceId)
    root_children = [
        e
        for e in span_events
        if "parentObservationId" not in e["body"] or e["body"]["parentObservationId"] is None
    ]
    # map_op + 3 stream_contexts + aggregate = 5 direct children
    assert len(root_children) == 5

    # Stream items should have parentObservationId pointing to their stream_context
    items_with_parent = [e for e in span_events if e["body"].get("parentObservationId")]
    assert len(items_with_parent) == 3  # 3 process nodes inside stream contexts

    mock_client.ingest.assert_called_once()


def test_langfuse_tracer_flush_all_events_have_trace_id(sample_trace_data):
    """Test all span/generation events reference the correct traceId."""
    from hush.telemetry import LangfuseTracer

    tracer = LangfuseTracer(resource="langfuse:default")

    captured_batch = []

    mock_client = MagicMock()
    mock_client.ingest.side_effect = lambda b: (
        captured_batch.extend(b),
        {"successes": [], "errors": []},
    )[1]
    mock_client.trace_url.return_value = "https://example.com/trace/test"

    mock_hub = MagicMock()
    mock_hub.get.return_value = mock_client

    with patch("hush.core.registry.get_hub", return_value=mock_hub):
        tracer.flush(sample_trace_data)

    trace_id = sample_trace_data["request_id"]
    for event in captured_batch:
        if event["type"] != "trace-create":
            assert event["body"]["traceId"] == trace_id


# ============================================================================
# Error Handling Tests
# ============================================================================


def test_langfuse_tracer_flush_raises_on_http_error(sample_trace_data):
    """Test flush() propagates HTTP errors from ingest."""
    from hush.telemetry import LangfuseTracer

    tracer = LangfuseTracer(resource="langfuse:default")

    mock_client = MagicMock()
    mock_client.ingest.side_effect = urllib.error.HTTPError(
        url="https://example.com", code=401, msg="Unauthorized", hdrs=None, fp=None
    )

    mock_hub = MagicMock()
    mock_hub.get.return_value = mock_client

    with patch("hush.core.registry.get_hub", return_value=mock_hub):
        with pytest.raises(urllib.error.HTTPError):
            tracer.flush(sample_trace_data)


def test_langfuse_tracer_flush_raises_on_connection_error(sample_trace_data):
    """Test flush() propagates connection errors."""
    from hush.telemetry import LangfuseTracer

    tracer = LangfuseTracer(resource="langfuse:default")

    mock_client = MagicMock()
    mock_client.ingest.side_effect = urllib.error.URLError("Connection refused")

    mock_hub = MagicMock()
    mock_hub.get.return_value = mock_client

    with patch("hush.core.registry.get_hub", return_value=mock_hub):
        with pytest.raises(urllib.error.URLError):
            tracer.flush(sample_trace_data)


def test_langfuse_tracer_flush_raises_on_timeout(sample_trace_data):
    """Test flush() propagates timeout errors."""
    import socket

    from hush.telemetry import LangfuseTracer

    tracer = LangfuseTracer(resource="langfuse:default")

    mock_client = MagicMock()
    mock_client.ingest.side_effect = socket.timeout("timed out")

    mock_hub = MagicMock()
    mock_hub.get.return_value = mock_client

    with patch("hush.core.registry.get_hub", return_value=mock_hub):
        with pytest.raises(socket.timeout):
            tracer.flush(sample_trace_data)


def test_langfuse_tracer_flush_raises_on_hub_not_found(sample_trace_data):
    """Test flush() propagates missing ResourceHub errors."""
    from hush.telemetry import LangfuseTracer

    tracer = LangfuseTracer(resource="langfuse:default")

    mock_hub = MagicMock()
    mock_hub.get.side_effect = KeyError("langfuse:default not found")

    with patch("hush.core.registry.get_hub", return_value=mock_hub):
        with pytest.raises(KeyError):
            tracer.flush(sample_trace_data)


def test_langfuse_tracer_flush_raises_on_partial_errors(sample_trace_data):
    """Test flush() raises RuntimeError when ingest returns partial errors."""
    from hush.telemetry import LangfuseTracer

    tracer = LangfuseTracer(resource="langfuse:default")

    mock_client = MagicMock()
    mock_client.ingest.return_value = {
        "successes": [{"id": "evt-1", "status": 201}],
        "errors": [{"id": "evt-2", "status": 400, "message": "Invalid body"}],
    }
    mock_client.trace_url.return_value = "https://example.com/trace/test"

    mock_hub = MagicMock()
    mock_hub.get.return_value = mock_client

    with patch("hush.core.registry.get_hub", return_value=mock_hub):
        with pytest.raises(RuntimeError, match="ingestion had 1 error"):
            tracer.flush(sample_trace_data)

    mock_client.ingest.assert_called_once()


def test_langfuse_tracer_flush_empty_nodes():
    """Test flush() handles trace_data with empty nodes list."""
    from hush.telemetry import LangfuseTracer

    tracer = LangfuseTracer(resource="langfuse:default")

    mock_client = MagicMock()
    mock_client.ingest.return_value = {"successes": [], "errors": []}
    mock_client.trace_url.return_value = "https://example.com/trace/test"

    mock_hub = MagicMock()
    mock_hub.get.return_value = mock_client

    trace_data = {
        "workflow_name": "empty",
        "request_id": "test-123",
        "nodes": [],
    }

    with patch("hush.core.registry.get_hub", return_value=mock_hub):
        tracer.flush(trace_data)

    # Should still call ingest with empty batch
    mock_client.ingest.assert_called_once_with([])


def test_langfuse_client_ingest_http_error():
    """Test LangfuseClient.ingest() raises on HTTP errors."""
    from hush.telemetry import LangfuseClient, LangfuseConfig

    config = LangfuseConfig(public_key="pk-test", secret_key="sk-test", host="https://example.com")
    client = LangfuseClient(config)

    with patch("urllib.request.urlopen") as mock_urlopen:
        mock_urlopen.side_effect = urllib.error.HTTPError(
            url="https://example.com", code=500, msg="Server Error", hdrs=None, fp=None
        )
        with pytest.raises(urllib.error.HTTPError):
            client.ingest([{"id": "test", "type": "trace-create", "body": {}}])


def test_langfuse_client_auth_check_failure():
    """Test LangfuseClient.auth_check() returns False on failure."""
    from hush.telemetry import LangfuseClient, LangfuseConfig

    config = LangfuseConfig(public_key="pk-bad", secret_key="sk-bad", host="https://example.com")
    client = LangfuseClient(config)

    with patch("urllib.request.urlopen") as mock_urlopen:
        mock_urlopen.side_effect = urllib.error.HTTPError(
            url="https://example.com", code=401, msg="Unauthorized", hdrs=None, fp=None
        )
        assert client.auth_check() is False


@pytest.mark.integration
def test_langfuse_tracer_flush_integration():
    """Test LangfuseTracer.flush() end-to-end with real Langfuse cloud."""
    from hush.telemetry import LangfuseConfig, LangfuseTracer

    if not (os.environ.get("LANGFUSE_HUSH_PUBLIC_KEY") or os.environ.get("LANGFUSE_PUBLIC_KEY")):
        pytest.skip("LANGFUSE keys not set")

    config = LangfuseConfig(**LANGFUSE_CONFIG)
    tracer = LangfuseTracer(config=config)

    request_id = str(uuid.uuid4())
    trace_data = {
        "workflow_name": "test-workflow",
        "request_id": request_id,
        "user_id": "test-user",
        "session_id": "test-session",
        "tags": ["integration", "test", "no-sdk"],
        "nodes": [
            {
                "trace_key": "root",
                "parent_trace_key": None,
                "op_name": "root",
                "display_name": "test-workflow",
                "node_type": "trace",
                "kind": "graph",
                "inputs": {"workflow": "test"},
                "outputs": {"status": "completed"},
                "start_time": "2026-03-08T20:00:00Z",
                "end_time": "2026-03-08T20:00:01Z",
                "metadata": {"version": "1.0"},
            },
            {
                "trace_key": "root.child-1",
                "parent_trace_key": "root",
                "op_name": "root.child-1",
                "display_name": "child-1",
                "node_type": "span",
                "kind": "batch",
                "inputs": {"step": 1},
                "outputs": {"processed": True},
                "start_time": "2026-03-08T20:00:00Z",
                "end_time": "2026-03-08T20:00:00.500Z",
                "metadata": {},
            },
            {
                "trace_key": "root.llm-node",
                "parent_trace_key": "root",
                "op_name": "root.llm-node",
                "display_name": "llm-node",
                "node_type": "generation",
                "kind": "batch",
                "inputs": {"prompt": "Test prompt"},
                "outputs": {"completion": "Test response"},
                "start_time": "2026-03-08T20:00:00.500Z",
                "end_time": "2026-03-08T20:00:01Z",
                "model": "gpt-4",
                "usage": {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30},
                "cost": 0.0015,
                "metadata": {"temperature": 0.7},
            },
        ],
    }

    tracer.flush(trace_data)

    print(f"\nTrace created with request_id: {request_id}")
    print(f"View: {config.host}/trace/{request_id}")
