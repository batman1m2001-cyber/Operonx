"""Shortcut utilities for ResourceHub access."""

from .global_hub import get_hub, set_global_hub
from .health import HealthCheckResult

__all__ = [
    "get_hub",
    "set_global_hub",
    "HealthCheckResult",
]
