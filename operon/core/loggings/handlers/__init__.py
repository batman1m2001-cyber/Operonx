"""Logging handler package.

Each handler module defines:
- a config class (subclass of ``HandlerConfig``)
- a factory function (takes config, returns ``logging.Handler``)

Handlers auto-register to the parent package's registry on import.
"""

from .console import ColoredRichHandler, ConsoleHandlerConfig, create_console_handler
from .file import (
    FileHandlerConfig,
    TimedFileHandlerConfig,
    create_file_handler,
    create_timed_file_handler,
)

__all__ = [
    # Console
    "ConsoleHandlerConfig",
    "ColoredRichHandler",
    "create_console_handler",
    # File
    "FileHandlerConfig",
    "TimedFileHandlerConfig",
    "create_file_handler",
    "create_timed_file_handler",
]
