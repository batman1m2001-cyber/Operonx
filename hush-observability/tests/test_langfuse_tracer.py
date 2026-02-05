"""Test LangfuseTracer with Langfuse cloud instance.

This test verifies that the LangfuseTracer can successfully connect
to Langfuse cloud and create traces.
"""

import os
import uuid
from datetime import datetime
from typing import Any, Dict
from unittest.mock import MagicMock, patch

import pytest

# Langfuse cloud credentials (for integration tests) - loaded from environment
LANGFUSE_CONFIG = {
    "public_key": os.environ.get("LANGFUSE_PUBLIC_KEY", ""),
    "secret_key": os.environ.get("LANGFUSE_SECRET_KEY", ""),
    "host": os.environ.get("LANGFUSE_HOST", "https://cloud.langfuse.com"),
}


class MockNode:
    """Mock node for testing."""

    def __init__(self, node_id: str, contain_generation: bool = False):
        self.node_id = node_id
        self.contain_generation = contain_generation
        self._trace_data = {
            "name": node_id,
            "start_time": datetime.now(),
            "end_time": datetime.now(),
            "input": {"test": "input"},
            "output": {"test": "output"},
            "metadata": {"source": "test"},
        }

    def trace_data(self, state: Any, context_id: str = None) -> Dict[str, Any]:
        return self._trace_data


class MockIndexer:
    """Mock indexer for testing."""

    def __init__(self):
        self._nodes = {}

    def add_node(self, node: MockNode):
        self._nodes[node.node_id] = node


class MockMemoryState:
    """Mock MemoryState for testing."""

    def __init__(self):
        self.request_id = str(uuid.uuid4())
        self.user_id = "test-user"
        self.session_id = "test-session"
        self.execution_order = []
        self._indexer = MockIndexer()

    def add_execution(self, node_id: str, parent_id: str = None, context_id: str = None):
        """Add an execution to the order."""
        node = MockNode(node_id)
        self._indexer.add_node(node)
        self.execution_order.append(
            {
                "node": node_id,
                "parent": parent_id,
                "context_id": context_id,
            }
        )


def test_langfuse_config_creation():
    """Test LangfuseConfig can be created."""
    from hush.observability import LangfuseConfig

    config = LangfuseConfig(**LANGFUSE_CONFIG)
    assert config.public_key == LANGFUSE_CONFIG["public_key"]
    assert config.secret_key == LANGFUSE_CONFIG["secret_key"]
    assert config.host == LANGFUSE_CONFIG["host"]


def test_langfuse_client_creation():
    """Test LangfuseClient can be created."""
    from hush.observability import LangfuseClient, LangfuseConfig

    config = LangfuseConfig(**LANGFUSE_CONFIG)
    client = LangfuseClient(config)

    assert client.config == config
    assert repr(client) == f"<LangfuseClient host={config.host}>"


def test_langfuse_tracer_creation():
    """Test LangfuseTracer can be created with resource_key."""
    from hush.observability import LangfuseTracer

    tracer = LangfuseTracer(resource_key="langfuse:default")

    assert tracer.resource_key == "langfuse:default"
    assert repr(tracer) == "<LangfuseTracer resource_key=langfuse:default>"


def test_tracer_config_serialization():
    """Test tracer config returns resource_key for subprocess."""
    from hush.observability import LangfuseTracer

    tracer = LangfuseTracer(resource_key="langfuse:default")

    tracer_config = tracer._get_tracer_config()
    assert tracer_config["resource_key"] == "langfuse:default"


def test_langfuse_tracer_registered():
    """Test LangfuseTracer is registered in tracer registry."""
    from hush.core.tracers import get_registered_tracers

    from hush.observability import LangfuseTracer  # noqa: F401

    tracers = get_registered_tracers()
    assert "LangfuseTracer" in tracers


@pytest.mark.integration
def test_langfuse_cloud_connection():
    """Test actual connection to Langfuse cloud.

    This test creates a real trace in Langfuse cloud.
    Run with: pytest -m integration
    """
    from hush.observability import LangfuseClient, LangfuseConfig

    try:
        from langfuse import Langfuse  # noqa: F401
    except ImportError:
        pytest.skip("langfuse package not installed")

    config = LangfuseConfig(**LANGFUSE_CONFIG)
    client = LangfuseClient(config)

    # Create a test trace
    trace = client.trace(
        name="hush-observability-test",
        user_id="test-user",
        session_id="test-session",
        metadata={"test": True, "source": "hush-observability-tests"},
        input={"message": "Testing LangfuseClient integration"},
        output={"status": "success"},
    )

    # Create a span
    trace.span(
        name="test-span",
        input={"operation": "test"},
        output={"result": "ok"},
        metadata={"step": 1},
    )

    # Flush to ensure trace is sent
    client.flush()

    trace_url = trace.get_trace_url()
    print(f"\nTrace URL: {trace_url}")

    assert trace_url is not None
    assert "cloud.langfuse.com" in trace_url


@pytest.mark.integration
def test_langfuse_tracer_flush_with_resource_hub():
    """Test LangfuseTracer.flush() method with ResourceHub.

    This test calls the static flush method with mock data.
    Requires ResourceHub to be configured with langfuse:test key.
    """
    from hush.observability import LangfuseTracer

    try:
        import langfuse  # noqa: F401
    except ImportError:
        pytest.skip("langfuse package not installed")

    # Mock ResourceHub to return a LangfuseClient
    from hush.observability import LangfuseClient, LangfuseConfig

    mock_config = LangfuseConfig(**LANGFUSE_CONFIG)
    mock_client = LangfuseClient(mock_config)

    # Prepare mock flush data
    request_id = str(uuid.uuid4())
    flush_data = {
        "tracer_type": "LangfuseTracer",
        "tracer_config": {"resource_key": "langfuse:test"},
        "workflow_name": "test-workflow",
        "request_id": request_id,
        "user_id": "test-user",
        "session_id": "test-session",
        "execution_order": [
            {
                "node": "root",
                "parent": None,
                "context_id": None,
                "contain_generation": False,
            },
            {
                "node": "child-1",
                "parent": "root",
                "context_id": None,
                "contain_generation": False,
            },
            {
                "node": "llm-node",
                "parent": "root",
                "context_id": None,
                "contain_generation": True,
            },
        ],
        "nodes_trace_data": {
            "root": {
                "name": "root",
                "input": {"workflow": "test"},
                "output": {"status": "completed"},
                "metadata": {"version": "1.0"},
            },
            "child-1": {
                "name": "child-1",
                "input": {"step": 1},
                "output": {"processed": True},
                "metadata": {},
            },
            "llm-node": {
                "name": "llm-node",
                "model": "gpt-4",
                "input": {"prompt": "Test prompt"},
                "output": {"completion": "Test response"},
                "metadata": {"temperature": 0.7},
                "usage": {
                    "prompt_tokens": 10,
                    "completion_tokens": 20,
                    "total_tokens": 30,
                },
            },
        },
    }

    # Mock get_hub to return our mock client
    mock_hub = MagicMock()
    mock_hub.langfuse.return_value = mock_client

    with patch("hush.core.registry.get_hub", return_value=mock_hub):
        # Call flush directly (this runs synchronously for testing)
        LangfuseTracer.flush(flush_data)

    print(f"\nTrace created with request_id: {request_id}")
    print("Check Langfuse dashboard: https://cloud.langfuse.com")


if __name__ == "__main__":
    # Run integration tests directly
    print("Running LangfuseTracer integration tests...")
    print("=" * 60)

    print("\n1. Testing LangfuseConfig creation...")
    test_langfuse_config_creation()
    print("   PASSED")

    print("\n2. Testing LangfuseClient creation...")
    test_langfuse_client_creation()
    print("   PASSED")

    print("\n3. Testing LangfuseTracer creation...")
    test_langfuse_tracer_creation()
    print("   PASSED")

    print("\n4. Testing tracer config serialization...")
    test_tracer_config_serialization()
    print("   PASSED")

    print("\n5. Testing LangfuseTracer registration...")
    test_langfuse_tracer_registered()
    print("   PASSED")

    print("\n6. Testing Langfuse cloud connection...")
    try:
        test_langfuse_cloud_connection()
        print("   PASSED")
    except Exception as e:
        print(f"   FAILED: {e}")

    print("\n7. Testing LangfuseTracer.flush() with ResourceHub...")
    try:
        test_langfuse_tracer_flush_with_resource_hub()
        print("   PASSED")
    except Exception as e:
        print(f"   FAILED: {e}")

    print("\n" + "=" * 60)
    print("All tests completed!")
