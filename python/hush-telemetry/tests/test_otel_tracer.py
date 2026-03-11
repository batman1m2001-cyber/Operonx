"""Test OTELTracer with the new tracing API.

Tests cover:
- OTELConfig/Client creation
- OTELTracer creation and validation
- Inheritance from hush.core.tracing.Tracer
- Helper methods (datetime conversion, short name extraction)
- flush() with nodes format (pre-computed TraceNode tree)
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
        from hush.telemetry import OTELConfig

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
        from hush.telemetry import OTELConfig

        config = OTELConfig(
            endpoint="https://tempo.example.com:4317",
            protocol="grpc",
            headers={"Authorization": "Bearer test-token"},
            service_name="my-service",
        )
        assert config.headers == {"Authorization": "Bearer test-token"}

    def test_config_http_protocol(self):
        from hush.telemetry import OTELConfig

        config = OTELConfig(
            endpoint="http://localhost:4318/v1/traces",
            protocol="http",
            service_name="http-service",
        )
        assert config.protocol == "http"

    def test_config_jaeger_factory(self):
        from hush.telemetry import OTELConfig

        config = OTELConfig.jaeger()
        assert config.endpoint == "http://localhost:4317"
        assert config.protocol == "grpc"
        assert config.insecure is True

    def test_config_jaeger_custom_host(self):
        from hush.telemetry import OTELConfig

        config = OTELConfig.jaeger(host="jaeger.local", port=14250)
        assert config.endpoint == "http://jaeger.local:14250"

    def test_config_tempo_factory(self):
        from hush.telemetry import OTELConfig

        config = OTELConfig.tempo(
            endpoint="https://tempo.grafana.net",
            api_key="test-api-key",
        )
        assert config.endpoint == "https://tempo.grafana.net"
        assert config.headers == {"Authorization": "Bearer test-api-key"}

    def test_config_model_dump(self):
        from hush.telemetry import OTELConfig

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
        from hush.telemetry import OTELClient, OTELConfig

        config = OTELConfig(
            endpoint="http://localhost:4317",
            protocol="grpc",
            service_name="test-service",
        )
        client = OTELClient(config)
        assert client.config == config
        assert repr(client) == "<OTELClient endpoint=http://localhost:4317 protocol=grpc>"

    def test_client_lazy_initialization(self):
        from hush.telemetry import OTELClient, OTELConfig

        config = OTELConfig(
            endpoint="http://localhost:4317",
            protocol="grpc",
            service_name="test-service",
        )
        client = OTELClient(config)
        assert client._initialized is False


# ============================================================================
# Tracer Tests
# ============================================================================


class TestOTELTracer:
    """Test OTELTracer creation and configuration."""

    def test_tracer_creation_with_resource(self):
        from hush.telemetry import OTELTracer

        tracer = OTELTracer(resource="otel:jaeger")
        assert tracer.resource == "otel:jaeger"
        assert repr(tracer) == "<OTELTracer resource=otel:jaeger>"

    def test_tracer_creation_with_config(self):
        from hush.telemetry import OTELConfig, OTELTracer

        config = OTELConfig.jaeger()
        tracer = OTELTracer(config=config)
        assert tracer._config == config
        assert tracer.resource is None
        assert "endpoint=" in repr(tracer)

    def test_tracer_creation_with_tags(self):
        from hush.telemetry import OTELTracer

        tracer = OTELTracer(resource="otel:jaeger", tags=["prod", "ml-team"])
        assert tracer.tags == ["prod", "ml-team"]

    def test_tracer_inherits_from_new_tracer(self):
        from hush.core.tracing import Tracer

        from hush.telemetry import OTELTracer

        tracer = OTELTracer(resource="otel:jaeger", tags=["test"])
        assert isinstance(tracer, Tracer)
        assert tracer.tags == ["test"]

    def test_tracer_requires_config_or_resource(self):
        from hush.telemetry import OTELTracer

        with pytest.raises(ValueError, match="Must provide either"):
            OTELTracer()

    def test_tracer_rejects_both_config_and_resource(self):
        from hush.telemetry import OTELConfig, OTELTracer

        config = OTELConfig.jaeger()
        with pytest.raises(ValueError, match="Cannot provide both"):
            OTELTracer(config=config, resource="otel:jaeger")


# ============================================================================
# Helper Method Tests
# ============================================================================


class TestOTELTracerHelpers:
    """Test OTELTracer helper methods."""

    def test_datetime_to_ns_with_datetime(self):
        from hush.telemetry.tracers.otel import OTELTracer

        dt = datetime(2024, 1, 15, 10, 30, 0, tzinfo=timezone.utc)
        ns = OTELTracer._datetime_to_ns(dt)
        assert ns is not None
        assert isinstance(ns, int)
        assert ns == int(dt.timestamp() * 1_000_000_000)

    def test_datetime_to_ns_with_iso_string(self):
        from hush.telemetry.tracers.otel import OTELTracer

        iso_str = "2024-01-15T10:30:00+00:00"
        ns = OTELTracer._datetime_to_ns(iso_str)
        assert ns is not None
        assert isinstance(ns, int)

    def test_datetime_to_ns_with_z_suffix(self):
        from hush.telemetry.tracers.otel import OTELTracer

        iso_str = "2024-01-15T10:30:00Z"
        ns = OTELTracer._datetime_to_ns(iso_str)
        assert ns is not None
        assert isinstance(ns, int)

    def test_datetime_to_ns_with_none(self):
        from hush.telemetry.tracers.otel import OTELTracer

        assert OTELTracer._datetime_to_ns(None) is None

    def test_get_short_name(self):
        from hush.telemetry.tracers.otel import OTELTracer

        assert OTELTracer._get_short_name("workflow.node.child") == "child"
        assert OTELTracer._get_short_name("simple") == "simple"
        assert OTELTracer._get_short_name("") == ""

    def test_get_short_name_with_none(self):
        from hush.telemetry.tracers.otel import OTELTracer

        assert OTELTracer._get_short_name(None) is None


# ============================================================================
# Flush Method Tests (with mocks)
# ============================================================================


class TestOTELTracerFlush:
    """Test OTELTracer.flush() method with mocked backend."""

    def test_flush_with_resource(self, sample_trace_data):
        """Test flush with resource creates spans correctly."""
        from hush.telemetry import OTELTracer

        tracer = OTELTracer(resource="otel:jaeger")

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
                tracer.flush(sample_trace_data)

        mock_hub.otel.assert_called_once_with("otel:jaeger")
        assert mock_tracer.start_span.called
        mock_client.flush.assert_called_once()

    def test_flush_with_direct_config(self):
        """Test flush with direct config creates client correctly."""
        from hush.telemetry import OTELConfig, OTELTracer

        config = OTELConfig(
            endpoint="http://localhost:4317",
            protocol="grpc",
            service_name="test-service",
            insecure=True,
        )
        tracer = OTELTracer(config=config)

        trace_data = {
            "workflow_name": "test-workflow",
            "request_id": str(uuid.uuid4()),
            "user_id": "test-user",
            "session_id": "test-session",
            "tags": ["otel-test"],
            "nodes": [
                {
                    "trace_key": "root",
                    "parent_trace_key": None,
                    "op_name": "root",
                    "display_name": "test-workflow",
                    "node_type": "trace",
                    "kind": "graph",
                    "inputs": {"test": True},
                    "outputs": {"success": True},
                    "start_time": "2024-01-15T10:00:00Z",
                    "end_time": "2024-01-15T10:00:01Z",
                    "duration_ms": 1000.0,
                    "metadata": {},
                },
            ],
        }

        mock_tracer = MagicMock()
        mock_span = MagicMock()
        mock_tracer.start_span.return_value = mock_span

        with patch("hush.telemetry.backends.otel.OTELClient") as MockClient:
            mock_client = MagicMock()
            mock_client.tracer = mock_tracer
            MockClient.return_value = mock_client

            with patch("opentelemetry.trace.set_span_in_context") as mock_set_ctx:
                mock_set_ctx.return_value = MagicMock()
                tracer.flush(trace_data)

        MockClient.assert_called_once()
        mock_client.flush.assert_called_once()

    def test_flush_creates_correct_attributes(self, sample_trace_data):
        """Test flush creates correct span attributes."""
        from hush.telemetry import OTELTracer

        tracer = OTELTracer(resource="otel:jaeger")

        captured_attributes = {}

        mock_tracer = MagicMock()

        def capture_span(*args, **kwargs):
            if "attributes" in kwargs:
                captured_attributes.update(kwargs["attributes"])
            return MagicMock()

        mock_tracer.start_span.side_effect = capture_span

        mock_client = MagicMock()
        mock_client.tracer = mock_tracer

        mock_hub = MagicMock()
        mock_hub.otel.return_value = mock_client

        with patch("hush.core.registry.get_hub", return_value=mock_hub):
            with patch("opentelemetry.trace.set_span_in_context") as mock_set_ctx:
                mock_set_ctx.return_value = MagicMock()
                tracer.flush(sample_trace_data)

        assert "workflow.name" in captured_attributes
        assert captured_attributes["workflow.name"] == "test-workflow"

    def test_flush_with_context_aware_nodes(self):
        """Test flush correctly handles context-aware nodes (from iteration)."""
        from hush.telemetry import OTELTracer

        tracer = OTELTracer(resource="otel:jaeger")

        trace_data = {
            "workflow_name": "iteration-workflow",
            "request_id": str(uuid.uuid4()),
            "user_id": None,
            "session_id": None,
            "tags": [],
            "nodes": [
                {
                    "trace_key": "root",
                    "parent_trace_key": None,
                    "op_name": "root",
                    "display_name": "iteration-workflow",
                    "node_type": "trace",
                    "kind": "graph",
                    "inputs": {},
                    "outputs": {},
                },
                {
                    "trace_key": "root.loop",
                    "parent_trace_key": "root",
                    "op_name": "root.loop",
                    "display_name": "loop",
                    "node_type": "span",
                    "kind": "generator",
                    "inputs": {},
                    "outputs": {},
                    "metadata": {"yield_count": 2},
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
                },
                {
                    "trace_key": "root.loop.process:main.s0",
                    "parent_trace_key": "$ctx:root:main.s0",
                    "op_name": "root.loop.process",
                    "display_name": "process",
                    "node_type": "span",
                    "kind": "stream_item",
                    "inputs": {"i": 0},
                    "outputs": {"r": 0},
                    "metadata": {"spawned_by": "root.loop"},
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
                },
                {
                    "trace_key": "root.loop.process:main.s1",
                    "parent_trace_key": "$ctx:root:main.s1",
                    "op_name": "root.loop.process",
                    "display_name": "process",
                    "node_type": "span",
                    "kind": "stream_item",
                    "inputs": {"i": 1},
                    "outputs": {"r": 1},
                    "metadata": {"spawned_by": "root.loop"},
                },
            ],
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
                tracer.flush(trace_data)

        # root + loop + 2 stream_contexts + 2 process = 6 spans
        assert mock_tracer.start_span.call_count == 6


# ============================================================================
# Integration with Langfuse via OTEL
# ============================================================================


class TestOTELToLangfuse:
    """Test OTEL tracer sending to Langfuse OTEL endpoint."""

    def test_create_langfuse_otel_config(self):
        import base64

        from hush.telemetry import OTELConfig

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
