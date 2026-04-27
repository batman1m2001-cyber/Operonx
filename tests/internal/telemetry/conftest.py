"""Pytest configuration and shared fixtures for operonx-telemetry tests.

Discovery: walks up from this file to find the project root (nearest
``pyproject.toml``), then loads ``.env`` and ``resources.yaml`` from there.
Integration tests skip if ``resources.yaml`` is missing.
"""

import uuid
from pathlib import Path

import pytest
from dotenv import load_dotenv


def _find_project_root(start: Path) -> Path:
    """Walk up from *start* to find nearest directory containing pyproject.toml."""
    p = start.resolve()
    while p != p.parent:
        if (p / "pyproject.toml").exists():
            return p
        p = p.parent
    raise RuntimeError(f"No pyproject.toml found walking up from {start}")


ROOT = _find_project_root(Path(__file__).parent)
ENV_FILE = ROOT / ".env"
RESOURCES_FILE = ROOT / "resources.yaml"

# Auto-load .env (non-override, preserves shell env)
if ENV_FILE.exists():
    load_dotenv(ENV_FILE, override=False)


def pytest_configure(config):
    import os

    os.environ["COLUMNS"] = "200"
    config.addinivalue_line(
        "markers", "integration: mark test as integration test (requires credentials)"
    )


def pytest_collection_modifyitems(config, items):
    """Skip integration tests if resources.yaml is missing."""
    if RESOURCES_FILE.exists():
        return
    skip_marker = pytest.mark.skip(reason=f"resources.yaml not found at {RESOURCES_FILE}")
    for item in items:
        if "integration" in item.keywords:
            item.add_marker(skip_marker)


# ============================================================================
# Test Data Fixtures — nodes format (TraceNode tree)
# ============================================================================


@pytest.fixture
def sample_request_id():
    """Generate a unique request ID for testing."""
    return f"test-{uuid.uuid4()}"


@pytest.fixture
def sample_trace_data(sample_request_id):
    """Create sample trace data in nodes format (pre-computed TraceNode tree)."""
    return {
        "workflow_name": "test-workflow",
        "request_id": sample_request_id,
        "user_id": "test-user",
        "session_id": "test-session",
        "tags": ["test", "unit"],
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
                "start_time": "2024-01-15T10:00:00Z",
                "end_time": "2024-01-15T10:00:01Z",
                "duration_ms": 1000.0,
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
                "start_time": "2024-01-15T10:00:00.100Z",
                "end_time": "2024-01-15T10:00:00.500Z",
                "duration_ms": 400.0,
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
                "start_time": "2024-01-15T10:00:00.500Z",
                "end_time": "2024-01-15T10:00:00.900Z",
                "duration_ms": 400.0,
                "metadata": {"temperature": 0.7},
                "model": "gpt-4",
                "usage": {
                    "prompt_tokens": 10,
                    "completion_tokens": 20,
                    "total_tokens": 30,
                },
                "cost": 0.0015,
            },
        ],
    }


@pytest.fixture
def sample_iteration_trace_data(sample_request_id):
    """Create trace data with iteration context (stream contexts)."""
    return {
        "workflow_name": "iteration-workflow",
        "request_id": sample_request_id,
        "user_id": "test-user",
        "session_id": "test-session",
        "tags": ["iteration", "test"],
        "nodes": [
            {
                "trace_key": "root",
                "parent_trace_key": None,
                "op_name": "root",
                "display_name": "iteration-workflow",
                "node_type": "trace",
                "kind": "graph",
                "inputs": {"items": [1, 2, 3]},
                "outputs": {"result": 6},
            },
            {
                "trace_key": "root.map_op",
                "parent_trace_key": "root",
                "op_name": "root.map_op",
                "display_name": "map_op",
                "node_type": "span",
                "kind": "generator",
                "inputs": {},
                "outputs": {},
                "metadata": {"yield_count": 3},
            },
            {
                "trace_key": "$ctx:root:main.s0",
                "parent_trace_key": "root",
                "op_name": None,
                "display_name": "[0]",
                "node_type": "span",
                "kind": "stream_context",
                "inputs": {},
                "outputs": {},
                "metadata": {},
            },
            {
                "trace_key": "root.map_op.process:main.s0",
                "parent_trace_key": "$ctx:root:main.s0",
                "op_name": "root.map_op.process",
                "display_name": "process",
                "node_type": "span",
                "kind": "stream_item",
                "inputs": {"item": 1},
                "outputs": {"doubled": 2},
                "metadata": {"spawned_by": "root.map_op"},
            },
            {
                "trace_key": "$ctx:root:main.s1",
                "parent_trace_key": "root",
                "op_name": None,
                "display_name": "[1]",
                "node_type": "span",
                "kind": "stream_context",
                "inputs": {},
                "outputs": {},
                "metadata": {},
            },
            {
                "trace_key": "root.map_op.process:main.s1",
                "parent_trace_key": "$ctx:root:main.s1",
                "op_name": "root.map_op.process",
                "display_name": "process",
                "node_type": "span",
                "kind": "stream_item",
                "inputs": {"item": 2},
                "outputs": {"doubled": 4},
                "metadata": {"spawned_by": "root.map_op"},
            },
            {
                "trace_key": "$ctx:root:main.s2",
                "parent_trace_key": "root",
                "op_name": None,
                "display_name": "[2]",
                "node_type": "span",
                "kind": "stream_context",
                "inputs": {},
                "outputs": {},
                "metadata": {},
            },
            {
                "trace_key": "root.map_op.process:main.s2",
                "parent_trace_key": "$ctx:root:main.s2",
                "op_name": "root.map_op.process",
                "display_name": "process",
                "node_type": "span",
                "kind": "stream_item",
                "inputs": {"item": 3},
                "outputs": {"doubled": 6},
                "metadata": {"spawned_by": "root.map_op"},
            },
            {
                "trace_key": "root.aggregate",
                "parent_trace_key": "root",
                "op_name": "root.aggregate",
                "display_name": "aggregate",
                "node_type": "span",
                "kind": "batch",
                "inputs": {"values": [2, 4, 6]},
                "outputs": {"sum": 12},
            },
        ],
    }


# ============================================================================
# Tracer Fixtures
# ============================================================================


@pytest.fixture
def langfuse_tracer():
    """Create LangfuseTracer with test resource key."""
    from operonx.telemetry import LangfuseTracer

    return LangfuseTracer(resource="langfuse:default")


@pytest.fixture
def langfuse_tracer_with_tags():
    """Create LangfuseTracer with static tags."""
    from operonx.telemetry import LangfuseTracer

    return LangfuseTracer(resource="langfuse:default", tags=["test", "unit"])


@pytest.fixture
def otel_tracer():
    """Create OTELTracer with test resource key."""
    from operonx.telemetry import OTELTracer

    return OTELTracer(resource="otel:default")


@pytest.fixture
def otel_tracer_with_config():
    """Create OTELTracer with direct config."""
    from operonx.telemetry import OTELConfig, OTELTracer

    config = OTELConfig.jaeger()
    return OTELTracer(config=config)


# ============================================================================
# Session Fixtures
# ============================================================================


@pytest.fixture(scope="session", autouse=True)
def setup_resource_hub():
    """Setup ResourceHub from resources.yaml for the entire test session.

    Gracefully handles missing config — unit tests don't need ResourceHub.
    """
    if not RESOURCES_FILE.exists():
        yield None
        return

    from operonx.core.registry import ResourceHub

    try:
        import operonx.providers  # noqa: F401 — register provider plugins
    except ImportError:
        pass

    hub = ResourceHub.from_yaml(RESOURCES_FILE)
    ResourceHub.set_instance(hub)

    yield hub

    ResourceHub.reset_instance()


@pytest.fixture
def hub(setup_resource_hub):
    """Get the ResourceHub instance."""
    return setup_resource_hub
