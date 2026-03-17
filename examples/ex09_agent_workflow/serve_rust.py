"""09 Agent Workflow — Serve with Rust backend (Axum).

Endpoints:
  POST /agent   — {query} → {answer}

Requires example-ops binary to be built:
  cd examples/rust_ops && cargo build --release

Chạy:
  cd examples && uv run python ex09_agent_workflow/serve_rust.py

Test:
  uv run python ex09_agent_workflow/bench.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import os
from pathlib import Path

from hush.core import Hush

from ex09_agent_workflow.workflow import build_agent

root = Path(__file__).parent.parent.parent
engine = Hush(build_agent, env=str(root / ".env"), resources=str(root / "resources.yaml"))
engine.serve(
    path="/agent",
    port=int(os.environ.get("PORT", 8000)),
    backend="rust",
    binary="rust_ops/target/release/example-ops",
)
