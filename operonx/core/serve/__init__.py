"""The serve layer: transports, sessions, and the ops that bind them.

`operonx.toml` already declared what puts work into a graph. Nothing read
it. This package is the other half — the declaration becomes what boots.

The transport is an extension point, not a wrapper around one web
framework. See :mod:`operonx.core.serve.protocol`.
"""

from .memory import MemorySession, MemoryTransport
from .ops import egress, ingress
from .protocol import (
    SESSION_KEY,
    BoundedSession,
    RunRequest,
    Session,
    Transport,
    current_session,
)
from .registry import (
    load_object,
    register_transport,
    resolve_transport,
    transport_names,
)
from .runner import ServeRunner, serve_session

__all__ = [
    "BoundedSession",
    "MemorySession",
    "MemoryTransport",
    "RunRequest",
    "SESSION_KEY",
    "ServeRunner",
    "Session",
    "Transport",
    "current_session",
    "egress",
    "ingress",
    "load_object",
    "register_transport",
    "resolve_transport",
    "serve_session",
    "transport_names",
]

# The in-memory transport is registered here rather than by its own
# module, so that importing it has no side effect and a test can choose to
# register it under another name.
register_transport("memory", lambda spec: MemoryTransport(spec.max_inflight))
