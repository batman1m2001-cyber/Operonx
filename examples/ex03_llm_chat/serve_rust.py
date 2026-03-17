"""03 LLM Chat — Serve workflow with Rust backend (Axum).

Requires example-ops binary to be built:
  cd examples/rust_ops && cargo build --release

Chạy:
  cd examples && uv run python ex03_llm_chat/serve_rust.py

Test:
  uv run python ex03_llm_chat/bench.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import os

from hush.core import Hush

from ex03_llm_chat.workflow import build_basic_chat

engine = Hush(build_basic_chat())
engine.serve(
    port=int(os.environ.get("PORT", 8000)),
    backend="rust",
    binary="rust_ops/target/release/example-ops",
)
