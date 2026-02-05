"""Test OTELTracer with OpenTelemetry backend.

This module tests the OTELTracer implementation including:
- Tracer creation with direct config and resource_key
- Config serialization for subprocess
- Tracer registration
- Flush method with mocked OTEL client
- Helper methods (datetime conversion, short name extraction)
"""

import uuid
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest


# ============================================================================
# Config and Client Tests
# ============================================================================


class TestOTELConfig:
    """Test OTELConfig creation and methods."""

    def test_config_creation_basic(self):
        """Test basic OTELConfig creation."""
        from hush.observability import OTELConfig

        config = OTELConfig(
            endpoint="http://localhost:4317",
            protocol="grpc",
            service_name="test-service",
        )

        assert config.endpoint == "http://localhost:4317"
        assert config.protocol == "grpc"
        assert config.service_name == "test-service"
        assert config.insecure is False
        assert config.timeout == 30

    def test_config_creation_with_headers(self):
        """Test OTELConfig creation with custom headers."""
        from hush.observability import OTELConfig

        config = OTELConfig(
            endpoint="https://tempo.example.com:4317",
            protocol="grpc",
            headers={"Authorization": "Bearer test-token"},
            service_name="my-service",
        )

        assert config.headers == {"Authorization": "Bearer test-token"}

    def test_config_http_protocol(self):
        """Test OTELConfig with HTTP protocol."""
        from hush.observability import OTELConfig

        config = OTELConfig(
            endpoint="http://localhost:4318/v1/traces",
            protocol="http",
            service_name="http-service",
        )

        assert config.protocol == "http"

    def test_config_jaeger_factory(self):
        """Test OTELConfig.jaeger() factory method."""
        from hush.observability import OTELConfig

        config = OTELConfig.jaeger()

        assert config.endpoint == "http://localhost:4317"
        assert config.protocol == "grpc"
        assert config.insecure is True

    def test_config_jaeger_custom_host(self):
        """Test OTELConfig.jaeger() with custom host and port."""
        from hush.observability import OTELConfig

        config = OTELConfig.jaeger(host="jaeger.local", port=14250)

        assert config.endpoint == "http://jaeger.local:14250"

    def test_config_tempo_factory(self):
        """Test OTELConfig.tempo() factory method."""
        from hush.observability import OTELConfig

        config = OTELConfig.tempo(
            endpoint="https://tempo.grafana.net",
            api_key="test-api-key",
        )

        assert config.endpoint == "https://tempo.grafana.net"
        assert config.headers == {"Authorization": "Bearer test-api-key"}

    def test_config_model_dump(self):
        """Test OTELConfig serialization via model_dump."""
        from hush.observability import OTELConfig

        config = OTELConfig(
            endpoint="http://localhost:4317",
            protocol="grpc",
            service_name="test-service",
            headers={"X-Custom": "value"},
        )

        dumped = config.model_dump()

        assert dumped["endpoint"] == "http://localhost:4317"
        assert dumped["protocol"] == "grpc"
        assert dumped["service_name"] == "test-service"
        assert dumped["headers"] == {"X-Custom": "value"}


class TestOTELClient:
    """Test OTELClient creation and methods."""

    def test_client_creation(self):
        """Test OTELClient can be created."""
        from hush.observability import OTELClient, OTELConfig

        config = OTELConfig(
            endpoint="http://localhost:4317",
            protocol="grpc",
            service_name="test-service",
        )
        client = OTELClient(config)

        assert client.config == config
        assert repr(client) == "<OTELClient endpoint=http://localhost:4317 protocol=grpc>"

    def test_client_lazy_initialization(self):
        """Test OTELClient uses lazy initialization."""
        from hush.observability import OTELClient, OTELConfig

        config = OTELConfig(
            endpoint="http://localhost:4317",
            protocol="grpc",
            service_name="test-service",
        )
        client = OTELClient(config)

        # Should not be initialized until accessed
        assert client._initialized is False


# ============================================================================
# Tracer Tests
# ============================================================================


class TestOTELTracer:
    """Test OTELTracer creation and configuration."""

    def test_tracer_creation_with_resource_key(self):
        """Test OTELTracer creation with resource_key."""
        from hush.observability import OTELTracer

        tracer = OTELTracer(resource_key="otel:jaeger")

        assert tracer.resource_key == "otel:jaeger"
        assert repr(tracer) == "<OTELTracer resource_key=otel:jaeger>"

    def test_tracer_creation_with_config(self):
        """Test OTELTracer creation with direct config."""
        from hush.observability import OTELConfig, OTELTracer

        config = OTELConfig.jaeger()
        tracer = OTELTracer(config=config)

        assert tracer._config == config
        assert tracer.resource_key is None
        assert "endpoint=" in repr(tracer)

    def test_tracer_creation_with_tags(self):
        """Test OTELTracer creation with static tags."""
        from hush.observability import OTELTracer

        tracer = OTELTracer(resource_key="otel:jaeger", tags=["prod", "ml-team"])

        assert tracer.tags == ["prod", "ml-team"]

    def test_tracer_requires_config_or_resource_key(self):
        """Test OTELTracer raises error if neither config nor resource_key provided."""
        from hush.observability import OTELTracer

        with pytest.raises(ValueError, match="Must provide either"):
            OTELTracer()

    def test_tracer_rejects_both_config_and_resource_key(self):
        """Test OTELTracer raises error if both config and resource_key provided."""
        from hush.observability import OTELConfig, OTELTracer

        config = OTELConfig.jaeger()

        with pytest.raises(ValueError, match="Cannot provide both"):
            OTELTracer(config=config, resource_key="otel:jaeger")

    def test_tracer_config_serialization_resource_key(self):
        """Test tracer config returns resource_key for subprocess."""
        from hush.observability import OTELTracer

        tracer = OTELTracer(resource_key="otel:jaeger")

        tracer_config = tracer._get_tracer_config()
        assert tracer_config["resource_key"] == "otel:jaeger"
        assert "config" not in tracer_config

    def test_tracer_config_serialization_direct_config(self):
        """Test tracer config returns serialized config for subprocess."""
        from hush.observability import OTELConfig, OTELTracer

        config = OTELConfig(
            endpoint="http://localhost:4317",
            protocol="grpc",
            service_name="test-service",
        )
        tracer = OTELTracer(config=config)

        tracer_config = tracer._get_tracer_config()
        assert "config" in tracer_config
        assert tracer_config["config"]["endpoint"] == "http://localhost:4317"
        assert "resource_key" not in tracer_config

    def test_tracer_registered(self):
        """Test OTELTracer is registered in tracer registry."""
        from hush.core.tracers import get_registered_tracers
        from hush.observability import OTELTracer  # noqa: F401

        tracers = get_registered_tracers()
        assert "OTELTracer" in tracers


# ============================================================================
# Helper Method Tests
# ============================================================================


class TestOTELTracerHelpers:
    """Test OTELTracer helper methods."""

    def test_datetime_to_ns_with_datetime(self):
        """Test _datetime_to_ns with datetime object."""
        from hush.observability.tracers.otel import OTELTracer

        dt = datetime(2024, 1, 15, 10, 30, 0, tzinfo=timezone.utc)
        ns = OTELTracer._datetime_to_ns(dt)

        assert ns is not None
        assert isinstance(ns, int)
        # Should be nanoseconds (10^9 * seconds since epoch)
        assert ns == int(dt.timestamp() * 1_000_000_000)

    def test_datetime_to_ns_with_iso_string(self):
        """Test _datetime_to_ns with ISO format string."""
        from hush.observability.tracers.otel import OTELTracer

        iso_str = "2024-01-15T10:30:00+00:00"
        ns = OTELTracer._datetime_to_ns(iso_str)

        assert ns is not None
        assert isinstance(ns, int)

    def test_datetime_to_ns_with_z_suffix(self):
        """Test _datetime_to_ns with Z suffix for UTC."""
        from hush.observability.tracers.otel import OTELTracer

        iso_str = "2024-01-15T10:30:00Z"
        ns = OTELTracer._datetime_to_ns(iso_str)

        assert ns is not None
        assert isinstance(ns, int)

    def test_datetime_to_ns_with_none(self):
        """Test _datetime_to_ns returns None for None input."""
        from hush.observability.tracers.otel import OTELTracer

        assert OTELTracer._datetime_to_ns(None) is None

    def test_get_short_name(self):
        """Test _get_short_name extracts last part after dot."""
        from hush.observability.tracers.otel import OTELTracer

        assert OTELTracer._get_short_name("workflow.node.child") == "child"
        assert OTELTracer._get_short_name("simple") == "simple"
        assert OTELTracer._get_short_name("") == ""

    def test_get_short_name_with_empty(self):
        """Test _get_short_name with empty string."""
        from hush.observability.tracers.otel import OTELTracer

        assert OTELTracer._get_short_name("") == ""
        assert OTELTracer._get_short_name(None) is None


# ============================================================================
# Flush Method Tests (with mocks)
# ============================================================================


class TestOTELTracerFlush:
    """Test OTELTracer.flush() method with mocked backend."""

    @pytest.fixture
    def mock_flush_data(self):
        """Create mock flush data structure."""
        return {
            "tracer_type": "OTELTracer",
            "tracer_config": {"resource_key": "otel:test"},
            "workflow_name": "test-workflow",
            "request_id": str(uuid.uuid4()),
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
    def mock_flush_data_with_direct_config(self):
        """Create mock flush data with direct config."""
        return {
            "tracer_type": "OTELTracer",
            "tracer_config": {
                "config": {
                    "endpoint": "http://localhost:4317",
                    "protocol": "grpc",
                    "service_name": "test-service",
                    "headers": None,
                    "service_version": None,
                    "insecure": True,
                    "timeout": 30,
                    "enabled": True,
                    "sample_rate": 1.0,
                }
            },
            "workflow_name": "test-workflow",
            "request_id": str(uuid.uuid4()),
            "user_id": "test-user",
            "session_id": "test-session",
            "tags": ["otel-test"],
            "execution_order": [
                {
                    "node": "root",
                    "parent": None,
                    "context_id": None,
                    "contain_generation": False,
                },
            ],
            "nodes_trace_data": {
                "root": {
                    "name": "test-workflow.root",
                    "start_time": "2024-01-15T10:00:00Z",
                    "end_time": "2024-01-15T10:00:01Z",
                    "input": {"test": True},
                    "output": {"success": True},
                    "metadata": {},
                },
            },
        }

    def test_flush_with_resource_key(self, mock_flush_data):
        """Test flush with resource_key creates spans correctly."""
        from hush.observability import OTELTracer

        # Mock the OTEL dependencies
        mock_tracer = MagicMock()
        mock_span = MagicMock()
        mock_tracer.start_span.return_value = mock_span

        mock_client = MagicMock()
        mock_client.tracer = mock_tracer

        mock_hub = MagicMock()
        mock_hub.otel.return_value = mock_client

        with patch("hush.core.registry.get_hub", return_value=mock_hub):
            with patch("opentelemetry.trace.set_span_in_context") as mock_set_ctx:
                mock_set_ctx.return_value = MagicMock()
                OTELTracer.flush(mock_flush_data)

        # Verify client was obtained from hub
        mock_hub.otel.assert_called_once_with("otel:test")

        # Verify spans were created
        assert mock_tracer.start_span.called

        # Verify flush was called
        mock_client.flush.assert_called_once()

    def test_flush_with_direct_config(self, mock_flush_data_with_direct_config):
        """Test flush with direct config creates client correctly."""
        from hush.observability import OTELTracer

        # Mock the OTEL client
        mock_tracer = MagicMock()
        mock_span = MagicMock()
        mock_tracer.start_span.return_value = mock_span

        with patch("hush.observability.backends.otel.OTELClient") as MockClient:
            mock_client = MagicMock()
            mock_client.tracer = mock_tracer
            MockClient.return_value = mock_client

            with patch("opentelemetry.trace.set_span_in_context") as mock_set_ctx:
                mock_set_ctx.return_value = MagicMock()
                OTELTracer.flush(mock_flush_data_with_direct_config)

        # Verify client was created with config
        MockClient.assert_called_once()

        # Verify flush was called
        mock_client.flush.assert_called_once()

    def test_flush_creates_correct_attributes(self, mock_flush_data):
        """Test flush creates correct span attributes."""
        from hush.observability import OTELTracer

        captured_attributes = {}

        mock_tracer = MagicMock()

        def capture_span(*args, **kwargs):
            if "attributes" in kwargs:
                captured_attributes.update(kwargs["attributes"])
            mock_span = MagicMock()
            return mock_span

        mock_tracer.start_span.side_effect = capture_span

        mock_client = MagicMock()
        mock_client.tracer = mock_tracer

        mock_hub = MagicMock()
        mock_hub.otel.return_value = mock_client

        with patch("hush.core.registry.get_hub", return_value=mock_hub):
            with patch("opentelemetry.trace.set_span_in_context") as mock_set_ctx:
                mock_set_ctx.return_value = MagicMock()
                OTELTracer.flush(mock_flush_data)

        # Check that workflow attributes are set
        assert "workflow.name" in captured_attributes
        assert captured_attributes["workflow.name"] == "test-workflow"

    def test_flush_with_context_aware_nodes(self):
        """Test flush correctly handles context-aware nodes (from iteration)."""
        from hush.observability import OTELTracer

        flush_data = {
            "tracer_type": "OTELTracer",
            "tracer_config": {"resource_key": "otel:test"},
            "workflow_name": "iteration-workflow",
            "request_id": str(uuid.uuid4()),
            "user_id": None,
            "session_id": None,
            "tags": [],
            "execution_order": [
                {"node": "root", "parent": None, "context_id": None, "contain_generation": False},
                {"node": "loop", "parent": "root", "context_id": None, "contain_generation": False},
                {"node": "process", "parent": "loop", "context_id": "[0]", "contain_generation": False},
                {"node": "process", "parent": "loop", "context_id": "[1]", "contain_generation": False},
            ],
            "nodes_trace_data": {
                "root": {"name": "root", "input": {}, "output": {}},
                "loop": {"name": "loop", "input": {}, "output": {}},
                "process:[0]": {"name": "process", "input": {"i": 0}, "output": {"r": 0}},
                "process:[1]": {"name": "process", "input": {"i": 1}, "output": {"r": 1}},
            },
        }

        mock_tracer = MagicMock()
        mock_span = MagicMock()
        mock_tracer.start_span.return_value = mock_span

        mock_client = MagicMock()
        mock_client.tracer = mock_tracer

        mock_hub = MagicMock()
        mock_hub.otel.return_value = mock_client

        with patch("hush.core.registry.get_hub", return_value=mock_hub):
            with patch("opentelemetry.trace.set_span_in_context") as mock_set_ctx:
                mock_set_ctx.return_value = MagicMock()
                OTELTracer.flush(flush_data)

        # Should have created 4 spans (root, loop, process:[0], process:[1])
        assert mock_tracer.start_span.call_count == 4


# ============================================================================
# Integration with Langfuse via OTEL
# ============================================================================


class TestOTELToLangfuse:
    """Test OTEL tracer sending to Langfuse OTEL endpoint."""

    def test_create_langfuse_otel_config(self):
        """Test creating OTELConfig for Langfuse endpoint."""
        import base64

        from hush.observability import OTELConfig

        # Create config for Langfuse OTEL endpoint
        public_key = "pk-test"
        secret_key = "sk-test"
        host = "https://cloud.langfuse.com"

        auth = base64.b64encode(f"{public_key}:{secret_key}".encode()).decode()
        config = OTELConfig(
            endpoint=f"{host}/api/public/otel/v1/traces",
            protocol="http",
            headers={"Authorization": f"Basic {auth}"},
            service_name="hush-workflow",
        )

        assert config.protocol == "http"
        assert "/api/public/otel/v1/traces" in config.endpoint
        assert "Authorization" in config.headers


# ============================================================================
# Run tests directly
# ============================================================================

if __name__ == "__main__":
    print("Running OTELTracer unit tests...")
    print("=" * 60)

    print("\n1. Testing OTELConfig creation...")
    TestOTELConfig().test_config_creation_basic()
    print("   PASSED")

    print("\n2. Testing OTELConfig with headers...")
    TestOTELConfig().test_config_creation_with_headers()
    print("   PASSED")

    print("\n3. Testing OTELConfig.jaeger() factory...")
    TestOTELConfig().test_config_jaeger_factory()
    print("   PASSED")

    print("\n4. Testing OTELConfig.tempo() factory...")
    TestOTELConfig().test_config_tempo_factory()
    print("   PASSED")

    print("\n5. Testing OTELClient creation...")
    TestOTELClient().test_client_creation()
    print("   PASSED")

    print("\n6. Testing OTELTracer creation with resource_key...")
    TestOTELTracer().test_tracer_creation_with_resource_key()
    print("   PASSED")

    print("\n7. Testing OTELTracer creation with config...")
    TestOTELTracer().test_tracer_creation_with_config()
    print("   PASSED")

    print("\n8. Testing OTELTracer registration...")
    TestOTELTracer().test_tracer_registered()
    print("   PASSED")

    print("\n9. Testing datetime_to_ns helper...")
    TestOTELTracerHelpers().test_datetime_to_ns_with_datetime()
    print("   PASSED")

    print("\n10. Testing get_short_name helper...")
    TestOTELTracerHelpers().test_get_short_name()
    print("   PASSED")

    print("\n" + "=" * 60)
    print("All OTELTracer unit tests completed!")
