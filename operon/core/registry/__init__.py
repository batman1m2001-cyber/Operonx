"""Unified registry for operon packages.

Provides a centralized resource management system. Access the singleton
via ``ResourceHub.instance()``; it is installed automatically when you
construct an ``Operon`` engine.

Basic usage:
    from operon.core.registry import ResourceHub

    hub = ResourceHub.instance()
    llm = hub.get("llm:gpt-4o")

Extension from other packages:
    from operon.core.registry import REGISTRY
    from my_package.configs import MyConfig

    REGISTRY.register(MyConfig, lambda c: MyBackend(c))

Config class requirements:
    class MyConfig(YamlModel):
        _category: ClassVar[str] = "custom"    # Category (matches key prefix)
        ...
"""

from .config_registry import (
    REGISTRY,
    ConfigEntry,
    ConfigRegistry,
)
from .errors import (
    BOOTSTRAP_ENV_PATHS,
    EnvVarUnsetError,
    ResourceHubWarning,
)
from .resource_hub import (
    CacheEntry,
    ResourceHub,
)
from .shortcuts import (
    HealthCheckResult,
)
from .storage import (
    ConfigStorage,
    JsonConfigStorage,
    YamlConfigStorage,
)

__all__ = [
    # Registry
    "ConfigRegistry",
    "ConfigEntry",
    "REGISTRY",
    # Hub
    "ResourceHub",
    "CacheEntry",
    "HealthCheckResult",
    # Errors and warnings
    "ResourceHubWarning",
    "EnvVarUnsetError",
    "BOOTSTRAP_ENV_PATHS",
    # Storage backends
    "ConfigStorage",
    "YamlConfigStorage",
    "JsonConfigStorage",
]
