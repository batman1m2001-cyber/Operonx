"""03 LLM Chat — Serve workflow with Python backend (FastAPI + uvicorn).

Chạy:
  cd examples && uv run python ex03_llm_chat/serve_python.py

Test:
  uv run python ex03_llm_chat/bench.py
"""

import os

from hush.core import Hush
from workflow import build_basic_chat

engine = Hush(build_basic_chat())
engine.serve(port=int(os.environ.get("PORT", 8000)))
