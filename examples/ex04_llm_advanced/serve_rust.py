"""04 LLM Advanced — Serve workflow with Rust backend (Axum).

Serves the structured output (sentiment analysis) endpoint.

Requires example-ops binary to be built:
  cd examples/rust_ops && cargo build --release

Chạy:
  cd examples && uv run python ex04_llm_advanced/serve_rust.py

Test:
  uv run python ex04_llm_advanced/bench.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import os

from hush.core import Hush

from ex04_llm_advanced.workflow import build_structured_output

engine = Hush(build_structured_output())
engine.serve(
    port=int(os.environ.get("PORT", 8000)),
    backend="rust",
    binary="rust_ops/target/release/example-ops",
)
