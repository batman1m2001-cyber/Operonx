"""Langfuse backend for hush-ops.

This module provides:
- LangfuseConfig: Configuration for ResourceHub
- LangfuseClient: Client for tracing and prompt management
"""

from hush.ops.backends.langfuse.client import LangfuseClient
from hush.ops.backends.langfuse.config import LangfuseConfig

__all__ = [
    "LangfuseConfig",
    "LangfuseClient",
]
