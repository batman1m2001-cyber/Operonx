"""01 Hello World — Serve workflow with Rust backend (Axum).

Requires rush-serve binary and rust_ops cdylib to be built:
  cd rust && cargo build --release -p rush-serve
  cd examples/rust_ops && cargo build --release

Chạy:
  cd examples && uv run python 01_hello_world/serve_rust.py

Test:
  uv run python 01_hello_world/bench.py
"""

import os

from hush.core import Hush
from workflow import build_hello

engine = Hush(build_hello())
engine.serve(
    port=int(os.environ.get("PORT", 8000)),
    backend="rust",
    rust_ops="rust_ops",
)
