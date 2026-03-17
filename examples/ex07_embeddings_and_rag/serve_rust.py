"""07 Embeddings & RAG — Serve RAG pipeline with Rust backend (Axum).

Endpoints:
  POST /embed        — embed texts → vectors
  POST /rag          — query + documents + doc_vectors → answer + context_docs

Requires example-ops binary to be built:
  cd examples/rust_ops && cargo build --release

Chạy:
  cd examples && uv run python ex07_embeddings_and_rag/serve_rust.py

Test:
  uv run python ex07_embeddings_and_rag/bench.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent.parent / ".env")

from hush.serve import HushApp

from ex07_embeddings_and_rag.workflow import build_basic_embedding, build_simple_rag

app = HushApp()
app.endpoint("/embed", graph=build_basic_embedding())
app.endpoint("/rag", graph=build_simple_rag())
app.serve(
    port=int(os.environ.get("PORT", 8000)),
    backend="rust",
    binary="rust_ops/target/release/example-ops",
)
