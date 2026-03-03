"""Test LangfuseTracer with the new tracing API.

Tests cover:
- LangfuseConfig/Client creation
- LangfuseTracer creation and validation
- Inheritance from hush.core.tracing.Tracer
- flush() with new trace_data format (graph_structure + records)
"""

import os
import uuid
from unittest.mock import MagicMock, patch

import pytest

# Langfuse cloud credentials (for integration tests) - loaded from environment
LANGFUSE_CONFIG = {
    "public_key": os.environ.get("LANGFUSE_PUBLIC_KEY", ""),
    "secret_key": os.environ.get("LANGFUSE_SECRET_KEY", ""),
    "host": os.environ.get("LANGFUSE_HOST", "https://cloud.langfuse.com"),
}


def test_langfuse_config_creation():
    """Test LangfuseConfig can be created."""
    from hush.telemetry import LangfuseConfig

    config = LangfuseConfig(**LANGFUSE_CONFIG)
    assert config.public_key == LANGFUSE_CONFIG["public_key"]
    assert config.secret_key == LANGFUSE_CONFIG["secret_key"]
    assert config.host == LANGFUSE_CONFIG["host"]


def test_langfuse_client_creation():
    """Test LangfuseClient can be created."""
    from hush.telemetry import LangfuseClient, LangfuseConfig

    config = LangfuseConfig(**LANGFUSE_CONFIG)
    client = LangfuseClient(config)

    assert client.config == config
    assert repr(client) == f"<LangfuseClient host={config.host}>"


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


def test_langfuse_tracer_flush_with_mock(sample_trace_data):
    """Test flush() creates Langfuse trace/span/generation hierarchy."""
    from hush.telemetry import LangfuseTracer

    tracer = LangfuseTracer(resource="langfuse:default")

    mock_trace = MagicMock()
    mock_span = MagicMock()
    mock_generation = MagicMock()
    mock_trace.span.return_value = mock_span
    mock_trace.generation.return_value = mock_generation

    mock_client = MagicMock()
    mock_client.trace.return_value = mock_trace

    mock_hub = MagicMock()
    mock_hub.langfuse.return_value = mock_client

    with patch("hush.core.registry.get_hub", return_value=mock_hub):
        tracer.flush(sample_trace_data)

    # Verify root trace created
    mock_client.trace.assert_called_once()
    call_kwargs = mock_client.trace.call_args[1]
    assert call_kwargs["name"] == "test-workflow"
    assert call_kwargs["user_id"] == "test-user"
    assert call_kwargs["session_id"] == "test-session"

    # Verify child span created (child-1)
    mock_trace.span.assert_called_once()
    span_kwargs = mock_trace.span.call_args[1]
    assert span_kwargs["name"] == "child-1"

    # Verify generation created (llm-node)
    mock_trace.generation.assert_called_once()
    gen_kwargs = mock_trace.generation.call_args[1]
    assert gen_kwargs["name"] == "llm-node"
    assert gen_kwargs["model"] == "gpt-4"
    assert gen_kwargs["usage"] == {"input": 10, "output": 20, "total": 30, "total_cost": 0.0015}

    # Verify flush called on client
    mock_client.flush.assert_called_once()


def test_langfuse_tracer_flush_with_direct_config(sample_trace_data):
    """Test flush() with direct config (no ResourceHub)."""
    from hush.telemetry import LangfuseConfig, LangfuseTracer

    config = LangfuseConfig(**LANGFUSE_CONFIG)
    tracer = LangfuseTracer(config=config)

    mock_trace = MagicMock()
    mock_trace.span.return_value = MagicMock()
    mock_trace.generation.return_value = MagicMock()

    mock_client = MagicMock()
    mock_client.trace.return_value = mock_trace

    with patch(
        "hush.telemetry.backends.langfuse.LangfuseClient", return_value=mock_client
    ) as MockClient:
        tracer.flush(sample_trace_data)

    MockClient.assert_called_once_with(config)
    mock_client.flush.assert_called_once()


def test_langfuse_tracer_flush_with_iteration(sample_iteration_trace_data):
    """Test flush() handles context-aware nodes (iteration/map)."""
    from hush.telemetry import LangfuseTracer

    tracer = LangfuseTracer(resource="langfuse:default")

    mock_trace = MagicMock()
    mock_span = MagicMock()
    mock_trace.span.return_value = mock_span
    mock_span.span.return_value = mock_span

    mock_client = MagicMock()
    mock_client.trace.return_value = mock_trace

    mock_hub = MagicMock()
    mock_hub.langfuse.return_value = mock_client

    with patch("hush.core.registry.get_hub", return_value=mock_hub):
        tracer.flush(sample_iteration_trace_data)

    # Root + map_op + 3 process iterations + aggregate = 6 total
    # Root → trace, rest → spans
    mock_client.trace.assert_called_once()
    # map_op + 3 process + aggregate = 5 spans
    total_spans = mock_trace.span.call_count + mock_span.span.call_count
    assert total_spans == 5
    mock_client.flush.assert_called_once()


@pytest.mark.integration
def test_langfuse_cloud_connection():
    """Test actual connection to Langfuse cloud."""
    from hush.telemetry import LangfuseClient, LangfuseConfig

    try:
        from langfuse import Langfuse  # noqa: F401
    except ImportError:
        pytest.skip("langfuse package not installed")

    config = LangfuseConfig(**LANGFUSE_CONFIG)
    client = LangfuseClient(config)

    trace = client.trace(
        name="hush-telemetry-test",
        user_id="test-user",
        session_id="test-session",
        metadata={"test": True, "source": "hush-telemetry-tests"},
        input={"message": "Testing LangfuseClient integration"},
        output={"status": "success"},
    )

    trace.span(
        name="test-span",
        input={"operation": "test"},
        output={"result": "ok"},
        metadata={"step": 1},
    )

    client.flush()

    trace_url = trace.get_trace_url()
    print(f"\nTrace URL: {trace_url}")

    assert trace_url is not None
    assert "cloud.langfuse.com" in trace_url


@pytest.mark.integration
def test_langfuse_tracer_flush_integration():
    """Test LangfuseTracer.flush() end-to-end with real Langfuse cloud."""
    from hush.telemetry import LangfuseClient, LangfuseConfig, LangfuseTracer

    try:
        import langfuse  # noqa: F401
    except ImportError:
        pytest.skip("langfuse package not installed")

    mock_config = LangfuseConfig(**LANGFUSE_CONFIG)
    mock_client = LangfuseClient(mock_config)

    request_id = str(uuid.uuid4())
    trace_data = {
        "workflow_name": "test-workflow",
        "request_id": request_id,
        "user_id": "test-user",
        "session_id": "test-session",
        "tags": ["integration", "test"],
        "graph_structure": [
            {
                "op_name": "root",
                "op_type": "graph",
                "parent_name": None,
                "contain_generation": False,
            },
            {
                "op_name": "root.child-1",
                "op_type": "code",
                "parent_name": "root",
                "contain_generation": False,
            },
            {
                "op_name": "root.llm-node",
                "op_type": "llm",
                "parent_name": "root",
                "contain_generation": True,
            },
        ],
        "records": [
            {
                "op_name": "root",
                "context_id": None,
                "inputs": {"workflow": "test"},
                "outputs": {"status": "completed"},
                "start_time": None,
                "end_time": None,
                "duration_ms": None,
                "metadata": {"version": "1.0"},
            },
            {
                "op_name": "root.child-1",
                "context_id": None,
                "inputs": {"step": 1},
                "outputs": {"processed": True},
                "start_time": None,
                "end_time": None,
                "duration_ms": None,
                "metadata": {},
            },
            {
                "op_name": "root.llm-node",
                "context_id": None,
                "inputs": {"prompt": "Test prompt"},
                "outputs": {"completion": "Test response"},
                "start_time": None,
                "end_time": None,
                "duration_ms": None,
                "model": "gpt-4",
                "usage": {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30},
                "cost": 0.0015,
                "metadata": {"temperature": 0.7},
            },
        ],
    }

    # Mock get_hub to return our actual client
    mock_hub = MagicMock()
    mock_hub.langfuse.return_value = mock_client

    tracer = LangfuseTracer(resource="langfuse:test")

    with patch("hush.core.registry.get_hub", return_value=mock_hub):
        tracer.flush(trace_data)

    print(f"\nTrace created with request_id: {request_id}")
    print("Check Langfuse dashboard: https://cloud.langfuse.com")
