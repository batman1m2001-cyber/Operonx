"""05 Loops & Branches — Serve with Python backend (FastAPI + uvicorn).

Endpoints:
  POST /for-loop      — generator yield sequential iteration
  POST /map-op        — generator yield parallel map
  POST /while-loop    — generator while conditional loop
  POST /branch        — if_() conditional routing

Chạy:
  cd examples && uv run python ex05_loops_and_branches/serve_python.py

Test:
  uv run python ex05_loops_and_branches/bench.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import os

from hush.serve import HushApp

from ex05_loops_and_branches.workflow import (
    build_branch,
    build_for_loop,
    build_map_op,
    build_while_loop,
)

app = HushApp()
app.endpoint("/for-loop", graph=build_for_loop())
app.endpoint("/map-op", graph=build_map_op())
app.endpoint("/while-loop", graph=build_while_loop())
app.endpoint("/branch", graph=build_branch())
app.serve(port=int(os.environ.get("PORT", 8000)))
