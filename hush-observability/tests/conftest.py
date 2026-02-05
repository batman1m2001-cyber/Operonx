"""Pytest configuration and shared fixtures for hush-observability tests."""

import os
import sys
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict

import pytest
from dotenv import load_dotenv

# Load .env file from package root
load_dotenv(Path(__file__).parent.parent / ".env")

# Also try loading from monorepo root
load_dotenv(Path(__file__).parent.parent.parent / ".env")

# Get config path from environment
CONFIGS_PATH = Path(os.environ.get("HUSH_CONFIG", ""))

# =============================================================================
# Configuration Validation
# =============================================================================

SETUP_TUTORIAL = """
╔══════════════════════════════════════════════════════════════════════════════╗
║                 HUSH OBSERVABILITY TEST CONFIGURATION REQUIRED               ║
╠══════════════════════════════════════════════════════════════════════════════╣
║                                                                              ║
║  To run hush-observability tests, you need to configure credentials.         ║
║                                                                              ║
║  STEP 1: Create .env file                                                    ║
║  ─────────────────────────────────────────────────────────────────────────── ║
║  Copy .env.template to .env in the project root:                             ║
║                                                                              ║
║    cp .env.template .env                                                     ║
║                                                                              ║
║  STEP 2: Set HUSH_CONFIG path                                                ║
║  ─────────────────────────────────────────────────────────────────────────── ║
║  Add to your .env file:                                                      ║
║                                                                              ║
║    HUSH_CONFIG=/path/to/your/resources.yaml                                  ║
║                                                                              ║
║  STEP 3: Add observability credentials to .env                               ║
║  ─────────────────────────────────────────────────────────────────────────── ║
║  For Langfuse tests:                                                         ║
║    LANGFUSE_PUBLIC_KEY=pk-lf-your-key                                        ║
║    LANGFUSE_SECRET_KEY=sk-lf-your-key                                        ║
║    LANGFUSE_HOST=https://cloud.langfuse.com                                  ║
║    Get keys at: https://cloud.langfuse.com                                   ║
║                                                                              ║
║  STEP 4: Configure resources.yaml                                            ║
║  ─────────────────────────────────────────────────────────────────────────── ║
║  Add observability configs to resources.yaml:                                ║
║                                                                              ║
║    langfuse:default:                                                         ║
║      type: langfuse                                                          ║
║      public_key: ${LANGFUSE_PUBLIC_KEY}                                      ║
║      secret_key: ${LANGFUSE_SECRET_KEY}                                      ║
║      host: ${LANGFUSE_HOST}                                                  ║
║                                                                              ║
║    otel:jaeger:                                                              ║
║      type: otel                                                              ║
║      endpoint: http://localhost:4317                                         ║
║      protocol: grpc                                                          ║
║      service_name: hush-workflow                                             ║
║      insecure: true                                                          ║
║                                                                              ║
╚══════════════════════════════════════════════════════════════════════════════╝
"""


def pytest_configure(config):
    """Configure pytest environment."""
    # Set terminal width for Rich console output
    os.environ["COLUMNS"] = "200"

    # Check if HUSH_CONFIG is set
    if not os.environ.get("HUSH_CONFIG"):
        print(SETUP_TUTORIAL, file=sys.stderr)
        pytest.exit(
            "HUSH_CONFIG environment variable not set. "
            "Please follow the setup tutorial above.",
            returncode=1
        )

    # Check if config file exists
    if not CONFIGS_PATH.exists():
        print(SETUP_TUTORIAL, file=sys.stderr)
        pytest.exit(
            f"Config file not found: {CONFIGS_PATH}\n"
            "Please create resources.yaml or update HUSH_CONFIG path.",
            returncode=1
        )

    # Register custom markers
    config.addinivalue_line(
        "markers",
        "integration: mark test as integration test (requires credentials)"
    )


# ============================================================================
# Test Data Fixtures
# ============================================================================


@pytest.fixture
def sample_request_id():
    """Generate a unique request ID for testing."""
    return f"test-{uuid.uuid4()}"


@pytest.fixture
def sample_flush_data(sample_request_id):
    """Create sample flush data structure for tracer tests."""
    return {
        "tracer_type": "LangfuseTracer",
        "tracer_config": {"resource_key": "langfuse:test"},
        "workflow_name": "test-workflow",
        "request_id": sample_request_id,
        "user_id": "test-user",
        "session_id": "test-session",
        "tags": ["test", "unit"],
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
                "name": "test-workflow.root",
                "start_time": "2024-01-15T10:00:00Z",
                "end_time": "2024-01-15T10:00:01Z",
                "input": {"workflow": "test"},
                "output": {"status": "completed"},
                "metadata": {"version": "1.0"},
            },
            "child-1": {
                "name": "test-workflow.child-1",
                "start_time": "2024-01-15T10:00:00.100Z",
                "end_time": "2024-01-15T10:00:00.500Z",
                "input": {"step": 1},
                "output": {"processed": True},
                "metadata": {},
            },
            "llm-node": {
                "name": "test-workflow.llm-node",
                "start_time": "2024-01-15T10:00:00.500Z",
                "end_time": "2024-01-15T10:00:00.900Z",
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


@pytest.fixture
def sample_iteration_flush_data(sample_request_id):
    """Create flush data with iteration context (MapNode, ForLoop)."""
    return {
        "tracer_type": "LangfuseTracer",
        "tracer_config": {"resource_key": "langfuse:test"},
        "workflow_name": "iteration-workflow",
        "request_id": sample_request_id,
        "user_id": "test-user",
        "session_id": "test-session",
        "tags": ["iteration", "test"],
        "execution_order": [
            {"node": "root", "parent": None, "context_id": None, "contain_generation": False},
            {"node": "map_node", "parent": "root", "context_id": None, "contain_generation": False},
            {"node": "process", "parent": "map_node", "context_id": "[0]", "contain_generation": False},
            {"node": "process", "parent": "map_node", "context_id": "[1]", "contain_generation": False},
            {"node": "process", "parent": "map_node", "context_id": "[2]", "contain_generation": False},
            {"node": "aggregate", "parent": "root", "context_id": None, "contain_generation": False},
        ],
        "nodes_trace_data": {
            "root": {"name": "root", "input": {"items": [1, 2, 3]}, "output": {"result": 6}},
            "map_node": {"name": "map_node", "input": {}, "output": {}},
            "process:[0]": {"name": "process", "input": {"item": 1}, "output": {"doubled": 2}},
            "process:[1]": {"name": "process", "input": {"item": 2}, "output": {"doubled": 4}},
            "process:[2]": {"name": "process", "input": {"item": 3}, "output": {"doubled": 6}},
            "aggregate": {"name": "aggregate", "input": {"values": [2, 4, 6]}, "output": {"sum": 12}},
        },
    }


# ============================================================================
# Mock Fixtures
# ============================================================================


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
        self.trace_metadata = {}
        self.tags = []
        self._indexer = MockIndexer()
        self.has_trace_store = False
        self._trace_store = None

    def add_execution(
        self, node_id: str, parent_id: str = None, context_id: str = None
    ):
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


@pytest.fixture
def mock_node():
    """Create a mock node factory."""
    return MockNode


@pytest.fixture
def mock_state():
    """Create a mock MemoryState."""
    return MockMemoryState()


# ============================================================================
# Tracer Fixtures
# ============================================================================


@pytest.fixture
def langfuse_tracer():
    """Create LangfuseTracer with test resource key."""
    from hush.observability import LangfuseTracer
    return LangfuseTracer(resource_key="langfuse:default")


@pytest.fixture
def langfuse_tracer_with_tags():
    """Create LangfuseTracer with static tags."""
    from hush.observability import LangfuseTracer
    return LangfuseTracer(resource_key="langfuse:default", tags=["test", "unit"])


@pytest.fixture
def otel_tracer():
    """Create OTELTracer with test resource key."""
    from hush.observability import OTELTracer
    return OTELTracer(resource_key="otel:jaeger")


@pytest.fixture
def otel_tracer_with_config():
    """Create OTELTracer with direct config."""
    from hush.observability import OTELConfig, OTELTracer
    config = OTELConfig.jaeger()
    return OTELTracer(config=config)


@pytest.fixture
def local_tracer():
    """Create LocalTracer for testing."""
    from hush.core.tracers import LocalTracer
    return LocalTracer(name="test", tags=["local", "test"])


# ============================================================================
# Session Fixtures
# ============================================================================


@pytest.fixture(scope="session", autouse=True)
def setup_resource_hub():
    """Setup ResourceHub with configurations for the entire test session."""
    from hush.core.registry import ResourceHub, set_global_hub

    # Import observability plugin to register Langfuse/OTEL configs
    from hush.observability.plugin import ObservabilityPlugin

    # Optionally import providers plugin if available (for full integration tests)
    try:
        from hush.providers.registry import LLMPlugin, EmbeddingPlugin, RerankPlugin
    except ImportError:
        pass  # hush-providers not installed, skip

    # Create hub from config file
    hub = ResourceHub.from_yaml(CONFIGS_PATH)
    set_global_hub(hub)
    ResourceHub.set_instance(hub)

    yield hub

    # Cleanup
    ResourceHub._instance = None


@pytest.fixture
def hub(setup_resource_hub):
    """Get the ResourceHub instance."""
    return setup_resource_hub


@pytest.fixture(scope="session", autouse=True)
def cleanup_after_tests():
    """Cleanup after all tests complete."""
    yield
    # Shutdown background worker gracefully
    try:
        from hush.core.tracers import BaseTracer
        BaseTracer.shutdown_executor()
    except Exception:
        pass
