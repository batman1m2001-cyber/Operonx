"""Unified registry for hush packages.

This module provides a centralized resource management system that can be
extended by other hush-* packages.

Basic usage:
    from hush.core.registry import get_hub

    hub = get_hub()
    llm = hub.llm("gpt-4")
    redis = hub.redis("default")

Extension from other packages:
    from hush.core.registry import REGISTRY
    from my_package.configs import MyConfig
    from my_package.factory import MyFactory

    # Register config class with factory in one call
    REGISTRY.register(MyConfig, MyFactory.create)

Config class requirements:
    class MyConfig(YamlModel):
        _category: ClassVar[str] = "custom"    # Category (matches key prefix)
        ...
"""

from .config_registry import (
    ConfigRegistry,
    ConfigEntry,
    REGISTRY,
)
from .resource_hub import (
    ResourceHub,
    CacheEntry,
)
from .shortcuts import (
    get_hub,
    set_global_hub,
    HealthCheckResult,
)
from .storage import (
    ConfigStorage,
    YamlConfigStorage,
    JsonConfigStorage,
)

__all__ = [
    # Registry
    "ConfigRegistry",
    "ConfigEntry",
    "REGISTRY",
    # Hub
    "ResourceHub",
    "CacheEntry",
    "get_hub",
    "set_global_hub",
    "HealthCheckResult",
    # Storage backends
    "ConfigStorage",
    "YamlConfigStorage",
    "JsonConfigStorage",
]
