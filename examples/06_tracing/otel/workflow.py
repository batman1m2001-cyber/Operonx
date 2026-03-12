"""06 Tracing / OTEL — OTELTracer sends traces via OpenTelemetry.

Exports to any OTLP-compatible backend (Jaeger, Zipkin, Grafana Tempo, etc.).
Needs: opentelemetry packages installed (pip install hush-telemetry[otel])
"""

import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[3] / ".env")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from _ops import build_text_analyzer  # noqa: E402
