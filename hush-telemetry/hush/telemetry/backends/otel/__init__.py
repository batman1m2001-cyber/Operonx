"""OpenTelemetry backend for hush-telemetry.

OpenTelemetry (OTEL) is a vendor-neutral observability framework
that can export traces to any OTLP-compatible backend.

This module provides:
- OTELConfig: Configuration for ResourceHub
- OTELClient: Client for tracing

Supported backends:
- Jaeger
- Zipkin
- Datadog
- New Relic
- Grafana Tempo
- Any OTLP-compatible collector

References:
    - Documentation: https://opentelemetry.io/docs/languages/python/
    - GitHub: https://github.com/open-telemetry/opentelemetry-python
"""

from hush.telemetry.backends.otel.client import OTELClient
from hush.telemetry.backends.otel.config import OTELConfig

__all__ = [
    "OTELConfig",
    "OTELClient",
]
