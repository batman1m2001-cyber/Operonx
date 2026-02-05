"""OpenTelemetry client for ResourceHub integration."""

from typing import Any, Dict, Optional

from hush.observability.backends.otel.config import OTELConfig


class OTELClient:
    """OpenTelemetry client for tracing.

    This client wraps the OpenTelemetry SDK and is registered to ResourceHub.
    It provides a vendor-neutral way to export traces to any OTLP-compatible
    backend (Jaeger, Zipkin, Datadog, New Relic, Grafana Tempo, etc.).

    Example:
        ```python
        from hush.core.registry import get_hub

        # Get client from ResourceHub
        client = get_hub().otel("jaeger")

        # Create a trace using context manager
        with client.start_trace("my-workflow") as trace:
            with client.start_span("processing", trace_id=trace.trace_id) as span:
                span.set_attribute("input.size", 100)
                # ... do work
        ```

    References:
        - Documentation: https://opentelemetry.io/docs/languages/python/
        - GitHub: https://github.com/open-telemetry/opentelemetry-python
    """

    def __init__(self, config: OTELConfig):
        """Initialize OpenTelemetry client.

        Args:
            config: OTELConfig with endpoint and settings
        """
        self._config = config
        self._provider = None
        self._tracer = None
        self._initialized = False

    def _ensure_initialized(self):
        """Lazy initialization of OpenTelemetry provider and tracer."""
        if self._initialized:
            return

        try:
            from opentelemetry import trace
            from opentelemetry.sdk.resources import Resource
            from opentelemetry.sdk.trace import TracerProvider
            from opentelemetry.sdk.trace.export import BatchSpanProcessor
        except ImportError:
            raise ImportError(
                "opentelemetry packages are required for OTELClient. "
                "Install them with: pip install opentelemetry-api opentelemetry-sdk "
                "opentelemetry-exporter-otlp-proto-grpc opentelemetry-exporter-otlp-proto-http"
            )

        # Check if global provider is already set (by another OTELClient or user code)
        existing_provider = trace.get_tracer_provider()
        is_noop = type(existing_provider).__name__ == "ProxyTracerProvider"

        if is_noop:
            # No provider set yet, create and set one
            # Create resource with service info
            resource_attributes = {
                "service.name": self._config.service_name,
            }
            if self._config.service_version:
                resource_attributes["service.version"] = self._config.service_version

            resource = Resource.create(resource_attributes)

            # Create tracer provider
            self._provider = TracerProvider(resource=resource)

            # Create exporter based on protocol
            exporter = self._create_exporter()

            # Add batch processor
            processor = BatchSpanProcessor(exporter)
            self._provider.add_span_processor(processor)

            # Set as global provider
            trace.set_tracer_provider(self._provider)
        else:
            # Use existing provider but add our exporter
            self._provider = existing_provider
            if hasattr(self._provider, "add_span_processor"):
                exporter = self._create_exporter()
                processor = BatchSpanProcessor(exporter)
                self._provider.add_span_processor(processor)

        # Create tracer
        self._tracer = trace.get_tracer(
            self._config.service_name,
            self._config.service_version,
        )

        self._initialized = True

    def _create_exporter(self):
        """Create the appropriate OTLP exporter based on protocol."""
        if self._config.protocol == "grpc":
            from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import (
                OTLPSpanExporter,
            )

            kwargs: Dict[str, Any] = {
                "endpoint": self._config.endpoint,
                "insecure": self._config.insecure,
                "timeout": self._config.timeout,
            }
            if self._config.headers:
                kwargs["headers"] = list(self._config.headers.items())

            return OTLPSpanExporter(**kwargs)
        else:
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
                OTLPSpanExporter,
            )

            kwargs = {
                "endpoint": self._config.endpoint,
                "timeout": self._config.timeout,
            }
            if self._config.headers:
                kwargs["headers"] = self._config.headers

            return OTLPSpanExporter(**kwargs)

    @property
    def config(self) -> OTELConfig:
        """Get the configuration."""
        return self._config

    @property
    def tracer(self):
        """Get the OpenTelemetry tracer."""
        self._ensure_initialized()
        return self._tracer

    @property
    def provider(self):
        """Get the OpenTelemetry tracer provider."""
        self._ensure_initialized()
        return self._provider

    def start_span(
        self,
        name: str,
        attributes: Optional[Dict[str, Any]] = None,
        **kwargs,
    ):
        """Start a new span.

        Args:
            name: Span name
            attributes: Span attributes
            **kwargs: Additional arguments passed to start_as_current_span

        Returns:
            Context manager for the span
        """
        self._ensure_initialized()
        span = self._tracer.start_as_current_span(name, **kwargs)
        if attributes:
            current_span = self.get_current_span()
            if current_span:
                for key, value in attributes.items():
                    current_span.set_attribute(key, value)
        return span

    def get_current_span(self):
        """Get the current active span."""
        from opentelemetry import trace

        return trace.get_current_span()

    def flush(self, timeout_millis: int = 30000) -> bool:
        """Flush all pending spans.

        Args:
            timeout_millis: Timeout in milliseconds

        Returns:
            True if successful
        """
        if self._provider:
            return self._provider.force_flush(timeout_millis)
        return True

    def shutdown(self):
        """Shutdown the tracer provider."""
        if self._provider:
            self._provider.shutdown()

    def __repr__(self) -> str:
        """String representation."""
        return f"<OTELClient endpoint={self._config.endpoint} protocol={self._config.protocol}>"
