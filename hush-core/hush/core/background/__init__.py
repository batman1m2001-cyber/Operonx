"""Unified background process for non-blocking trace operations.

This package provides a background process that handles all tasks that should
not block the main workflow execution:
- Trace writing to SQLite
- Trace flushing to external services (Langfuse, etc.)

The background process is started lazily on first use. It tries
multiprocessing.Process first, and falls back to subprocess.Popen when
running inside daemon workers (Gunicorn/Uvicorn).

Example:
    ```python
    from hush.core.background import get_background

    bg = get_background()
    bg.write_trace(...)
    bg.mark_complete(...)
    ```
"""

from hush.core.background.db import DEFAULT_DB_PATH
from hush.core.background.process import (
    BackgroundProcess,
    BackgroundTask,
    TaskType,
    get_background,
    shutdown_background,
)

__all__ = [
    "DEFAULT_DB_PATH",
    "BackgroundProcess",
    "BackgroundTask",
    "TaskType",
    "get_background",
    "shutdown_background",
]
