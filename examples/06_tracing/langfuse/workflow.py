"""06 Tracing / Langfuse — LangfuseTracer sends traces to Langfuse cloud.

Needs: LANGFUSE_HUSH_PUBLIC_KEY, LANGFUSE_HUSH_SECRET_KEY, LANGFUSE_HUSH_BASE_URL in .env
"""

import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[3] / ".env")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
