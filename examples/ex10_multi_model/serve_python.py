"""10 Multi-Model — Serve with Python backend (FastAPI + uvicorn).

Endpoints:
  POST /parallel   — parallel multi-model comparison
  POST /routing    — cost-optimized routing
  POST /balanced   — load balanced model selection
  POST /fallback   — fallback chain
  POST /ensemble   — ensemble with judge

Chạy:
  cd examples && uv run python ex10_multi_model/serve_python.py

Test:
  uv run python ex10_multi_model/bench.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import os
from pathlib import Path

from hush.serve import HushApp

from ex10_multi_model.workflow import (
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
app.serve(port=int(os.environ.get("PORT", 8000)))
