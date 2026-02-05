"""Các logging handler cho console."""

import os
import sys
import re
import copy
import codecs
import logging
from typing import Literal, Union

from pydantic import Field
from rich.logging import RichHandler
from rich.console import Console
from rich.highlighter import NullHighlighter

from ..config import HandlerConfig


def _parse_use_rich(value: str) -> Union[bool, str]:
    """Parse LOG_USE_RICH env var thành bool hoặc 'auto'."""
    v = value.strip().lower()
    if v in ("true", "1", "yes"):
        return True
    if v in ("false", "0", "no"):
        return False
    return "auto"
from ..theme import LOGGING_THEME

# Regex để strip Rich markup tags
_MARKUP_PATTERN = re.compile(r'\[/?[^\]]+\]')


def strip_markup(text: str) -> str:
    """Strip Rich markup tags từ text."""
    return _MARKUP_PATTERN.sub('', text)


class ConsoleHandlerConfig(HandlerConfig):
    """Config cho console handler.

    Args:
        enabled: Bật handler này (mặc định: True)
        level: Log level cho handler này
        format_str: Format string tùy chỉnh (bị bỏ qua nếu use_rich=True)
        use_rich: Sử dụng Rich handler cho màu sắc đẹp
            - True: luôn dùng Rich (màu sắc)
            - False: luôn dùng plain text (nhanh)
            - "auto": tự detect - Rich nếu TTY, plain nếu pipe/file
        show_name: Hiển thị tên logger trong output (mặc định: False)
    """

    type: Literal["console"] = "console"
    use_rich: Union[bool, Literal["auto"]] = Field(
        default_factory=lambda: _parse_use_rich(os.environ.get("LOG_USE_RICH", "auto"))
    )
    show_name: bool = False


class ColoredRichHandler(RichHandler):
    """RichHandler với màu sắc theo level và tùy chọn hiển thị tên logger."""

    def __init__(self, show_name: bool = False, **kwargs):
        super().__init__(**kwargs)
        self.show_name = show_name

    def emit(self, record: logging.LogRecord) -> None:
        """Emit log record với message được tô màu theo level."""
        # Map log level sang style (name_style, msg_style, strip_markup)
        level_styles = {
            "DEBUG": ("dim #8b949e", "dim #8b949e", True),         # Xám mờ, strip markup
            "INFO": ("#a8c7fa", "log.message", False),             # Xanh dương nhạt, giữ markup
            "WARNING": ("#f0a875", "#f0a875", True),               # Nâu cam, strip markup
            "ERROR": ("#ff7b72", "#ff7b72", True),                 # Đỏ sáng, strip markup
            "CRITICAL": ("bold reverse #b81c1c", "bold reverse #b81c1c", True),  # Đỏ đậm, strip markup
        }
        name_style, msg_style, strip_markup = level_styles.get(record.levelname, ("muted", "log.message", False))

        # Làm việc trên bản sao để không ảnh hưởng đến các handler khác
        record = copy.copy(record)

        # Strip markup cho các level không phải INFO
        # Phải format message trước rồi mới strip markup
        if strip_markup:
            # Format message với args trước
            if record.args:
                try:
                    formatted_msg = record.msg % record.args
                except (TypeError, ValueError):
                    formatted_msg = record.msg
                record.args = None  # Clear args vì đã format xong
            else:
                formatted_msg = record.msg
            # Strip markup sau khi format
            msg = _MARKUP_PATTERN.sub('', formatted_msg)
        else:
            msg = record.msg

        if self.show_name:
            record.msg = f"[{name_style}]\\[{record.name}][/{name_style}] [{msg_style}]{msg}[/{msg_style}]"
        else:
            record.msg = f"[{msg_style}]{msg}[/{msg_style}]"

        super().emit(record)


class PlainTextFormatter(logging.Formatter):
    """Formatter strip Rich markup tags cho plain text output."""

    def format(self, record: logging.LogRecord) -> str:
        # Format message trước
        result = super().format(record)
        # Strip markup tags
        return strip_markup(result)


def create_console_handler(config: ConsoleHandlerConfig) -> logging.Handler:
    """Tạo console handler từ config.

    Args:
        config: Config cho console handler

    Returns:
        Rich handler với màu sắc hoặc console handler UTF-8 cơ bản
    """
    # Resolve "auto" mode
    use_rich = config.use_rich
    if use_rich == "auto":
        # Dùng Rich nếu stdout là TTY (terminal thực)
        # Plain text nếu pipe sang file hoặc trong CI/CD
        use_rich = sys.stdout.isatty()

    if use_rich:
        console = Console(
            theme=LOGGING_THEME,
            # Không ép buộc terminal - để Rich tự phát hiện
            # Đảm bảo output plain text khi pipe sang file hoặc trong CI/CD
            highlight=False,
        )

        handler = ColoredRichHandler(
            show_name=config.show_name,
            console=console,
            show_time=True,
            show_level=True,
            show_path=False,
            enable_link_path=False,
            markup=True,
            rich_tracebacks=True,
            tracebacks_show_locals=False,
            tracebacks_extra_lines=1,
            tracebacks_theme="github-dark",
            omit_repeated_times=False,
            highlighter=NullHighlighter(),
        )
        handler.setLevel(getattr(logging, config.level.upper()))
        return handler
    else:
        # Plain text mode - strip markup tags
        format_str = config.format_str or '[%(asctime)s] %(levelname)-8s [%(name)s] %(message)s'
        handler = logging.StreamHandler(
            stream=codecs.getwriter('utf-8')(sys.stdout.buffer)
        )
        handler.setLevel(getattr(logging, config.level.upper()))
        handler.setFormatter(PlainTextFormatter(format_str, datefmt='%H:%M:%S'))
        return handler
