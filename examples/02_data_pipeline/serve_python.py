"""02 Data Pipeline — Serve all pipelines with Python backend (FastAPI + uvicorn).

Endpoints:
  POST /data-pipeline   — fetch → transform → aggregate
  POST /text-pipeline    — clean → count_words → summarize

Chạy:
  cd examples && uv run python 02_data_pipeline/serve_python.py

Test:
  uv run python 02_data_pipeline/client.py
"""

import os

from hush.serve import HushApp

from workflow import build_data_pipeline, build_text_pipeline

app = HushApp()
app.endpoint("/data-pipeline", graph=build_data_pipeline())
app.endpoint("/text-pipeline", graph=build_text_pipeline())
app.serve(port=int(os.environ.get("PORT", 8000)))
