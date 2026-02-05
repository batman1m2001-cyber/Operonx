"""Các logging handler ghi ra file."""

import logging
from pathlib import Path
from typing import Union, Literal
from logging.handlers import RotatingFileHandler, TimedRotatingFileHandler

from ..config import HandlerConfig


class FileHandlerConfig(HandlerConfig):
    """Config cho file handler (rotation theo kích thước).

    Args:
        enabled: Bật handler này (mặc định: True)
        level: Log level cho handler này
        format_str: Format string tùy chỉnh
        filepath: Đường dẫn đến file log
        max_bytes: Kích thước file tối đa trước khi rotation (mặc định: 10MB)
        backup_count: Số file backup giữ lại (mặc định: 5)
        encoding: Encoding của file (mặc định: utf-8)
    """

    type: Literal["file"] = "file"
    filepath: Union[str, Path]
    max_bytes: int = 10 * 1024 * 1024  # 10 MB
    backup_count: int = 5
    encoding: str = "utf-8"


class TimedFileHandlerConfig(HandlerConfig):
    """Config cho file handler rotation theo thời gian.

    Args:
        enabled: Bật handler này (mặc định: True)
        level: Log level cho handler này
        format_str: Format string tùy chỉnh
        filepath: Đường dẫn đến file log
        when: Loại khoảng thời gian rotation ('S', 'M', 'H', 'D', 'midnight', 'W0'-'W6')
        interval: Khoảng thời gian rotation (mặc định: 1)
        backup_count: Số file backup giữ lại (mặc định: 30)
        encoding: Encoding của file (mặc định: utf-8)
    """

    type: Literal["timed_file"] = "timed_file"
    filepath: Union[str, Path]
    when: str = "midnight"
    interval: int = 1
    backup_count: int = 30
    encoding: str = "utf-8"


def create_file_handler(config: FileHandlerConfig) -> logging.Handler:
    """Tạo file handler với rotation theo kích thước.

    Args:
        config: Config cho file handler

    Returns:
        Rotating file handler
    """
    filepath = Path(config.filepath)
    filepath.parent.mkdir(parents=True, exist_ok=True)

    format_str = config.format_str or "[%(asctime)s] %(levelname)-8s [%(name)s] %(message)s"

    handler = RotatingFileHandler(
        filepath,
        maxBytes=config.max_bytes,
        backupCount=config.backup_count,
        encoding=config.encoding,
    )
    handler.setLevel(getattr(logging, config.level.upper()))
    handler.setFormatter(logging.Formatter(format_str, datefmt="%Y-%m-%d %H:%M:%S"))
    return handler


def create_timed_file_handler(config: TimedFileHandlerConfig) -> logging.Handler:
    """Tạo file handler với rotation theo thời gian.

    Args:
        config: Config cho timed file handler

    Returns:
        Timed rotating file handler
    """
    filepath = Path(config.filepath)
    filepath.parent.mkdir(parents=True, exist_ok=True)

    format_str = config.format_str or "[%(asctime)s] %(levelname)-8s [%(name)s] %(message)s"

    handler = TimedRotatingFileHandler(
        filepath,
        when=config.when,
        interval=config.interval,
        backupCount=config.backup_count,
        encoding=config.encoding,
    )
    handler.setLevel(getattr(logging, config.level.upper()))
    handler.setFormatter(logging.Formatter(format_str, datefmt="%Y-%m-%d %H:%M:%S"))
    return handler
