"""08 Error Handling — Serve with Python backend (FastAPI + uvicorn).

Endpoints:
  POST /error-capture    — op fails, error captured in state
  POST /error-routing    — if_() routes success/error branches
  POST /retry-fallback   — retry with backoff + graceful degradation
  POST /llm-fallback     — LLM fallback chain (gpt-4o → gpt-4o-mini)

Chạy:
  cd examples && uv run python 08_error_handling/serve_python.py

Test:
  uv run python 08_error_handling/bench.py
"""

import os

from hush.serve import HushApp
from workflow import (
    build_error_capture,
    build_error_routing,
    build_llm_fallback,
    build_retry_fallback,
)

app = HushApp()
app.endpoint("/error-capture", graph=build_error_capture())
app.endpoint("/error-routing", graph=build_error_routing())
app.endpoint("/retry-fallback", graph=build_retry_fallback())
app.endpoint("/llm-fallback", graph=build_llm_fallback())
app.serve(port=int(os.environ.get("PORT", 8000)))
