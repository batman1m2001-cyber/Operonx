"""The HTTP client every SDK-backed provider hands its SDK.

Lives here, not in ``llms/base.py``, so a backend outside ``llms`` (the
OpenAI-protocol embeddings) can share it without importing the LLM base
class and the event-loop policy that module sets on import.
"""

from __future__ import annotations

from typing import Optional

import httpx


def create_http_client(
    verify: bool = True,
    proxy: Optional[str] = None,
    read_timeout: float = 120.0,
    max_connections: int = 100,
) -> httpx.AsyncClient:
    """Shared HTTP client factory for SDK-backed providers."""
    return httpx.AsyncClient(
        proxy=proxy,
        verify=verify,
        timeout=httpx.Timeout(connect=10.0, read=read_timeout, write=10.0, pool=5.0),
        limits=httpx.Limits(max_connections=max_connections, max_keepalive_connections=10),
    )
