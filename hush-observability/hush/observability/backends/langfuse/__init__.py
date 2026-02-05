"""Langfuse backend for hush observability.

This module provides:
- LangfuseConfig: Configuration for ResourceHub
- LangfuseClient: Client for tracing and prompt management
"""

from hush.observability.backends.langfuse.config import LangfuseConfig
from hush.observability.backends.langfuse.client import LangfuseClient

__all__ = [
    "LangfuseConfig",
    "LangfuseClient",
]
