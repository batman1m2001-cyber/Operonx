"""03 LLM Chat — Serve workflow with Rust backend (Axum).

Requires rush-serve binary and rust_ops cdylib to be built:
  cd rust && cargo build --release -p rush-serve
  cd examples/rust_ops && cargo build --release

Chạy:
  cd examples && uv run python 03_llm_chat/serve_rust.py

Test:
  uv run python 03_llm_chat/bench.py
"""

import os

from hush.core import Hush
from workflow import build_basic_chat

engine = Hush(build_basic_chat())
engine.serve(port=int(os.environ.get("PORT", 8000)), backend="rust", rust_ops="rust_ops")
