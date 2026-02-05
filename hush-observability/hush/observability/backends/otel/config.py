"""OpenTelemetry configuration for ResourceHub."""

from typing import ClassVar, Dict, List, Literal, Optional

from hush.core.utils.yaml_model import YamlModel


class OTELConfig(YamlModel):
    """Configuration for OpenTelemetry observability backend.

    OpenTelemetry (OTEL) is a vendor-neutral observability framework
    that can export traces to any OTLP-compatible backend (Jaeger,
    Zipkin, Datadog, New Relic, Grafana Tempo, etc.).

    This config is registered to ResourceHub and used to create OTELClient.

    Attributes:
        endpoint: OTLP endpoint URL (e.g., "http://localhost:4317" for gRPC)
        protocol: Export protocol ("grpc" or "http")
        headers: Custom headers for authentication (e.g., {"Authorization": "Bearer xxx"})
        service_name: Service name for traces (appears in backend UI)
        service_version: Service version
        insecure: Whether to use insecure connection (for gRPC)
        timeout: Export timeout in seconds
        enabled: Whether tracing is enabled
        sample_rate: Sampling rate for traces (0.0 to 1.0)

    Example:
        ```yaml
        # resources.yaml - Jaeger
        otel:jaeger:

          endpoint: http://localhost:4317
          protocol: grpc
          service_name: my-workflow
          insecure: true

        # resources.yaml - Grafana Tempo
        otel:tempo:

          endpoint: https://tempo.example.com:4317
          protocol: grpc
          service_name: my-workflow
          headers:
            Authorization: Bearer ${TEMPO_API_KEY}

        # resources.yaml - HTTP/Protobuf
        otel:collector:

          endpoint: http://localhost:4318/v1/traces
          protocol: http
          service_name: my-workflow
        ```

        ```python
        from hush.core.registry import get_hub

        client = get_hub().otel("jaeger")
        ```
    """

    _category: ClassVar[str] = "otel"

    endpoint: str
    protocol: Literal["grpc", "http"] = "grpc"
    headers: Optional[Dict[str, str]] = None
    service_name: str = "hush-workflow"
    service_version: Optional[str] = None
    insecure: bool = False
    timeout: int = 30
    enabled: bool = True
    sample_rate: float = 1.0

    @classmethod
    def from_env(cls) -> "OTELConfig":
        """Create config from environment variables.

        Environment variables:
            - OTEL_EXPORTER_OTLP_ENDPOINT (required)
            - OTEL_EXPORTER_OTLP_PROTOCOL (optional, default: grpc)
            - OTEL_EXPORTER_OTLP_HEADERS (optional, comma-separated key=value pairs)
            - OTEL_SERVICE_NAME (optional, default: hush-workflow)
            - OTEL_SERVICE_VERSION (optional)
        """
        import os

        headers = None
        headers_str = os.environ.get("OTEL_EXPORTER_OTLP_HEADERS")
        if headers_str:
            headers = dict(pair.split("=") for pair in headers_str.split(","))

        return cls(
            endpoint=os.environ["OTEL_EXPORTER_OTLP_ENDPOINT"],
            protocol=os.environ.get("OTEL_EXPORTER_OTLP_PROTOCOL", "grpc"),
            headers=headers,
            service_name=os.environ.get("OTEL_SERVICE_NAME", "hush-workflow"),
            service_version=os.environ.get("OTEL_SERVICE_VERSION"),
        )

    @classmethod
    def jaeger(cls, host: str = "localhost", port: int = 4317) -> "OTELConfig":
        """Create config for local Jaeger instance.

        Args:
            host: Jaeger host
            port: Jaeger OTLP gRPC port (default: 4317)

        Returns:
            OTELConfig configured for Jaeger
        """
        return cls(
            endpoint=f"http://{host}:{port}",
            protocol="grpc",
            service_name="hush-workflow",
            insecure=True,
        )

    @classmethod
    def tempo(cls, endpoint: str, api_key: Optional[str] = None) -> "OTELConfig":
        """Create config for Grafana Tempo.

        Args:
            endpoint: Tempo OTLP endpoint
            api_key: Optional API key for authentication

        Returns:
            OTELConfig configured for Tempo
        """
        headers = None
        if api_key:
            headers = {"Authorization": f"Bearer {api_key}"}

        return cls(
            endpoint=endpoint,
            protocol="grpc",
            service_name="hush-workflow",
            headers=headers,
        )
