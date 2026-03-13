"""04 LLM Advanced — Serve workflow with Python backend (FastAPI + uvicorn).

Serves the structured output (sentiment analysis) endpoint.

Chạy:
  cd examples && uv run python 04_llm_advanced/serve_python.py

Test:
  uv run python 04_llm_advanced/bench.py
"""

import os

from hush.core import Hush
from workflow import build_structured_output

engine = Hush(build_structured_output())
engine.serve(port=int(os.environ.get("PORT", 8000)))
