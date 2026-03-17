"""02 Data Pipeline — Serve all pipelines with Rust backend (Axum).

Endpoints:
  POST /data-pipeline   — fetch → transform → aggregate
  POST /text-pipeline    — clean → count_words → summarize

Requires example-ops binary to be built:
  cd examples/rust_ops && cargo build --release

Chạy:
  cd examples && uv run python ex02_data_pipeline/serve_rust.py

Test:
  uv run python ex02_data_pipeline/bench.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import os

from hush.serve import HushApp

from ex02_data_pipeline.workflow import build_data_pipeline, build_text_pipeline

app = HushApp()
app.endpoint("/data-pipeline", graph=build_data_pipeline())
app.endpoint("/text-pipeline", graph=build_text_pipeline())
app.serve(
    port=int(os.environ.get("PORT", 8000)),
    backend="rust",
    binary="rust_ops/target/release/example-ops",
)
