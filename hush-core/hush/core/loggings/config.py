"""Các model config cho logging."""

import os
from typing import Optional, List, Union, Dict, Any

from pydantic import BaseModel, Field, ConfigDict


class HandlerConfig(BaseModel):
    """Config cơ bản cho handler.

    Các package bên ngoài kế thừa class này để định nghĩa config handler riêng.
    Field 'type' được dùng như discriminator để tra cứu config class
    và factory tương ứng từ registry.

    Args:
        type: Định danh loại handler (ví dụ: "console", "file", "kafka")
        enabled: Bật handler này (mặc định: True)
        level: Log level cho handler này (mặc định: DEBUG)
        format_str: Format string tùy chỉnh

    Example:
        # Trong package bên ngoài (hush-loggings-kafka)
        class KafkaHandlerConfig(HandlerConfig):
            type: Literal["kafka"] = "kafka"
            bootstrap_servers: str
            topic: str
    """

    model_config = ConfigDict(extra="allow")

    type: str
    enabled: bool = True
    level: str = "DEBUG"
    format_str: Optional[str] = None


class LogConfig(BaseModel):
    """Config logging với hỗ trợ handler linh hoạt.

    Handler có thể được chỉ định dưới dạng:
    - Dict với key 'type' (sẽ được parse thành config class tương ứng)
    - Instance HandlerConfig (hoặc subclass)

    Args:
        name: Tên logger (mặc định: "hush")
        level: Log level gốc (DEBUG, INFO, WARNING, ERROR, CRITICAL)
        handlers: Danh sách config handler (dict hoặc instance HandlerConfig)
        propagate: Truyền lên logger cha (mặc định: False)

    Example:
        # Sử dụng cơ bản chỉ với console
        config = LogConfig()
        logger = setup_logger(config)

        # Với nhiều handler (dạng dict, tương thích YAML/JSON)
        config = LogConfig(
            name="my_app",
            level="DEBUG",
            handlers=[
                {"type": "console", "level": "INFO"},
                {"type": "file", "filepath": "logs/app.log", "level": "DEBUG"},
            ]
        )
        logger = setup_logger(config)

        # Với handler bên ngoài (sau khi import package)
        import hush.loggings.kafka  # Tự động đăng ký kafka handler

        config = LogConfig(
            handlers=[
                {"type": "console"},
                {"type": "kafka", "bootstrap_servers": "localhost:9092", "topic": "logs"},
            ]
        )
    """

    name: str = "hush"
    level: str = Field(default_factory=lambda: os.environ.get("LOG_LEVEL", "WARNING"))
    handlers: List[Union[HandlerConfig, Dict[str, Any]]] = Field(
        default_factory=lambda: [{"type": "console"}]
    )
    propagate: bool = False
