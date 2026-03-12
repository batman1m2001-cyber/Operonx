"""06 Tracing / Langfuse — Serve with LangfuseTracer + Rust backend.

Requires rush-serve binary and rust_ops cdylib to be built:
  cd rust && cargo build --release -p rush-serve
  cd examples/rust_ops && cargo build --release

Chạy:
  cd examples && uv run python 06_tracing/langfuse/serve_rust.py

Test:
  uv run python 06_tracing/langfuse/client.py
"""

import os
import sys

from workflow import build_text_analyzer


def _make_tracer():
    from hush.telemetry import LangfuseTracer
    from hush.telemetry.backends.langfuse import LangfuseConfig

    public_key = os.environ.get("LANGFUSE_HUSH_PUBLIC_KEY")
    secret_key = os.environ.get("LANGFUSE_HUSH_SECRET_KEY")
    base_url = os.environ.get("LANGFUSE_HUSH_BASE_URL", "https://cloud.langfuse.com")

    if not public_key or not secret_key:
        import warnings

        warnings.warn("LANGFUSE_HUSH_* keys not set in .env — running without Langfuse tracing")
        return None

    return LangfuseTracer(
        config=LangfuseConfig(public_key=public_key, secret_key=secret_key, host=base_url),
        tags=["serve", "langfuse"],
    )


from hush.core import Hush

tracer = _make_tracer()
engine = Hush(build_text_analyzer(), **({} if tracer is None else {"tracer": tracer}))
engine.serve(port=int(os.environ.get("PORT", 8000)), backend="rust", rust_ops="rust_ops")
