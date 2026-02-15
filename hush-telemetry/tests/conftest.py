"""Pytest configuration and shared fixtures for hush-telemetry tests."""

import os
import sys
import uuid
from pathlib import Path

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
║  To run hush-telemetry tests, you need to configure credentials.   ║
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
    os.environ["COLUMNS"] = "200"

    config.addinivalue_line(
        "markers", "integration: mark test as integration test (requires credentials)"
    )


def pytest_collection_modifyitems(config, items):
    """Skip integration tests if HUSH_CONFIG is not set."""
    has_config = os.environ.get("HUSH_CONFIG") and CONFIGS_PATH.exists()
    if not has_config:
        skip_marker = pytest.mark.skip(
            reason="HUSH_CONFIG not set or config file not found"
        )
        for item in items:
            if "integration" in item.keywords:
                item.add_marker(skip_marker)


# ============================================================================
# Test Data Fixtures — new tracing format (graph_structure + records)
# ============================================================================


@pytest.fixture
def sample_request_id():
    """Generate a unique request ID for testing."""
    return f"test-{uuid.uuid4()}"


@pytest.fixture
def sample_trace_data(sample_request_id):
    """Create sample trace data in the new format (graph_structure + records)."""
    return {
        "workflow_name": "test-workflow",
        "request_id": sample_request_id,
        "user_id": "test-user",
        "session_id": "test-session",
        "tags": ["test", "unit"],
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
                "start_time": "2024-01-15T10:00:00Z",
                "end_time": "2024-01-15T10:00:01Z",
                "duration_ms": 1000.0,
                "metadata": {"version": "1.0"},
            },
            {
                "op_name": "root.child-1",
                "context_id": None,
                "inputs": {"step": 1},
                "outputs": {"processed": True},
                "start_time": "2024-01-15T10:00:00.100Z",
                "end_time": "2024-01-15T10:00:00.500Z",
                "duration_ms": 400.0,
                "metadata": {},
            },
            {
                "op_name": "root.llm-node",
                "context_id": None,
                "inputs": {"prompt": "Test prompt"},
                "outputs": {"completion": "Test response"},
                "start_time": "2024-01-15T10:00:00.500Z",
                "end_time": "2024-01-15T10:00:00.900Z",
                "duration_ms": 400.0,
                "model": "gpt-4",
                "usage": {
                    "prompt_tokens": 10,
                    "completion_tokens": 20,
                    "total_tokens": 30,
                },
                "cost": 0.0015,
                "metadata": {"temperature": 0.7},
            },
        ],
    }


@pytest.fixture
def sample_iteration_trace_data(sample_request_id):
    """Create trace data with iteration context (MapOp, ForLoop)."""
    return {
        "workflow_name": "iteration-workflow",
        "request_id": sample_request_id,
        "user_id": "test-user",
        "session_id": "test-session",
        "tags": ["iteration", "test"],
        "graph_structure": [
            {
                "op_name": "root",
                "op_type": "graph",
                "parent_name": None,
                "contain_generation": False,
            },
            {
                "op_name": "root.map_op",
                "op_type": "map",
                "parent_name": "root",
                "contain_generation": False,
            },
            {
                "op_name": "root.map_op.process",
                "op_type": "code",
                "parent_name": "root.map_op",
                "contain_generation": False,
            },
            {
                "op_name": "root.aggregate",
                "op_type": "code",
                "parent_name": "root",
                "contain_generation": False,
            },
        ],
        "records": [
            {
                "op_name": "root",
                "context_id": None,
                "inputs": {"items": [1, 2, 3]},
                "outputs": {"result": 6},
                "start_time": None,
                "end_time": None,
                "duration_ms": None,
            },
            {
                "op_name": "root.map_op",
                "context_id": None,
                "inputs": {},
                "outputs": {},
                "start_time": None,
                "end_time": None,
                "duration_ms": None,
            },
            {
                "op_name": "root.map_op.process",
                "context_id": "[0]",
                "inputs": {"item": 1},
                "outputs": {"doubled": 2},
                "start_time": None,
                "end_time": None,
                "duration_ms": None,
            },
            {
                "op_name": "root.map_op.process",
                "context_id": "[1]",
                "inputs": {"item": 2},
                "outputs": {"doubled": 4},
                "start_time": None,
                "end_time": None,
                "duration_ms": None,
            },
            {
                "op_name": "root.map_op.process",
                "context_id": "[2]",
                "inputs": {"item": 3},
                "outputs": {"doubled": 6},
                "start_time": None,
                "end_time": None,
                "duration_ms": None,
            },
            {
                "op_name": "root.aggregate",
                "context_id": None,
                "inputs": {"values": [2, 4, 6]},
                "outputs": {"sum": 12},
                "start_time": None,
                "end_time": None,
                "duration_ms": None,
            },
        ],
    }


# ============================================================================
# Tracer Fixtures
# ============================================================================


@pytest.fixture
def langfuse_tracer():
    """Create LangfuseTracer with test resource key."""
    from hush.telemetry import LangfuseTracer

    return LangfuseTracer(resource="langfuse:default")


@pytest.fixture
def langfuse_tracer_with_tags():
    """Create LangfuseTracer with static tags."""
    from hush.telemetry import LangfuseTracer

    return LangfuseTracer(resource="langfuse:default", tags=["test", "unit"])


@pytest.fixture
def otel_tracer():
    """Create OTELTracer with test resource key."""
    from hush.telemetry import OTELTracer

    return OTELTracer(resource="otel:jaeger")


@pytest.fixture
def otel_tracer_with_config():
    """Create OTELTracer with direct config."""
    from hush.telemetry import OTELConfig, OTELTracer

    config = OTELConfig.jaeger()
    return OTELTracer(config=config)


# ============================================================================
# Session Fixtures
# ============================================================================


@pytest.fixture(scope="session", autouse=True)
def setup_resource_hub():
    """Setup ResourceHub with configurations for the entire test session.

    Gracefully handles missing config — unit tests don't need ResourceHub.
    """
    if not os.environ.get("HUSH_CONFIG") or not CONFIGS_PATH.exists():
        yield None
        return

    from hush.core.registry import ResourceHub, set_global_hub

    try:
        from hush.providers.registry import EmbeddingPlugin, LLMPlugin, RerankPlugin  # noqa: F401
    except ImportError:
        pass

    hub = ResourceHub.from_yaml(CONFIGS_PATH)
    set_global_hub(hub)
    ResourceHub.set_instance(hub)

    yield hub

    ResourceHub._instance = None


@pytest.fixture
def hub(setup_resource_hub):
    """Get the ResourceHub instance."""
    return setup_resource_hub
