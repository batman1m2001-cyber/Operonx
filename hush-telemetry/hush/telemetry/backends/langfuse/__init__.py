"""Langfuse backend for hush-telemetry.

This module provides:
- LangfuseConfig: Configuration for ResourceHub
- LangfuseClient: Client for tracing and prompt management
"""

from hush.telemetry.backends.langfuse.client import LangfuseClient
from hush.telemetry.backends.langfuse.config import LangfuseConfig

__all__ = [
    "LangfuseConfig",
    "LangfuseClient",
]
