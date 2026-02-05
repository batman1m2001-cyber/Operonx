"""Package các logging handler.

Mỗi module handler định nghĩa:
- Config class (kế thừa HandlerConfig)
- Hàm factory (nhận config, trả về logging.Handler)

Các handler được tự động đăng ký khi package này được import.
"""

from .console import ConsoleHandlerConfig, ColoredRichHandler, create_console_handler
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
