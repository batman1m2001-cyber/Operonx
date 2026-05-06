"""Tests for the legacy LangfuseTracer constructor surface + LangfuseClient.

Post-T2.12, ``LangfuseTracer`` is a thin pipeline-shaped shim — its
constructor builds a ``TracePipeline`` underneath, but the public attribute
surface (``.tags``, ``.resource``, ``_get_client()``, ``to_config_dict()``,
``__repr__``) is preserved so existing user code keeps working.

The actual trace-batch building and HTTP I/O lives in:
  - ``LangfuseTreeExporter`` (unit tests in ``test_langfuse_tree_exporter.py``)
  - ``LangfuseClient`` (HTTP layer — tested below)
  - ``scripts/probe_langfuse_edupia_roundtrip.py`` (real round-trip)
"""

import os
import urllib.error
from unittest.mock import patch

import pytest

LANGFUSE_CONFIG = {
    "public_key": os.environ.get("LANGFUSE_PUBLIC_KEY", ""),
    "secret_key": os.environ.get("LANGFUSE_SECRET_KEY", ""),
    "host": os.environ.get("LANGFUSE_HOST", "https://cloud.langfuse.com"),
}


# =============================================================================
# LangfuseConfig + LangfuseClient (HTTP layer — unchanged by the redesign)
# =============================================================================


def test_langfuse_config_creation():
    from operonx.telemetry import LangfuseConfig

    config = LangfuseConfig(**LANGFUSE_CONFIG)
    assert config.public_key == LANGFUSE_CONFIG["public_key"]
    assert config.secret_key == LANGFUSE_CONFIG["secret_key"]
    assert config.host == LANGFUSE_CONFIG["host"]


def test_langfuse_client_creation():
    from operonx.telemetry import LangfuseClient, LangfuseConfig

    config = LangfuseConfig(**LANGFUSE_CONFIG)
    client = LangfuseClient(config)
    assert client.config == config
    assert repr(client) == f"<LangfuseClient host={config.host}>"


def test_langfuse_client_has_ingest_method():
    from operonx.telemetry import LangfuseClient, LangfuseConfig

    config = LangfuseConfig(**LANGFUSE_CONFIG)
    client = LangfuseClient(config)
    assert hasattr(client, "ingest")
    assert hasattr(client, "trace_url")
    assert client.trace_url("test-id") == f"{config.host}/trace/test-id"


def test_langfuse_client_auth_header():
    import base64

    from operonx.telemetry import LangfuseClient, LangfuseConfig

    config = LangfuseConfig(public_key="pk-test", secret_key="sk-test", host="https://example.com")
    client = LangfuseClient(config)
    expected = base64.b64encode(b"pk-test:sk-test").decode()
    assert client._auth == expected


def test_langfuse_client_ingest_http_error():
    from operonx.telemetry import LangfuseClient, LangfuseConfig

    config = LangfuseConfig(public_key="pk-test", secret_key="sk-test", host="https://example.com")
    client = LangfuseClient(config)
    with patch("urllib.request.urlopen") as mock_urlopen:
        mock_urlopen.side_effect = urllib.error.HTTPError(
            url="https://example.com",
            code=500,
            msg="Server Error",
            hdrs=None,
            fp=None,
        )
        with pytest.raises(urllib.error.HTTPError):
            client.ingest([{"id": "test", "type": "trace-create", "body": {}}])


def test_langfuse_client_auth_check_failure():
    from operonx.telemetry import LangfuseClient, LangfuseConfig

    config = LangfuseConfig(public_key="pk-bad", secret_key="sk-bad", host="https://example.com")
    client = LangfuseClient(config)
    with patch("urllib.request.urlopen") as mock_urlopen:
        mock_urlopen.side_effect = urllib.error.HTTPError(
            url="https://example.com",
            code=401,
            msg="Unauthorized",
            hdrs=None,
            fp=None,
        )
        assert client.auth_check() is False


# =============================================================================
# LangfuseTracer — constructor + legacy attribute surface
# =============================================================================


def test_langfuse_tracer_creation():
    """Constructor accepts a resource key + exposes ``.resource``."""
    from operonx.telemetry import LangfuseTracer

    tracer = LangfuseTracer(resource="langfuse:default")
    assert tracer.resource == "langfuse:default"
    assert repr(tracer) == "<LangfuseTracer resource=langfuse:default>"


def test_langfuse_tracer_is_a_trace_pipeline():
    """Post-T2.12: LangfuseTracer IS a TracePipeline so the engine routes
    through the new event-stream path automatically."""
    from operonx.core.tracing.pipeline import TracePipeline
    from operonx.telemetry import LangfuseTracer

    tracer = LangfuseTracer(resource="langfuse:default", tags=["test"])
    assert isinstance(tracer, TracePipeline)
    assert tracer.tags == ["test"]


def test_langfuse_tracer_requires_config_or_resource():
    from operonx.telemetry import LangfuseTracer

    with pytest.raises(ValueError, match="Must provide either"):
        LangfuseTracer()


def test_langfuse_tracer_rejects_both_config_and_resource():
    from operonx.telemetry import LangfuseConfig, LangfuseTracer

    config = LangfuseConfig(**LANGFUSE_CONFIG)
    with pytest.raises(ValueError, match="Cannot provide both"):
        LangfuseTracer(config=config, resource="langfuse:default")


def test_langfuse_tracer_has_exporter_in_pipeline():
    """The shim wires up a ``LangfuseTreeExporter`` automatically."""
    from operonx.telemetry import LangfuseConfig, LangfuseTracer
    from operonx.telemetry.exporters import LangfuseTreeExporter

    config = LangfuseConfig(**LANGFUSE_CONFIG)
    tracer = LangfuseTracer(config=config, tags=["t1"])
    assert len(tracer.exporters) == 1
    assert isinstance(tracer.exporters[0], LangfuseTreeExporter)


def test_langfuse_tracer_with_trace_filter_builds_processors():
    """A passed ``trace_filter`` is converted to processors via the
    legacy adapter."""
    from operonx.core.tracing.trace_filter import TraceFilter
    from operonx.telemetry import LangfuseConfig, LangfuseTracer

    config = LangfuseConfig(**LANGFUSE_CONFIG)
    tf = TraceFilter(skip_empty=True, exclude_ops=["picker"], max_io_size=2000)
    tracer = LangfuseTracer(config=config, trace_filter=tf)
    types = [type(p).__name__ for p in tracer.processors]
    assert "DropOps" in types
    assert "DropEmpty" in types
    assert "TruncateIO" in types


def test_langfuse_tracer_to_config_dict_for_rust_backend():
    """``to_config_dict()`` returns the dict the Rust runtime needs."""
    from operonx.telemetry import LangfuseConfig, LangfuseTracer

    config = LangfuseConfig(public_key="pk", secret_key="sk", host="https://x")
    tracer = LangfuseTracer(config=config)
    d = tracer.to_config_dict()
    assert d == {"public_key": "pk", "secret_key": "sk", "host": "https://x"}


def test_langfuse_tracer_to_config_dict_none_when_resource_based():
    """Resource-based tracers return ``None`` from ``to_config_dict()`` —
    Rust resolves the resource itself."""
    from operonx.telemetry import LangfuseTracer

    tracer = LangfuseTracer(resource="langfuse:default")
    assert tracer.to_config_dict() is None


def test_langfuse_tracer_get_client_delegates_to_exporter():
    """Legacy ``_get_client()`` callers still work — delegates to the
    underlying exporter's HTTP client."""
    from operonx.telemetry import LangfuseConfig, LangfuseClient, LangfuseTracer

    config = LangfuseConfig(public_key="pk", secret_key="sk", host="https://x")
    tracer = LangfuseTracer(config=config)
    assert isinstance(tracer._get_client(), LangfuseClient)


def test_langfuse_tracer_repr_with_config():
    from operonx.telemetry import LangfuseConfig, LangfuseTracer

    config = LangfuseConfig(public_key="pk", secret_key="sk", host="https://example.com")
    tracer = LangfuseTracer(config=config)
    assert repr(tracer) == "<LangfuseTracer host=https://example.com>"
