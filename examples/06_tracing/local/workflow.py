"""06 Tracing / Local — LocalTracer writes traces to ~/.hush/traces/.

Zero setup, no API keys needed.
"""

import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[3] / ".env")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from _ops import build_text_analyzer  # noqa: E402
