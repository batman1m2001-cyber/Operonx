"""Thiết lập logging tập trung cho Hush Core.

Package này cung cấp instance LOGGER đã được config sẵn và các tiện ích logging
cho toàn bộ package hush-core.

Modules:
    config: LogConfig và base class HandlerConfig
    theme: Theme và highlighter cho logging
    handlers: Console, file, và các handler mở rộng
    formatters: Các tiện ích format dữ liệu log

Example:
    from hush.core.loggings import LOGGER, LogConfig, setup_logger

    # Sử dụng logger mặc định (chỉ console)
    LOGGER.info("Hello world")

    # Tạo logger tùy chỉnh với nhiều handler
    config = LogConfig(
        name="my_app",
        level="DEBUG",
        handlers=[
            {"type": "console", "level": "INFO"},
            {"type": "file", "filepath": "logs/app.log", "level": "DEBUG"},
        ]
    )
    logger = setup_logger(config)

    # Mở rộng với handler tùy chỉnh (ví dụ: Kafka)
    # Trong package bên ngoài: hush-loggings-kafka
    import hush.loggings.kafka  # Tự động đăng ký khi import

    config = LogConfig(
        handlers=[
            {"type": "console"},
            {"type": "kafka", "bootstrap_servers": "localhost:9092", "topic": "logs"},
        ]
    )
"""

import logging
from typing import Callable, Dict, Optional, Tuple, Type, Any, Union

from .config import LogConfig, HandlerConfig
from .handlers import (
    ConsoleHandlerConfig,
    FileHandlerConfig,
    TimedFileHandlerConfig,
    create_console_handler,
    create_file_handler,
    create_timed_file_handler,
)
from .formatters import format_log_data, log_break, LOG_INDENT
from .theme import LOGGING_THEME


# Registry handler: type -> (ConfigClass, FactoryFunction)
_HANDLER_REGISTRY: Dict[str, Tuple[Type[HandlerConfig], Callable[[HandlerConfig], logging.Handler]]] = {}


def register_handler(
    handler_type: str,
    config_class: Type[HandlerConfig],
    factory: Callable[[HandlerConfig], logging.Handler],
) -> None:
    """Đăng ký một loại handler với config class và factory tương ứng.

    Cho phép mở rộng hệ thống logging với các handler tùy chỉnh.
    Các package bên ngoài gọi hàm này để đăng ký handler của họ.

    Args:
        handler_type: Định danh loại handler (ví dụ: "kafka", "syslog")
        config_class: Class config cho handler này (kế thừa HandlerConfig)
        factory: Hàm factory nhận config và trả về logging.Handler

    Example:
        # Trong package bên ngoài (hush-loggings-kafka)
        from hush.core.loggings import HandlerConfig, register_handler

        class KafkaHandlerConfig(HandlerConfig):
            type: Literal["kafka"] = "kafka"
            bootstrap_servers: str
            topic: str

        def create_kafka_handler(config: KafkaHandlerConfig) -> logging.Handler:
            ...

        # Tự động đăng ký khi import
        register_handler("kafka", KafkaHandlerConfig, create_kafka_handler)
    """
    _HANDLER_REGISTRY[handler_type] = (config_class, factory)


def _parse_handler_config(data: Union[HandlerConfig, Dict[str, Any]]) -> HandlerConfig:
    """Parse dict hoặc HandlerConfig thành config class tương ứng."""
    # Đã là instance của config class cụ thể
    if isinstance(data, HandlerConfig) and type(data) is not HandlerConfig:
        return data

    # Chuyển đổi HandlerConfig base thành dict để parse lại
    if isinstance(data, HandlerConfig):
        data = data.model_dump()

    handler_type = data.get("type")
    if handler_type and handler_type in _HANDLER_REGISTRY:
        config_class, _ = _HANDLER_REGISTRY[handler_type]
        return config_class(**data)

    # Fallback về HandlerConfig base (sẽ fail nếu được sử dụng)
    return HandlerConfig(**data)


def _create_handler_from_config(config: HandlerConfig) -> Optional[logging.Handler]:
    """Tạo handler từ config tương ứng."""
    if not config.enabled:
        return None

    handler_type = config.type

    if handler_type in _HANDLER_REGISTRY:
        _, factory = _HANDLER_REGISTRY[handler_type]
        return factory(config)

    raise ValueError(f"Loại handler không xác định: {handler_type}. Bạn có quên import package handler không?")


def add_handler(logger: logging.Logger, handler: logging.Handler) -> logging.Logger:
    """Thêm handler vào logger.

    Args:
        logger: Logger cần thêm handler
        handler: Handler cần thêm

    Returns:
        Logger đã được thêm handler
    """
    logger.addHandler(handler)
    return logger


def remove_handlers(logger: logging.Logger) -> logging.Logger:
    """Xóa tất cả handler khỏi logger.

    Args:
        logger: Logger cần xóa handler

    Returns:
        Logger đã được xóa tất cả handler
    """
    for handler in logger.handlers[:]:
        logger.removeHandler(handler)
    return logger


def setup_logger(config: Optional[LogConfig] = None) -> logging.Logger:
    """Thiết lập logger từ config.

    Args:
        config: Config cho log (mặc định: LogConfig())

    Returns:
        Instance Logger đã được config
    """
    if config is None:
        config = LogConfig()

    logger = logging.getLogger(config.name)
    logger.setLevel(getattr(logging, config.level.upper()))
    logger.propagate = config.propagate

    # Tránh trùng lặp handler
    if logger.handlers:
        return logger

    # Tạo và thêm handler từ config
    for handler_data in config.handlers:
        handler_config = _parse_handler_config(handler_data)
        handler = _create_handler_from_config(handler_config)
        if handler:
            add_handler(logger, handler)

    return logger


# Đăng ký các handler có sẵn
register_handler("console", ConsoleHandlerConfig, create_console_handler)
register_handler("file", FileHandlerConfig, create_file_handler)
register_handler("timed_file", TimedFileHandlerConfig, create_timed_file_handler)


# Instance logger mặc định
LOGGER = setup_logger(LogConfig(name="hush.core"))


__all__ = [
    # Export chính
    "LOGGER",
    "LogConfig",
    "setup_logger",
    # Base config để mở rộng
    "HandlerConfig",
    # Config cho các handler có sẵn
    "ConsoleHandlerConfig",
    "FileHandlerConfig",
    "TimedFileHandlerConfig",
    # Tiện ích
    "add_handler",
    "remove_handlers",
    "register_handler",
    "format_log_data",
    "log_break",
    "LOG_INDENT",
    # Theme
    "LOGGING_THEME",
]
