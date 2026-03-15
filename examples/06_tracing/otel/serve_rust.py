"""06 Tracing / OTEL — Serve with OTELTracer + Rust backend.

Requires example-ops binary to be built:
  cd examples/rust_ops && cargo build --release

Chạy:
  cd examples && uv run python 06_tracing/otel/serve_rust.py

Test:
  uv run python 06_tracing/otel/bench.py
"""

import os

from workflow import build_text_analyzer


def _make_tracer():
    try:
        from hush.telemetry import OTELTracer
        from hush.telemetry.backends.otel import OTELConfig
    except ImportError:
        import warnings

        warnings.warn("hush-telemetry[otel] not installed — running without OTEL tracing")
        return None

    endpoint = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4317")
    return OTELTracer(
        config=OTELConfig(
            endpoint=endpoint, protocol="grpc", service_name="hush-tutorial", insecure=True
        ),
        tags=["serve", "otel"],
    )


from hush.core import Hush

tracer = _make_tracer()
engine = Hush(build_text_analyzer(), **({} if tracer is None else {"tracer": tracer}))
engine.serve(
    port=int(os.environ.get("PORT", 8000)),
    backend="rust",
    binary="rust_ops/target/release/example-ops",
)
