"""05 Loops & Branches — Serve with Rust backend (Axum).

Endpoints:
  POST /for-loop      — generator yield sequential iteration
  POST /map-op        — generator yield parallel map
  POST /while-loop    — generator while conditional loop
  POST /branch        — if_() conditional routing

Requires example-ops binary to be built:
  cd examples/rust_ops && cargo build --release

Chạy:
  cd examples && uv run python ex05_loops_and_branches/serve_rust.py

Test:
  uv run python ex05_loops_and_branches/bench.py
"""

import os

from hush.serve import HushApp
from workflow import build_branch, build_for_loop, build_map_op, build_while_loop

app = HushApp()
app.endpoint("/for-loop", graph=build_for_loop())
app.endpoint("/map-op", graph=build_map_op())
app.endpoint("/while-loop", graph=build_while_loop())
app.endpoint("/branch", graph=build_branch())
app.serve(
    port=int(os.environ.get("PORT", 8000)),
    backend="rust",
    binary="rust_ops/target/release/example-ops",
)
