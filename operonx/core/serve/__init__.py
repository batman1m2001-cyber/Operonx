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
    "build_app",
    "serve_manifest",
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

def __getattr__(name):
    """`build_app` / `serve_manifest` need the serve extra; import them lazily.

    Keeps `import operonx.core.serve` working — and the protocol, registry
    and in-memory transport usable — on an install with no web stack. A
    project bringing its own transport should not have to install FastAPI.
    """
    if name in ("build_app", "build_apps", "serve_manifest", "engine_for"):
        from . import app as _app
        return getattr(_app, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


# The built-ins register here rather than in their own module, so importing
# them has no side effect and a project can shadow a name deliberately.
def _register_builtins() -> None:
    def _http(spec):
        from .asgi import HttpTransport
        return HttpTransport(spec)

    def _websocket(spec):
        from .asgi import WebSocketTransport
        return WebSocketTransport(spec)

    register_transport("http", _http)
    register_transport("websocket", _websocket)


_register_builtins()

# The in-memory transport is registered here rather than by its own
# module, so that importing it has no side effect and a test can choose to
# register it under another name.
register_transport("memory", lambda spec: MemoryTransport(spec.max_inflight))
