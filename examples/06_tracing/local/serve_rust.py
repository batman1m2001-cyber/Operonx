"""06 Tracing / Local — Serve with LocalTracer + Rust backend.

Requires example-ops binary to be built:
  cd examples/rust_ops && cargo build --release

Chạy:
  cd examples && uv run python 06_tracing/local/serve_rust.py

Test:
  uv run python 06_tracing/local/bench.py
"""

import os

from hush.core import Hush
from hush.core.tracing import LocalTracer
from workflow import build_text_analyzer

engine = Hush(build_text_analyzer(), tracer=LocalTracer(tags=["serve"]))
engine.serve(
    port=int(os.environ.get("PORT", 8000)),
    backend="rust",
    binary="rust_ops/target/release/example-ops",
)
