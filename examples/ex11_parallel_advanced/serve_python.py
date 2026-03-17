"""11 Parallel Advanced — Serve with Python backend (FastAPI + uvicorn).

Endpoints:
  POST /fan-out          — text → parallel analysis → merged result
  POST /iteration        — items → generator → squared results
  POST /partial-failure  — items → safe process → filter ok/error

Chạy:
  cd examples && uv run python ex11_parallel_advanced/serve_python.py

Test:
  uv run python ex11_parallel_advanced/bench.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import os

from hush.serve import HushApp

from ex11_parallel_advanced.workflow import build_fan_out, build_iteration, build_partial_failure

app = HushApp()
app.endpoint("/fan-out", graph=build_fan_out())
app.endpoint("/iteration", graph=build_iteration())
app.endpoint("/partial-failure", graph=build_partial_failure())
app.serve(port=int(os.environ.get("PORT", 8000)))
