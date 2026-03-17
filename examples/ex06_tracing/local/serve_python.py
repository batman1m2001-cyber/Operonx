"""06 Tracing / Local — Serve with LocalTracer + Python backend.

Chạy:
  cd examples && uv run python ex06_tracing/local/serve_python.py

Test:
  uv run python ex06_tracing/local/bench.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import os

from hush.core import Hush
from hush.core.tracing import LocalTracer

from ex06_tracing.local.workflow import build_text_analyzer

engine = Hush(build_text_analyzer(), tracer=LocalTracer(tags=["serve"]))
engine.serve(port=int(os.environ.get("PORT", 8000)))
