"""02 Data Pipeline — Serve all pipelines with Python backend (FastAPI + uvicorn).

Endpoints:
  POST /data-pipeline   — fetch → transform → aggregate
  POST /text-pipeline    — clean → count_words → summarize

Chạy:
  cd examples && uv run python ex02_data_pipeline/serve_python.py

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
app.serve(port=int(os.environ.get("PORT", 8000)))
