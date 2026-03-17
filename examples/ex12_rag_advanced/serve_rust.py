"""12 RAG Advanced — Serve with Rust backend (Axum).

Endpoints:
  POST /keyword-rrf  — keyword search + RRF merge (no API key needed)

Requires example-ops binary to be built:
  cd examples/rust_ops && cargo build --release

Chạy:
  cd examples && uv run python ex12_rag_advanced/serve_rust.py

Test:
  uv run python ex12_rag_advanced/bench.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import os

from hush.serve import HushApp

from ex12_rag_advanced.workflow import build_keyword_rrf

app = HushApp()
app.endpoint("/keyword-rrf", graph=build_keyword_rrf())
app.serve(
    port=int(os.environ.get("PORT", 8000)),
    backend="rust",
    binary="rust_ops/target/release/example-ops",
)
