"""12 RAG Advanced — Serve with Python backend (FastAPI + uvicorn).

Endpoints:
  POST /keyword-rrf  — keyword search + RRF merge (no API key needed)

Chạy:
  cd examples && uv run python ex12_rag_advanced/serve_python.py

Test:
  uv run python ex12_rag_advanced/bench.py
"""

import os

from hush.serve import HushApp
from workflow import build_keyword_rrf

app = HushApp()
app.endpoint("/keyword-rrf", graph=build_keyword_rrf())
app.serve(port=int(os.environ.get("PORT", 8000)))
