"""06 Tracing / Local — Serve with LocalTracer + Python backend.

Chạy:
  cd examples && uv run python 06_tracing/local/serve_python.py

Test:
  uv run python 06_tracing/local/client.py
"""

import os

from hush.core import Hush
from hush.core.tracing import LocalTracer

from workflow import build_text_analyzer

engine = Hush(build_text_analyzer(), tracer=LocalTracer(tags=["serve"]))
engine.serve(port=int(os.environ.get("PORT", 8000)))
