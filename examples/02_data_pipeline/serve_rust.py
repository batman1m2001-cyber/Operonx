"""02 Data Pipeline — Serve all pipelines with Rust backend (Axum).

Endpoints:
  POST /data-pipeline   — fetch → transform → aggregate
  POST /text-pipeline    — clean → count_words → summarize

Requires rush-serve binary and rust_ops cdylib to be built:
  cd rust && cargo build --release -p rush-serve
  cd examples/rust_ops && cargo build --release

Chạy:
  cd examples && uv run python 02_data_pipeline/serve_rust.py

Test:
  uv run python 02_data_pipeline/client.py
"""

import os

from hush.serve import HushApp

from workflow import build_data_pipeline, build_text_pipeline

app = HushApp()
app.endpoint("/data-pipeline", graph=build_data_pipeline())
app.endpoint("/text-pipeline", graph=build_text_pipeline())
app.serve(port=int(os.environ.get("PORT", 8000)), backend="rust", rust_ops="rust_ops")
