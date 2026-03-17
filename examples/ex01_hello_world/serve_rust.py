"""01 Hello World — Serve workflow with Rust backend (Axum).

Requires example-ops binary to be built:
  cd examples/rust_ops && cargo build --release

Chạy:
  cd examples && uv run python ex01_hello_world/serve_rust.py

Test:
  uv run python ex01_hello_world/bench.py
"""

import os

from hush.core import Hush
from workflow import build_hello

engine = Hush(build_hello())
engine.serve(
    port=int(os.environ.get("PORT", 8000)),
    backend="rust",
    binary="rust_ops/target/release/example-ops",
)
