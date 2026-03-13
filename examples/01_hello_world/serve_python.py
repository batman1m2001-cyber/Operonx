"""01 Hello World — Serve workflow with Python backend (FastAPI + uvicorn).

Chạy:
  cd examples && uv run python 01_hello_world/serve_python.py

Test:
  uv run python 01_hello_world/bench.py
"""

import os

from hush.core import Hush
from workflow import build_hello

engine = Hush(build_hello())
engine.serve(port=int(os.environ.get("PORT", 8000)))
