"""09 Agent Workflow — Serve with Rust backend (Axum).

Endpoints:
  POST /agent   — {query} → {answer}

Requires hush-serve binary and rust_ops cdylib to be built:
  cd rust && cargo build --release -p hush-serve
  cd examples/rust_ops && cargo build --release

Chạy:
  cd examples && uv run python 09_agent_workflow/serve_rust.py

Test:
  uv run python 09_agent_workflow/bench.py
"""

import os
from pathlib import Path

from hush.core import Hush
from workflow import build_agent

root = Path(__file__).parent.parent.parent
engine = Hush(build_agent, env=str(root / ".env"), resources=str(root / "resources.yaml"))
engine.serve(
    path="/agent", port=int(os.environ.get("PORT", 8000)), backend="rust", rust_ops="rust_ops"
)
