"""06 Tracing / OTEL — OTELTracer sends traces via OpenTelemetry.

Exports to any OTLP-compatible backend (Jaeger, Zipkin, Grafana Tempo, etc.).
Needs: opentelemetry packages installed (pip install hush-telemetry[otel])
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
