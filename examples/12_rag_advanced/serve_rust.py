"""12 RAG Advanced — Serve with Rust backend (Axum).

Endpoints:
  POST /keyword-rrf  — keyword search + RRF merge (no API key needed)

Requires hush-serve binary and rust_ops cdylib to be built:
  cd rust && cargo build --release -p hush-serve
  cd examples/rust_ops && cargo build --release

Chạy:
  cd examples && uv run python 12_rag_advanced/serve_rust.py

Test:
  uv run python 12_rag_advanced/bench.py
"""

import os

from hush.serve import HushApp
from workflow import build_keyword_rrf

app = HushApp()
app.endpoint("/keyword-rrf", graph=build_keyword_rrf())
app.serve(port=int(os.environ.get("PORT", 8000)), backend="rust", rust_ops="rust_ops")
