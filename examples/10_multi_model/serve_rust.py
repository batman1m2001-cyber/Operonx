"""10 Multi-Model — Serve with Rust backend (Axum).

Endpoints:
  POST /parallel   — parallel multi-model comparison
  POST /routing    — cost-optimized routing
  POST /balanced   — load balanced model selection
  POST /fallback   — fallback chain
  POST /ensemble   — ensemble with judge

Requires rush-serve binary and rust_ops cdylib to be built:
  cd rust && cargo build --release -p rush-serve
  cd examples/rust_ops && cargo build --release

Chạy:
  cd examples && uv run python 10_multi_model/serve_rust.py

Test:
  uv run python 10_multi_model/bench.py
"""

import os
from pathlib import Path

from hush.serve import HushApp
from workflow import (
    build_cost_routing,
    build_ensemble,
    build_fallback,
    build_load_balanced,
    build_parallel_comparison,
)

root = Path(__file__).parent.parent.parent
app = HushApp(env=str(root / ".env"), resources=str(root / "resources.yaml"))
app.endpoint("/parallel", graph=build_parallel_comparison())
app.endpoint("/routing", graph=build_cost_routing())
app.endpoint("/balanced", graph=build_load_balanced())
app.endpoint("/fallback", graph=build_fallback())
app.endpoint("/ensemble", graph=build_ensemble())
app.serve(port=int(os.environ.get("PORT", 8000)), backend="rust", rust_ops="rust_ops")
