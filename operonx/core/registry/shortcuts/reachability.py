"""Does anything answer at a resource's address?

`health_check` used to mean "the client constructed", which for an HTTP or
gRPC client opens no socket — so a hub full of corporate endpoints reported
healthy on a laptop with the VPN down, and the real symptom arrived later
as every call timing out one at a time.

This resolves the address a config points at and opens a TCP connection to
it. Not a request: no auth, no model, no cost. Enough to separate "the
network cannot see this" from "the service said no", which are different
problems with different fixes.
"""

from __future__ import annotations

import socket
from typing import Any, Optional, Tuple
from urllib.parse import urlparse

#: Config fields that carry an address, in the order they are consulted.
_URL_FIELDS = ("base_url", "azure_endpoint", "endpoint", "url", "dsn", "server_url")

_DEFAULT_PORTS = {"https": 443, "http": 80, "grpc": 443, "postgresql": 5432, "postgres": 5432}


def endpoint_of(config: Any) -> Optional[Tuple[str, int]]:
    """`(host, port)` a config points at, or None when it is local.

    None is not a failure — an in-memory store or a filesystem-backed
    resource has no address, and probing one would invent a problem.
    """
    for field in _URL_FIELDS:
        raw = getattr(config, field, None)
        if not raw or not isinstance(raw, str):
            continue
        parsed = urlparse(raw if "://" in raw else f"//{raw}", scheme="https")
        host = parsed.hostname
        if not host:
            continue
        port = parsed.port or _DEFAULT_PORTS.get((parsed.scheme or "").lower(), 443)
        return host, int(port)

    host = getattr(config, "host", None)
    if isinstance(host, str) and host:
        port = getattr(config, "port", None)
        return host, int(port) if port else 443
    return None


def probe(host: str, port: int, timeout: float = 2.0) -> Optional[str]:
    """None when the address accepts a connection, else why it did not.

    A short timeout on purpose. The point is to fail in seconds at startup
    rather than minutes spread across a batch, so a slow answer is as
    useful to us as no answer.
    """
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return None
    except socket.timeout:
        return f"no answer from {host}:{port} within {timeout:g}s"
    except OSError as e:
        return f"cannot reach {host}:{port} ({e.__class__.__name__}: {e})"
