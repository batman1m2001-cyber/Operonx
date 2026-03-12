"""04 LLM Advanced — Serve workflow with Rust backend (Axum).

Serves the structured output (sentiment analysis) endpoint.

Requires rush-serve binary and rust_ops cdylib to be built:
  cd rust && cargo build --release -p rush-serve
  cd examples/rust_ops && cargo build --release

Chạy:
  cd examples && uv run python 04_llm_advanced/serve_rust.py

Test:
  uv run python 04_llm_advanced/client.py
"""

import os

from hush.core import Hush

from workflow import build_structured_output

engine = Hush(build_structured_output())
engine.serve(port=int(os.environ.get("PORT", 8000)), backend="rust", rust_ops="rust_ops")
