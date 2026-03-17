"""01 Hello World — Serve workflow with Python backend (FastAPI + uvicorn).

Chạy:
  cd examples && uv run python ex01_hello_world/serve_python.py

Test:
  uv run python ex01_hello_world/bench.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import os

from hush.core import Hush

from ex01_hello_world.workflow import build_hello

engine = Hush(build_hello())
engine.serve(port=int(os.environ.get("PORT", 8000)))
