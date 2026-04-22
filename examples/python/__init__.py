"""Python-side usage examples for Operon.

Each ``ex*`` subfolder is a self-contained demo. Run via its own
``run.sh`` / ``run.ps1`` or ``uv run python -m examples.python.<name>.demo``.
"""

import sys

# Vietnamese docstrings / outputs need UTF-8 stdout on Windows.
if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
